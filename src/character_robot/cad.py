from __future__ import annotations

import hashlib
import io
import importlib
import json
import re
import tempfile
import uuid
import zipfile
from copy import copy
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from pydantic import ValidationError

from .profiles import HardwareProfile, ProfileRegistry
from .schemas import CharacterRobotSpec


BUILD123D_VERSION = "0.11.1"
CAD_COMPILER_VERSION = "character-cad-v1"
_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
_EXPORT_ORDER = ("glb", "step", "stl", "3mf")
_SHOWCASE_PROFILE_MARGIN_MM = 4.0
_MAX_SCHEMA_AXIS_MM = 500.0


class CadCompileError(RuntimeError):
    """A typed, user-safe CAD boundary failure."""

    def __init__(
        self,
        code: str,
        safe_message: str,
        *,
        retryable: bool = False,
        details: Mapping[str, object] | None = None,
    ) -> None:
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable
        self.details = dict(details or {})
        super().__init__(safe_message)


@dataclass(frozen=True, slots=True)
class Bounds3D:
    minimum_mm: tuple[float, float, float]
    maximum_mm: tuple[float, float, float]

    @property
    def size_mm(self) -> tuple[float, float, float]:
        return tuple(self.maximum_mm[axis] - self.minimum_mm[axis] for axis in range(3))  # type: ignore[return-value]

    @property
    def center_mm(self) -> tuple[float, float, float]:
        return tuple(
            (self.minimum_mm[axis] + self.maximum_mm[axis]) / 2 for axis in range(3)
        )  # type: ignore[return-value]


@dataclass(frozen=True, slots=True)
class CadPartMetadata:
    name: str
    role: str
    bounds: Bounds3D
    volume_mm3: float
    printable: bool


@dataclass(frozen=True, slots=True)
class CadIssue:
    code: str
    severity: str
    path: str
    message: str
    suggestion: str | None = None


@dataclass(frozen=True, slots=True)
class CompiledArtifact:
    kind: str
    file_name: str
    media_type: str
    content: bytes
    sha256: str
    experimental: bool = True

    @property
    def byte_size(self) -> int:
        return len(self.content)


@dataclass(frozen=True, slots=True)
class CadCompileResult:
    compiler_version: str
    build123d_version: str
    profile_id: str
    geometry_sha256: str
    assembly_bounds: Bounds3D
    parts: tuple[CadPartMetadata, ...]
    artifacts: tuple[CompiledArtifact, ...]
    issues: tuple[CadIssue, ...]

    @property
    def dimensions_mm(self) -> tuple[float, float, float]:
        return self.assembly_bounds.size_mm


@dataclass(slots=True)
class _CompiledPart:
    name: str
    role: str
    shape: Any
    printable: bool


def _profile_shell_node(payload: Mapping[str, Any]) -> dict[str, Any]:
    nodes = payload["morphology"]["nodes"]
    candidates = [
        node
        for role in ("head_shell", "chassis_shell")
        for node in nodes
        if node["visible"] and node["role"] == role
    ]
    if not candidates:
        raise CadCompileError(
            "CAD_PROFILE_SHELL_MISSING",
            "The character needs a visible head or chassis shell for its digital profile.",
        )
    return next(
        (node for node in candidates if node["kind"] == "rounded_solid"),
        candidates[0],
    )


def _autofit_profile_shell(payload: Mapping[str, Any], profile: HardwareProfile) -> str:
    """Grow one eligible shell around the profile's planning AABB.

    The Showcase compiler only grows a rounded-solid shell.  It never shrinks a
    user-authored shell and does not claim wall, cavity, fastening, or physical fit.
    """

    shell = _profile_shell_node(payload)
    if shell["kind"] != "rounded_solid":
        return str(shell["node_id"])
    required = tuple(
        value + 2 * _SHOWCASE_PROFILE_MARGIN_MM
        for value in profile.digital_envelope.size_mm
    )
    current = _vec3(shell["size_mm"])
    reflowed = tuple(max(current[axis], required[axis]) for axis in range(3))
    if any(value > _MAX_SCHEMA_AXIS_MM for value in reflowed):
        raise CadCompileError(
            "CAD_PROFILE_ENVELOPE_UNSUPPORTED",
            "The digital hardware envelope exceeds the bounded Showcase shell size.",
            details={"required_size_mm": reflowed},
        )
    shell["size_mm"] = dict(zip("xyz", reflowed, strict=True))
    return str(shell["node_id"])


def _import_build123d() -> Any:
    return importlib.import_module("build123d")


