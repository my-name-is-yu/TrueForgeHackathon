from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import struct
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import replace

import pytest

import character_robot.cad as cad_module
from character_robot.cad import CadCompileError, CadCompiler
from character_robot.profiles import ProfileRegistry
from character_robot.schemas import CharacterRobotSpec, SetDesignDraftInput
from character_robot.service import CharacterRobotService


def _spec_payload(
    *,
    profile_id: str = "m5-cores3-goplus2/v1",
    maximum_dimensions_mm: tuple[float, float, float] = (250.0, 250.0, 250.0),
) -> dict[str, object]:
    return {
        "identity": {
            "name": "Timid Duck Guide",
            "role": "Indoor guide",
            "motif": "duck",
            "design_brief": "A small, cautious, curious duck companion.",
        },
        "hardware_profile_id": profile_id,
        "appearance": {
            "primary_color": "#F4C542",
            "secondary_color": "#FFF2B2",
            "accent_color": "#EF7F1A",
            "eye_color": "#111111",
            "finish": "matte",
            "style_tags": ["rounded", "friendly"],
        },
        "morphology": {
            "nodes": [
                {
                    "node_id": "chassis",
                    "role": "chassis_shell",
                    "label": "Rounded chassis",
                    "kind": "rounded_solid",
                    "size_mm": {"x": 96.0, "y": 76.0, "z": 64.0},
                    "corner_radius_mm": 12.0,
                    "attachment": None,
                    "visible": True,
                },
                {
                    "node_id": "head",
                    "role": "head_shell",
                    "label": "Duck head",
                    "kind": "rounded_solid",
                    "size_mm": {"x": 78.0, "y": 64.0, "z": 70.0},
                    "corner_radius_mm": 18.0,
                    "attachment": {
                        "parent_node_id": "chassis",
                        "parent_anchor": "top",
                        "translation_mm": {"x": 0.0, "y": 0.0, "z": 45.0},
                        "rotation_deg": {"x": 0.0, "y": 0.0, "z": 0.0},
                    },
                    "visible": True,
                },
                {
                    "node_id": "beak",
                    "role": "beak",
                    "label": "Short beak",
                    "kind": "loft",
                    "sections": [
                        {"z_mm": 0.0, "radius_x_mm": 15.0, "radius_y_mm": 8.0},
                        {"z_mm": 18.0, "radius_x_mm": 9.0, "radius_y_mm": 5.0},
                    ],
                    "attachment": {
                        "parent_node_id": "head",
                        "parent_anchor": "front",
                        "translation_mm": {"x": 0.0, "y": -9.0, "z": -5.0},
                        "rotation_deg": {"x": 90.0, "y": 0.0, "z": 0.0},
                    },
                    "visible": True,
                },
            ]
        },
        "personality": {
            "curiosity": 0.7,
            "boldness": 0.2,
            "energy": 0.35,
            "sociability": 0.65,
            "voice_style": "shy",
            "motion_style": "careful",
        },
        "face": {
            "default_expression": "neutral",
            "supported_expressions": ["neutral", "happy"],
        },
        "behavior": {
            "scenarios": [
                {
                    "scenario_id": "idle",
                    "duration_ms": 1200,
                    "keyframes": [
                        {
                            "at_ms": 0,
                            "face_expression": "neutral",
                            "wheel_left": 0.0,
                            "wheel_right": 0.0,
                            "head_pan_deg": 0.0,
                            "head_tilt_deg": 0.0,
                            "sound_cue": None,
                        }
                    ],
                }
            ]
        },
        "manufacturing": {
            "material": "pla",
            "nozzle_diameter_mm": 0.4,
            "layer_height_mm": 0.2,
            "minimum_wall_mm": 1.6,
            "fit_clearance_mm": 0.3,
            "printer_volume_mm": {"x": 250.0, "y": 250.0, "z": 250.0},
        },
        "constraints": {
            "maximum_dimensions_mm": {
                "x": maximum_dimensions_mm[0],
                "y": maximum_dimensions_mm[1],
                "z": maximum_dimensions_mm[2],
            },
            "maximum_mass_g": 1200.0,
            "maximum_speed_m_s": 0.25,
            "indoor_only": True,
            "low_voltage_only": True,
        },
        "versions": {
            "schema_version": "character-robot/v1",
            "compiler": "character-cad-v1",
            "catalog": "hardware-catalog-v1",
            "firmware_runtime": "character-runtime-v1",
        },
    }