def _load_build123d() -> Any:
    try:
        build123d = _import_build123d()
        installed_version = metadata.version("build123d")
    except (ImportError, metadata.PackageNotFoundError) as error:
        raise CadCompileError(
            "CAD_DEPENDENCY_UNAVAILABLE",
            f"build123d=={BUILD123D_VERSION} is required for CAD compilation.",
        ) from error
    if installed_version != BUILD123D_VERSION:
        raise CadCompileError(
            "CAD_DEPENDENCY_VERSION_MISMATCH",
            f"CAD compilation requires build123d=={BUILD123D_VERSION}.",
            details={"required_version": BUILD123D_VERSION},
        )
    return build123d


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError) as error:
        raise CadCompileError(
            "CAD_INPUT_INVALID",
            "The character design cannot be represented as canonical JSON.",
        ) from error


def _validated_spec_payload(
    spec: CharacterRobotSpec | Mapping[str, object],
) -> dict[str, Any]:
    try:
        model = (
            spec
            if isinstance(spec, CharacterRobotSpec)
            else CharacterRobotSpec.model_validate(spec)
        )
    except ValidationError as error:
        raise CadCompileError(
            "CAD_INPUT_INVALID",
            "The character design does not satisfy CharacterRobotSpec.",
        ) from error
    return cast(dict[str, Any], model.model_dump(mode="json"))


def _vec3(value: Mapping[str, object]) -> tuple[float, float, float]:
    return (float(value["x"]), float(value["y"]), float(value["z"]))


def _vec2(value: Mapping[str, object]) -> tuple[float, float]:
    return (float(value["x"]), float(value["y"]))


def _shape_bounds(shape: Any) -> Bounds3D:
    bounds = shape.bounding_box(optimal=True)
    return Bounds3D(
        minimum_mm=(float(bounds.min.X), float(bounds.min.Y), float(bounds.min.Z)),
        maximum_mm=(float(bounds.max.X), float(bounds.max.Y), float(bounds.max.Z)),
    )


def _rounded_box(
    build123d: Any, size: tuple[float, float, float], radius: float
) -> Any:
    shape = build123d.Box(
        *size,
        align=(
            build123d.Align.CENTER,
            build123d.Align.CENTER,
            build123d.Align.CENTER,
        ),
    )
    if radius <= 0:
        return shape
    safe_radius = min(radius, min(size) * 0.499)
    return build123d.fillet(shape.edges(), safe_radius)


def _anchor_point(bounds: Bounds3D, anchor: str) -> tuple[float, float, float]:
    minimum = bounds.minimum_mm
    maximum = bounds.maximum_mm
    center = bounds.center_mm
    anchors = {
        "center": center,
        "top": (center[0], center[1], maximum[2]),
        "neck_mount": (center[0], center[1], maximum[2]),
        "head_mount": (center[0], center[1], maximum[2]),
        "bottom": (center[0], center[1], minimum[2]),
        "front": (center[0], minimum[1], center[2]),
        "face": (center[0], minimum[1], center[2]),
        "back": (center[0], maximum[1], center[2]),
        "tail_mount": (center[0], maximum[1], center[2]),
        "left": (minimum[0], center[1], center[2]),
        "left_side": (minimum[0], center[1], center[2]),
        "right": (maximum[0], center[1], center[2]),
        "right_side": (maximum[0], center[1], center[2]),
    }
    try:
        return anchors[anchor]
    except KeyError:
        raise CadCompileError(
            "CAD_ANCHOR_UNSUPPORTED",
            f"Morphology anchor {anchor!r} is not supported by this family.",
        ) from None


def _apply_attachment(
    build123d: Any,
    shape: Any,
    attachment: Mapping[str, Any] | None,
    compiled: Mapping[str, Any],
) -> Any:
    if attachment is None:
        return shape
    parent_id = str(attachment["parent_node_id"])
    parent = compiled[parent_id]
    anchor = _anchor_point(_shape_bounds(parent), str(attachment["parent_anchor"]))
    translation = _vec3(attachment["translation_mm"])
    rotation = _vec3(attachment["rotation_deg"])
    position = tuple(anchor[i] + translation[i] for i in range(3))
    return build123d.Location(position, rotation) * shape


def _primitive_shape(build123d: Any, node: Mapping[str, Any]) -> Any:
    kind = node["kind"]
    if kind == "rounded_solid":
        return _rounded_box(
            build123d,
            _vec3(node["size_mm"]),
            float(node["corner_radius_mm"]),
        )
    if kind == "revolve":
        points = [_vec2(point) for point in node["profile_points_mm"]]
        if any(radius < 0 for radius, _height in points):
            raise CadCompileError(
                "CAD_INPUT_INVALID",
                "Revolve profile radii must not be negative.",
            )
        profile = build123d.Plane.XZ * build123d.Polygon(*points, align=None)
        return build123d.revolve(
            profile.face(),
            axis=build123d.Axis.Z,
            revolution_arc=float(node["angle_deg"]),
        )
    if kind == "loft":
        sections = [
            build123d.Pos(0, 0, float(section["z_mm"]))
            * build123d.Ellipse(
                float(section["radius_x_mm"]),
                float(section["radius_y_mm"]),
                align=(build123d.Align.CENTER, build123d.Align.CENTER),
            )
            for section in node["sections"]
        ]
        return build123d.loft(sections)
    if kind == "sweep":
        points = [_vec3(point) for point in node["path_points_mm"]]
        path = build123d.Polyline(*points)
        section = build123d.Circle(float(node["radius_mm"])).face()
        return build123d.sweep(section, path, is_frenet=True)
    raise CadCompileError(
        "CAD_NODE_UNSUPPORTED",
        f"Morphology node kind {kind!r} cannot be compiled as a primitive.",
    )


def _mirror_plane(build123d: Any, plane: str, offset_mm: float) -> Any:
    base = {
        "x": build123d.Plane.YZ,
        "y": build123d.Plane.XZ,
        "z": build123d.Plane.XY,
    }[plane]
    return base.offset(offset_mm)


def _compile_morphology(
    build123d: Any, payload: Mapping[str, Any]
) -> list[_CompiledPart]:
    nodes = payload["morphology"]["nodes"]
    by_id = {node["node_id"]: node for node in nodes}
    dependency_depths: dict[str, int] = {}
    validating: set[str] = set()

    def dependency_depth(node_id: str) -> int:
        if node_id in dependency_depths:
            return dependency_depths[node_id]
        if node_id in validating:
            raise CadCompileError(
                "CAD_INPUT_INVALID",
                "Morphology dependencies are cyclic or too deep.",
            )
        validating.add(node_id)
        node = by_id[node_id]
        dependencies: list[str] = []
        if node["kind"] == "csg":
            dependencies.extend(node["operand_node_ids"])
        elif node["kind"] == "mirror":
            dependencies.append(node["source_node_id"])
        attachment = node.get("attachment")
        if attachment is not None:
            dependencies.append(str(attachment["parent_node_id"]))
        depth = 1
        for dependency in dependencies:
            if dependency not in by_id:
                raise CadCompileError(
                    "CAD_INPUT_INVALID",
                    "Morphology dependencies reference an unknown node.",
                )
            depth = max(depth, dependency_depth(dependency) + 1)
            if depth > 8:
                raise CadCompileError(
                    "CAD_INPUT_INVALID",
                    "Morphology dependencies are cyclic or too deep.",
                )
        validating.remove(node_id)
        dependency_depths[node_id] = depth
        return depth

    for node_id in by_id:
        dependency_depth(node_id)

    compiled: dict[str, Any] = {}
    active: set[str] = set()

    def compile_node(node_id: str) -> Any:
        if node_id in compiled:
            return compiled[node_id]
        if node_id in active:
            raise CadCompileError(
                "CAD_INPUT_INVALID",
                "Morphology dependencies are cyclic or too deep.",
            )
        active.add(node_id)
        node = by_id[node_id]
        kind = node["kind"]
        if kind in {"rounded_solid", "revolve", "loft", "sweep"}:
            shape = _primitive_shape(build123d, node)
        elif kind == "csg":
            operands = [
                compile_node(operand_id) for operand_id in node["operand_node_ids"]
            ]
            shape = operands[0]
            if node["operation"] == "union":
                shape = shape + operands[1:]
            elif node["operation"] == "subtract":
                shape = shape - operands[1:]
            else:
                for operand in operands[1:]:
                    shape = shape & operand
        elif kind == "mirror":
            source = compile_node(node["source_node_id"])
            shape = source.mirror(
                _mirror_plane(
                    build123d,
                    str(node["plane"]),
                    float(node["offset_mm"]),
                )
            )
        else:
            raise CadCompileError(
                "CAD_NODE_UNSUPPORTED",
                f"Morphology node kind {kind!r} is not supported.",
            )
        attachment = node.get("attachment")
        if attachment is not None:
            compile_node(str(attachment["parent_node_id"]))
        shape = _apply_attachment(build123d, shape, attachment, compiled)
        if not shape.solids() or shape.volume <= 0:
            raise CadCompileError(
                "CAD_GEOMETRY_INVALID",
                f"Morphology node {node_id!r} did not produce a solid.",
            )
        # GLB node names are the stable semantic IDs consumed by the shared viewer.
        shape.label = node_id
        compiled[node_id] = shape
        active.remove(node_id)
        return shape

    for node_id in by_id:
        compile_node(node_id)

    consumed_by_csg: set[str] = set()
    pending_csg_operands = [
        operand_id
        for node in nodes
        if node["visible"] and node["kind"] == "csg"
        for operand_id in node["operand_node_ids"]
    ]
    while pending_csg_operands:
        operand_id = pending_csg_operands.pop()
        if operand_id in consumed_by_csg:
            continue
        consumed_by_csg.add(operand_id)
        operand = by_id[operand_id]
        if operand["kind"] == "csg":
            pending_csg_operands.extend(operand["operand_node_ids"])
    appearance = payload["appearance"]
    role_colors = {
        "chassis_shell": appearance["primary_color"],
        "head_shell": appearance["primary_color"],
        "face_bezel": appearance["secondary_color"],
        "beak": appearance["accent_color"],
        "ear": appearance["secondary_color"],
        "wing": appearance["secondary_color"],
        "arm": appearance["secondary_color"],
        "tail": appearance["secondary_color"],
        "ornament": appearance["accent_color"],
        "wheel_cover": appearance["secondary_color"],
        "neck_cover": appearance["secondary_color"],
        "sensor_cover": appearance["secondary_color"],
        "internal_mount": "#777777",
    }
    result: list[_CompiledPart] = []
    for node in nodes:
        node_id = str(node["node_id"])
        if not node["visible"] or node_id in consumed_by_csg:
            continue
        shape = compiled[node_id]
        shape.color = build123d.Color(role_colors[str(node["role"])])
        result.append(
            _CompiledPart(
                name=node_id,
                role=str(node["role"]),
                shape=shape,
                printable=True,
            )
        )
    return result