def _glb_node_names(content: bytes) -> set[str]:
    json_length, chunk_type = struct.unpack_from("<II", content, 12)
    assert chunk_type == 0x4E4F534A
    document = json.loads(content[20 : 20 + json_length].decode())
    return {node["name"] for node in document["nodes"] if "name" in node}


def test_compiler_generates_single_source_preview_and_cad_artifacts() -> None:
    spec = CharacterRobotSpec.model_validate(_spec_payload())

    result = CadCompiler().compile(spec)

    assert result.profile_id == "m5-cores3-goplus2/v1"
    assert len(result.geometry_sha256) == 64
    assert all(dimension > 0 for dimension in result.dimensions_mm)
    assert {part.name for part in result.parts}.issuperset(
        {
            "chassis",
            "head",
            "beak",
            "wheel_left",
            "wheel_right",
            "neck_pan",
            "neck_tilt",
        }
    )
    parts = {part.name: part for part in result.parts}
    assert parts["chassis"].bounds.center_mm[0] == pytest.approx(0.0, abs=1e-6)
    assert parts["head"].bounds.center_mm[:2] == pytest.approx(
        parts["chassis"].bounds.center_mm[:2], abs=1e-6
    )
    assert [artifact.kind for artifact in result.artifacts] == [
        "glb",
        "step",
        "stl",
        "3mf",
    ]
    assert result.artifacts[0].content.startswith(b"glTF")
    assert _glb_node_names(result.artifacts[0].content).issuperset(
        {
            "chassis",
            "head",
            "beak",
            "wheel_left",
            "wheel_right",
            "neck_pan",
            "neck_tilt",
        }
    )
    assert result.artifacts[1].content.startswith(b"ISO-10303-21;")
    assert result.artifacts[3].content.startswith(b"PK")
    for artifact in result.artifacts:
        assert artifact.sha256 == hashlib.sha256(artifact.content).hexdigest()
        assert artifact.experimental is True
        assert artifact.byte_size > 0


def test_default_service_exposes_the_generated_glb_bytes() -> None:
    service = CharacterRobotService()
    spec = CharacterRobotSpec.model_validate(_spec_payload())

    draft = asyncio.run(
        service.set_design_draft(SetDesignDraftInput(expected_revision=None, spec=spec))
    )

    assert draft.preview_artifact is not None
    assert draft.preview_artifact.kind == "glb"
    assert service.artifact_bytes(draft.preview_artifact.sha256).startswith(b"glTF")


def test_compiler_geometry_digest_is_deterministic() -> None:
    spec = CharacterRobotSpec.model_validate(_spec_payload())
    compiler = CadCompiler()

    first = compiler.compile(spec)
    second = compiler.compile(spec)

    assert first.geometry_sha256 == second.geometry_sha256
    assert first.parts == second.parts
    assert {artifact.kind: artifact.sha256 for artifact in first.artifacts} == {
        artifact.kind: artifact.sha256 for artifact in second.artifacts
    }
    assert {artifact.kind: artifact.content for artifact in first.artifacts} == {
        artifact.kind: artifact.content for artifact in second.artifacts
    }