def _ground_morphology(parts: Sequence[_CompiledPart]) -> float:
    chassis = next(part for part in parts if part.role == "chassis_shell")
    chassis_bounds = _shape_bounds(chassis.shape)
    wheel_radius = min(max(chassis_bounds.size_mm[2] * 0.34, 18.0), 32.0)
    target_bottom = wheel_radius * 0.42
    offset = target_bottom - chassis_bounds.minimum_mm[2]
    for part in parts:
        part.shape = part.shape.translate((0, 0, offset))
    return wheel_radius


def _mechanism_parts(
    build123d: Any,
    morphology: Sequence[_CompiledPart],
    wheel_radius: float,
) -> list[_CompiledPart]:
    chassis = next(part for part in morphology if part.role == "chassis_shell")
    bounds = _shape_bounds(chassis.shape)
    wheel_width = min(max(bounds.size_mm[0] * 0.1, 8.0), 14.0)
    wheel_offset = bounds.size_mm[0] / 2 + wheel_width / 2
    center = bounds.center_mm
    wheel_y = center[1]
    wheels = []
    for side, x in (
        ("left", center[0] - wheel_offset),
        ("right", center[0] + wheel_offset),
    ):
        shape = build123d.Location(
            (x, wheel_y, wheel_radius), (0, 90, 0)
        ) * build123d.Cylinder(
            wheel_radius,
            wheel_width,
            align=(
                build123d.Align.CENTER,
                build123d.Align.CENTER,
                build123d.Align.CENTER,
            ),
        )
        shape.label = f"wheel_{side}"
        shape.color = build123d.Color("#22252A")
        wheels.append(
            _CompiledPart(
                name=f"wheel_{side}",
                role="drive_wheel",
                shape=shape,
                printable=False,
            )
        )

    top = bounds.maximum_mm[2]
    pan = build123d.Cylinder(
        11.0,
        8.0,
        align=(
            build123d.Align.CENTER,
            build123d.Align.CENTER,
            build123d.Align.CENTER,
        ),
    ).translate((center[0], center[1], top + 4.0))
    pan.label = "neck_pan"
    pan.color = build123d.Color("#607080")
    tilt = _rounded_box(build123d, (30.0, 16.0, 12.0), 2.0).translate(
        (center[0], center[1], top + 14.0)
    )
    tilt.label = "neck_tilt"
    tilt.color = build123d.Color("#718394")
    return [
        *wheels,
        _CompiledPart("neck_pan", "pan_joint", pan, False),
        _CompiledPart("neck_tilt", "tilt_joint", tilt, False),
    ]


def _profile_proxy_parts(
    build123d: Any,
    profile: HardwareProfile,
    morphology: Sequence[_CompiledPart],
    shell_node_id: str,
) -> list[_CompiledPart]:
    shell = next(part for part in morphology if part.name == shell_node_id)
    shell_center = _shape_bounds(shell.shape).center_mm
    envelope_center = profile.digital_envelope.center_mm
    translation = tuple(shell_center[axis] - envelope_center[axis] for axis in range(3))
    result: list[_CompiledPart] = []
    proxies = (
        *(
            (
                f"hardware_{component.component_id}",
                f"hardware_{component.envelope.role}",
                component.envelope,
                {
                    "controller": "#24292F",
                    "display": "#4CC9F0",
                    "driver": "#725AC1",
                }[component.envelope.role],
            )
            for component in profile.components
        ),
        *(
            (
                f"keepout_{envelope.component_id}",
                "hardware_keepout",
                envelope,
                None,
            )
            for envelope in profile.keepouts
        ),
    )
    for name, role, envelope, color in proxies:
        position = tuple(
            envelope.center_mm[axis] + translation[axis] for axis in range(3)
        )
        shape = build123d.Box(
            *envelope.size_mm,
            align=(
                build123d.Align.CENTER,
                build123d.Align.CENTER,
                build123d.Align.CENTER,
            ),
        ).translate(position)
        shape.label = name
        shape.color = (
            build123d.Color(color)
            if color is not None
            else build123d.Color(1.0, 0.18, 0.32, 0.2)
        )
        result.append(
            _CompiledPart(
                name=name,
                role=role,
                shape=shape,
                printable=False,
            )
        )
    return result


def _contains_bounds(outer: Bounds3D, inner: Bounds3D, margin_mm: float) -> bool:
    return all(
        inner.minimum_mm[axis] >= outer.minimum_mm[axis] + margin_mm - 1e-6
        and inner.maximum_mm[axis] <= outer.maximum_mm[axis] - margin_mm + 1e-6
        for axis in range(3)
    )


def _check_profile_containment(
    morphology: Sequence[_CompiledPart],
    profile_parts: Sequence[_CompiledPart],
    shell_node_id: str,
) -> None:
    shell = next(part for part in morphology if part.name == shell_node_id)
    shell_bounds = _shape_bounds(shell.shape)
    profile_bounds = _combined_bounds(
        tuple(_part_metadata(part) for part in profile_parts)
    )
    if _contains_bounds(shell_bounds, profile_bounds, _SHOWCASE_PROFILE_MARGIN_MM):
        return
    required = tuple(
        value + 2 * _SHOWCASE_PROFILE_MARGIN_MM for value in profile_bounds.size_mm
    )
    raise CadCompileError(
        "CAD_DIGITAL_ENVELOPE_NOT_CONTAINED",
        "The digital hardware envelope does not fit the selected shell planning AABB.",
        details={
            "shell_node_id": shell_node_id,
            "shell_size_mm": shell_bounds.size_mm,
            "required_size_mm": required,
        },
    )


def _check_head_wheel_clearance(
    morphology: Sequence[_CompiledPart],
    mechanisms: Sequence[_CompiledPart],
) -> None:
    """Reject the one Showcase interference we can measure without a shell model.

    Head-to-neck overlap is intentional at the attachment and the current solids
    do not yet represent hollow cavities. Wheel planning volumes, however, must
    remain clear of the generated head envelope for this fixed family.
    """

    heads = [part for part in morphology if part.role == "head_shell"]
    wheels = [part for part in mechanisms if part.role == "drive_wheel"]
    for head in heads:
        head_bounds = _shape_bounds(head.shape)
        for wheel in wheels:
            wheel_bounds = _shape_bounds(wheel.shape)
            overlap = tuple(
                min(head_bounds.maximum_mm[axis], wheel_bounds.maximum_mm[axis])
                - max(head_bounds.minimum_mm[axis], wheel_bounds.minimum_mm[axis])
                for axis in range(3)
            )
            if not all(value > 1e-6 for value in overlap):
                continue
            required_lift = max(
                0.0, wheel_bounds.maximum_mm[2] - head_bounds.minimum_mm[2]
            )
            raise CadCompileError(
                "CAD_HEAD_WHEEL_INTERFERENCE",
                (
                    f"Head {head.name!r} overlaps {wheel.name!r} by "
                    f"{overlap[0]:.1f} x {overlap[1]:.1f} x {overlap[2]:.1f} mm. "
                    f"Reduce the head or raise its lower envelope by at least "
                    f"{required_lift:.1f} mm."
                ),
                details={
                    "head_node_id": head.name,
                    "wheel_part": wheel.name,
                    "overlap_mm": overlap,
                    "minimum_vertical_adjustment_mm": required_lift,
                },
            )