def test_canonical_step_and_3mf_round_trip_part_geometry(tmp_path) -> None:
    result = CadCompiler().compile(CharacterRobotSpec.model_validate(_spec_payload()))
    artifacts = {artifact.kind: artifact for artifact in result.artifacts}
    step_path = tmp_path / "assembly.step"
    step_path.write_bytes(artifacts["step"].content)

    imported = cad_module._load_build123d().import_step(step_path)

    imported_parts = {part.label: part for part in imported.children}
    assert set(imported_parts) == {part.name for part in result.parts}
    for expected in result.parts:
        actual = imported_parts[expected.name]
        bounds = actual.bounding_box()
        assert actual.volume == pytest.approx(expected.volume_mm3, rel=1e-9)
        assert tuple(bounds.min) == pytest.approx(expected.bounds.minimum_mm, abs=1e-6)
        assert tuple(bounds.max) == pytest.approx(expected.bounds.maximum_mm, abs=1e-6)

    package_path = tmp_path / "printable-parts.3mf"
    package_path.write_bytes(artifacts["3mf"].content)
    with zipfile.ZipFile(package_path) as package:
        assert "3D/3dmodel.model" in package.namelist()
        assert package.testzip() is None
        model = ET.fromstring(package.read("3D/3dmodel.model"))

    namespace = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    mesh_objects = model.findall(".//m:object[@partnumber]", namespace)
    printable = {part.name: part for part in result.parts if part.printable}
    assert {item.attrib["partnumber"] for item in mesh_objects} == set(printable)
    for item in mesh_objects:
        expected = printable[item.attrib["partnumber"]]
        vertices = [
            tuple(float(vertex.attrib[axis]) for axis in "xyz")
            for vertex in item.findall("./m:mesh/m:vertices/m:vertex", namespace)
        ]
        triangles = [
            tuple(int(triangle.attrib[key]) for key in ("v1", "v2", "v3"))
            for triangle in item.findall("./m:mesh/m:triangles/m:triangle", namespace)
        ]
        assert vertices and triangles
        mesh_volume = 0.0
        for first, second, third in triangles:
            a, b, c = vertices[first], vertices[second], vertices[third]
            mesh_volume += (
                a[0] * (b[1] * c[2] - b[2] * c[1])
                - a[1] * (b[0] * c[2] - b[2] * c[0])
                + a[2] * (b[0] * c[1] - b[1] * c[0])
            ) / 6.0
        mesh_minimum = tuple(
            min(vertex[axis] for vertex in vertices) for axis in range(3)
        )
        mesh_maximum = tuple(
            max(vertex[axis] for vertex in vertices) for axis in range(3)
        )
        assert abs(mesh_volume) == pytest.approx(expected.volume_mm3, rel=0.002)
        assert mesh_minimum == pytest.approx(expected.bounds.minimum_mm, abs=0.001)
        assert mesh_maximum == pytest.approx(expected.bounds.maximum_mm, abs=0.001)


def test_qualified_profile_does_not_emit_digital_only_cad_warning() -> None:
    spec = CharacterRobotSpec.model_validate(_spec_payload())
    profile = replace(
        ProfileRegistry().get_profile(spec.hardware_profile_id),
        qualification="profile_qualified",
    )

    result = CadCompiler().compile(spec, profile)

    codes = {issue.code for issue in result.issues}
    assert "profile_incomplete" not in codes
    assert "showcase_aabb_only" not in codes


def test_profile_switch_reflows_shell_and_preserves_profile_space_layout() -> None:
    compiler = CadCompiler()
    m5 = compiler.compile(CharacterRobotSpec.model_validate(_spec_payload()))
    pi = compiler.compile(
        CharacterRobotSpec.model_validate(
            _spec_payload(profile_id="pi-zero2wh-crickit-ws2/v1")
        )
    )
    m5_parts = {part.name: part for part in m5.parts}
    pi_parts = {part.name: part for part in pi.parts}

    assert m5.dimensions_mm != pytest.approx(pi.dimensions_mm)
    assert m5_parts["head"].bounds.size_mm[1] == pytest.approx(77.0)
    assert pi_parts["head"].bounds.size_mm[1] == pytest.approx(108.0)
    assert (
        m5_parts["hardware_m5stack-cores3"].bounds.center_mm[1]
        - m5_parts["hardware_m5stack-goplus2"].bounds.center_mm[1]
    ) == pytest.approx(-14.25)
    assert (
        pi_parts["hardware_raspberry-pi-zero-2-wh"].bounds.center_mm[2]
        - pi_parts["hardware_adafruit-crickit-hat"].bounds.center_mm[2]
    ) == pytest.approx(-18.0)
    assert {
        "keepout_cores3-front-access",
        "keepout_goplus2-rear-cable-access",
    }.issubset(m5_parts)
    assert all(
        not part.printable
        for name, part in m5_parts.items()
        if name.startswith("keepout_")
    )
    assert "keepout_cores3-front-access" in _glb_node_names(m5.artifacts[0].content)
    assert "center_of_gravity_unknown" in {issue.code for issue in m5.issues}