def _part_metadata(part: _CompiledPart) -> CadPartMetadata:
    return CadPartMetadata(
        name=part.name,
        role=part.role,
        bounds=_shape_bounds(part.shape),
        volume_mm3=round(float(part.shape.volume), 6),
        printable=part.printable,
    )


def _combined_bounds(parts: Sequence[CadPartMetadata]) -> Bounds3D:
    return Bounds3D(
        minimum_mm=tuple(
            min(part.bounds.minimum_mm[axis] for part in parts) for axis in range(3)
        ),  # type: ignore[arg-type]
        maximum_mm=tuple(
            max(part.bounds.maximum_mm[axis] for part in parts) for axis in range(3)
        ),  # type: ignore[arg-type]
    )


def _check_design_limits(payload: Mapping[str, Any], bounds: Bounds3D) -> None:
    maximum = _vec3(payload["constraints"]["maximum_dimensions_mm"])
    exceeded = tuple(
        ("xyz"[axis], bounds.size_mm[axis], maximum[axis])
        for axis in range(3)
        if bounds.size_mm[axis] > maximum[axis] + 1e-6
    )
    if exceeded:
        violations = [
            {
                "axis": axis,
                "measured_mm": round(measured, 6),
                "limit_mm": limit,
                "overage_mm": round(measured - limit, 6),
            }
            for axis, measured, limit in exceeded
        ]
        summary = ", ".join(
            f"{item['axis']} {item['measured_mm']:.1f} mm > "
            f"{item['limit_mm']:.1f} mm (+{item['overage_mm']:.1f} mm)"
            for item in violations
        )
        raise CadCompileError(
            "CAD_DIMENSION_LIMIT_EXCEEDED",
            f"The compiled robot exceeds maximum dimensions: {summary}.",
            details={"violations": violations, **violations[0]},
        )


def _geometry_sha256(
    payload: Mapping[str, Any],
    profile: HardwareProfile,
    parts: Sequence[CadPartMetadata],
) -> str:
    morphology = {
        "nodes": [
            {key: value for key, value in node.items() if key != "label"}
            for node in payload["morphology"]["nodes"]
        ]
    }
    profile_geometry = {
        "profile_id": profile.profile_id,
        "components": [
            {
                "component_id": component.component_id,
                "role": component.envelope.role,
                "size_mm": component.envelope.size_mm,
                "center_mm": component.envelope.center_mm,
            }
            for component in profile.components
        ],
        "keepouts": [
            {
                "component_id": envelope.component_id,
                "size_mm": envelope.size_mm,
                "center_mm": envelope.center_mm,
            }
            for envelope in profile.keepouts
        ],
    }
    geometry_record = {
        "compiler_version": CAD_COMPILER_VERSION,
        "build123d_version": BUILD123D_VERSION,
        "morphology": morphology,
        "appearance": payload["appearance"],
        "profile": profile_geometry,
        "parts": [
            {
                "name": part.name,
                "role": part.role,
                "minimum_mm": [round(value, 6) for value in part.bounds.minimum_mm],
                "maximum_mm": [round(value, 6) for value in part.bounds.maximum_mm],
                "volume_mm3": part.volume_mm3,
                "printable": part.printable,
            }
            for part in parts
        ],
    }
    return hashlib.sha256(_canonical_bytes(geometry_record)).hexdigest()


def _canonicalize_step(content: bytes) -> bytes:
    """Normalize OCCT labels and discard nondeterministic presentation records.

    OCCT emits the geometric/assembly records deterministically, but appends colour
    presentation entities in process-dependent order. GLB remains the authoritative
    coloured preview; STEP is the editable geometric assembly, so those orphaned
    presentation records are removed before hashing.
    """

    try:
        source = content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise CadCompileError(
            "CAD_EXPORT_FAILED", "The STEP exporter produced invalid text."
        ) from error

    output: list[str] = []
    pending_space = False
    quoted = False
    index = 0
    while index < len(source):
        char = source[index]
        if quoted:
            output.append(char)
            if char == "'":
                if index + 1 < len(source) and source[index + 1] == "'":
                    output.append("'")
                    index += 1
                else:
                    quoted = False
            index += 1
            continue
        if char == "'":
            if pending_space and output and output[-1] not in {"\n", "(", ","}:
                output.append(" ")
            pending_space = False
            quoted = True
            output.append(char)
        elif char.isspace():
            pending_space = True
        else:
            if (
                pending_space
                and output
                and output[-1] not in {"\n", "(", ",", "=", "#"}
                and char
                not in {
                    ")",
                    ",",
                    ";",
                }
            ):
                output.append(" ")
            pending_space = False
            output.append(char)
            if char == ";":
                output.append("\n")
        index += 1

    occurrence = 0

    def normalize_occurrence(match: re.Match[str]) -> str:
        nonlocal occurrence
        occurrence += 1
        return f"NEXT_ASSEMBLY_USAGE_OCCURRENCE('{occurrence}'"

    normalized = re.sub(
        r"NEXT_ASSEMBLY_USAGE_OCCURRENCE\('[0-9]+'",
        normalize_occurrence,
        "".join(output).strip(),
    )
    lines = normalized.splitlines()
    presentation_start = next(
        (
            index
            for index, line in enumerate(lines)
            if "=MECHANICAL_DESIGN_GEOMETRIC_PRESENTATION_REPRESENTATION(" in line
        ),
        None,
    )
    if presentation_start is not None:
        data_end = next(
            (
                index
                for index in range(presentation_start, len(lines))
                if lines[index] == "ENDSEC;"
            ),
            None,
        )
        if data_end is None:
            raise CadCompileError(
                "CAD_EXPORT_FAILED",
                "The STEP exporter produced an invalid data section.",
            )
        lines[presentation_start:data_end] = []
        normalized = "\n".join(lines)
    return (normalized + "\n").encode()