def test_non_geometric_edits_preserve_geometry_digest() -> None:
    base_payload = _spec_payload()
    non_geometric_payload = copy.deepcopy(base_payload)
    non_geometric_payload["identity"]["name"] = "A renamed duck"
    non_geometric_payload["personality"]["curiosity"] = 0.1
    non_geometric_payload["behavior"]["scenarios"][0]["keyframes"][0][
        "head_pan_deg"
    ] = 42.0
    appearance_payload = copy.deepcopy(base_payload)
    appearance_payload["appearance"]["primary_color"] = "#123456"
    compiler = CadCompiler()

    base = compiler.compile(CharacterRobotSpec.model_validate(base_payload))
    non_geometric = compiler.compile(
        CharacterRobotSpec.model_validate(non_geometric_payload)
    )
    appearance = compiler.compile(CharacterRobotSpec.model_validate(appearance_payload))

    assert non_geometric.geometry_sha256 == base.geometry_sha256
    assert non_geometric.parts == base.parts
    assert appearance.geometry_sha256 != base.geometry_sha256


def test_compiler_rejects_a_profile_mismatch_before_geometry() -> None:
    spec = CharacterRobotSpec.model_validate(_spec_payload())
    pi_profile = ProfileRegistry().get_profile("pi-zero2wh-crickit-ws2/v1")

    with pytest.raises(CadCompileError) as caught:
        CadCompiler().compile(spec, pi_profile)

    assert caught.value.code == "CAD_PROFILE_MISMATCH"
    assert caught.value.retryable is False


def test_compiler_rejects_dimensions_without_exporting_a_broken_draft() -> None:
    spec = CharacterRobotSpec.model_validate(
        _spec_payload(maximum_dimensions_mm=(80.0, 80.0, 80.0))
    )

    with pytest.raises(CadCompileError) as caught:
        CadCompiler().compile(spec)

    assert caught.value.code == "CAD_DIMENSION_LIMIT_EXCEEDED"
    assert caught.value.details["measured_mm"] > caught.value.details["limit_mm"]
    assert "mm >" in caught.value.safe_message
    assert "+" in caught.value.safe_message


def test_compiler_rejects_a_head_that_intersects_the_wheel_volume() -> None:
    payload = _spec_payload()
    head = payload["morphology"]["nodes"][1]
    head["size_mm"] = {"x": 160.0, "y": 160.0, "z": 160.0}
    head["corner_radius_mm"] = 30.0
    head["attachment"]["translation_mm"]["z"] = -30.0
    spec = CharacterRobotSpec.model_validate(payload)

    with pytest.raises(CadCompileError) as caught:
        CadCompiler().compile(spec)

    assert caught.value.code == "CAD_HEAD_WHEEL_INTERFERENCE"
    assert caught.value.details["minimum_vertical_adjustment_mm"] > 0
    assert "overlaps" in caught.value.safe_message


def test_missing_build123d_is_a_typed_dependency_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = CharacterRobotSpec.model_validate(_spec_payload())

    def unavailable() -> object:
        raise ImportError("private import failure")

    monkeypatch.setattr(cad_module, "_import_build123d", unavailable)

    with pytest.raises(CadCompileError) as caught:
        CadCompiler().compile(spec)

    assert caught.value.code == "CAD_DEPENDENCY_UNAVAILABLE"
    assert "private import failure" not in str(caught.value)