def _canonicalize_3mf(content: bytes, geometry_sha256: str) -> bytes:
    """Replace generated UUIDs and ZIP metadata with deterministic values."""

    uuid_pattern = re.compile(
        rb"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
    )
    replacements: dict[bytes, bytes] = {}

    def replace_uuid(match: re.Match[bytes]) -> bytes:
        original = match.group(0).lower()
        if original not in replacements:
            replacements[original] = str(
                uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"character-robot:{geometry_sha256}:3mf:{len(replacements)}",
                )
            ).encode()
        return replacements[original]

    try:
        with zipfile.ZipFile(io.BytesIO(content), "r") as source:
            entries = {
                name: uuid_pattern.sub(replace_uuid, source.read(name))
                for name in sorted(source.namelist())
            }
    except (OSError, zipfile.BadZipFile, KeyError) as error:
        raise CadCompileError(
            "CAD_EXPORT_FAILED", "The 3MF exporter produced an invalid package."
        ) from error

    target = io.BytesIO()
    with zipfile.ZipFile(
        target,
        "w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
    ) as package:
        for name, payload in entries.items():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 0
            info.external_attr = 0
            package.writestr(
                info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9
            )
    return target.getvalue()


def _export_artifacts(
    build123d: Any,
    parts: Sequence[_CompiledPart],
    geometry_sha256: str,
) -> tuple[CompiledArtifact, ...]:
    # build123d gives Compound ownership of its child objects. Each export tree
    # therefore needs independent shape wrappers or constructing the printable
    # tree would silently re-parent character parts out of the GLB preview.
    preview = build123d.Compound(
        label="Character Robot", children=[copy(part.shape) for part in parts]
    )
    printable = [part for part in parts if part.printable]
    manufacturing = build123d.Compound(
        label="Character Robot Printable Parts",
        children=[copy(part.shape) for part in printable],
    )
    names = {
        "glb": ("preview.glb", "model/gltf-binary"),
        "step": ("assembly.step", "model/step"),
        "stl": ("printable-parts.stl", "model/stl"),
        "3mf": ("printable-parts.3mf", "model/3mf"),
    }
    artifacts: list[CompiledArtifact] = []
    with tempfile.TemporaryDirectory(prefix="character-cad-") as temporary:
        directory = Path(temporary)
        paths = {kind: directory / names[kind][0] for kind in _EXPORT_ORDER}
        results = {
            "glb": build123d.export_gltf(preview, paths["glb"], binary=True),
            "step": build123d.export_step(
                preview,
                paths["step"],
                timestamp="2000-01-01T00:00:00",
            ),
            "stl": build123d.export_stl(manufacturing, paths["stl"]),
        }
        mesher = build123d.Mesher()
        for part in printable:
            mesher.add_shape(
                part.shape,
                part_number=part.name,
                uuid_value=uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"character-robot:{geometry_sha256}:{part.name}",
                ),
            )
        mesher.write(paths["3mf"])
        for kind, success in results.items():
            if success is False:
                raise CadCompileError(
                    "CAD_EXPORT_FAILED",
                    f"The {kind.upper()} exporter did not complete.",
                )
        for kind in _EXPORT_ORDER:
            path = paths[kind]
            if not path.is_file():
                raise CadCompileError(
                    "CAD_EXPORT_FAILED",
                    f"The {kind.upper()} exporter did not produce an artifact.",
                )
            content = path.read_bytes()
            if kind == "step":
                content = _canonicalize_step(content)
            elif kind == "3mf":
                content = _canonicalize_3mf(content, geometry_sha256)
            if not content or len(content) > _MAX_ARTIFACT_BYTES:
                raise CadCompileError(
                    "CAD_EXPORT_FAILED",
                    f"The {kind.upper()} artifact has an invalid size.",
                )
            file_name, media_type = names[kind]
            artifacts.append(
                CompiledArtifact(
                    kind=kind,
                    file_name=file_name,
                    media_type=media_type,
                    content=content,
                    sha256=hashlib.sha256(content).hexdigest(),
                )
            )
    return tuple(artifacts)


class CadCompiler:
    def __init__(self, profile_registry: ProfileRegistry | None = None) -> None:
        self.profile_registry = profile_registry or ProfileRegistry()

    def compile(
        self,
        spec: CharacterRobotSpec | Mapping[str, object],
        profile: HardwareProfile | None = None,
    ) -> CadCompileResult:
        payload = _validated_spec_payload(spec)
        selected_profile = profile or self.profile_registry.get_profile(
            str(payload["hardware_profile_id"])
        )
        if selected_profile.profile_id != payload["hardware_profile_id"]:
            raise CadCompileError(
                "CAD_PROFILE_MISMATCH",
                "The selected hardware profile does not match the character design.",
            )
        build123d = _load_build123d()
        try:
            profile_shell_node_id = _autofit_profile_shell(payload, selected_profile)
            morphology = _compile_morphology(build123d, payload)
            wheel_radius = _ground_morphology(morphology)
            profile_parts = _profile_proxy_parts(
                build123d,
                selected_profile,
                morphology,
                profile_shell_node_id,
            )
            _check_profile_containment(
                morphology,
                profile_parts,
                profile_shell_node_id,
            )
            mechanisms = _mechanism_parts(build123d, morphology, wheel_radius)
            _check_head_wheel_clearance(morphology, mechanisms)
            parts = [
                *morphology,
                *mechanisms,
                *profile_parts,
            ]
            part_metadata = tuple(_part_metadata(part) for part in parts)
            assembly_bounds = _combined_bounds(part_metadata)
            _check_design_limits(payload, assembly_bounds)
            geometry_sha256 = _geometry_sha256(payload, selected_profile, part_metadata)
            artifacts = _export_artifacts(build123d, parts, geometry_sha256)
        except CadCompileError:
            raise
        except Exception as error:
            raise CadCompileError(
                "CAD_GEOMETRY_FAILED",
                "The bounded character geometry could not be compiled.",
            ) from error

        issues = []
        if selected_profile.qualification == "digital_only":
            issues.extend(
                [
                    CadIssue(
                        code="profile_incomplete",
                        severity="warning",
                        path="hardware_profile_id",
                        message="This hardware profile is digital-only and has unverified physical values.",
                        suggestion="Measure the listed unknowns before treating the design as build-ready.",
                    ),
                    CadIssue(
                        code="showcase_aabb_only",
                        severity="warning",
                        path="hardware_profile_id",
                        message=(
                            "Profile containment uses a planning AABB; the separate measured manufacturing validator must establish cavity, mounting, connector, and motion clearances."
                        ),
                        suggestion="Connect the profile's versioned manufacturing evidence before qualification.",
                    ),
                ]
            )
        if selected_profile.mass.complete_assembly_mass_g is None:
            issues.append(
                CadIssue(
                    code="center_of_gravity_unknown",
                    severity="warning",
                    path="hardware_profile_id",
                    message=(
                        "Center of gravity is unknown because complete component and "
                        "assembly mass data is unavailable."
                    ),
                    suggestion="Measure every installed mass before calculating center of gravity.",
                )
            )
        if not any(part.role == "head_shell" for part in morphology):
            issues.append(
                CadIssue(
                    code="head_shell_missing",
                    severity="warning",
                    path="morphology.nodes",
                    message="The design has no explicit head shell.",
                    suggestion="Add a head shell before physical packaging validation.",
                )
            )
        return CadCompileResult(
            compiler_version=CAD_COMPILER_VERSION,
            build123d_version=BUILD123D_VERSION,
            profile_id=selected_profile.profile_id,
            geometry_sha256=geometry_sha256,
            assembly_bounds=assembly_bounds,
            parts=part_metadata,
            artifacts=artifacts,
            issues=tuple(issues),
        )


__all__ = [
    "BUILD123D_VERSION",
    "CAD_COMPILER_VERSION",
    "Bounds3D",
    "CadCompileError",
    "CadCompileResult",
    "CadCompiler",
    "CadIssue",
    "CadPartMetadata",
    "CompiledArtifact",
]
