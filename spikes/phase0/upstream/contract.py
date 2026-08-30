from __future__ import annotations

from typing import Any


REQUIRED_TOOL_NAMES = (
    "validate_mjcf",
    "sim_load",
    "sim_reset",
    "sim_set_state",
    "run_and_analyze",
    "model_summary",
    "render_snapshot",
)

SIM_LOAD_SCALAR_TYPES = {
    "name": str,
    "mujoco_version": str,
    "nq": int,
    "nv": int,
    "nu": int,
    "nbody": int,
    "ngeom": int,
    "njnt": int,
    "nsite": int,
    "nsensor": int,
    "ncam": int,
    "timestep": float,
    "has_renderer": bool,
}
SIM_LOAD_LIST_FIELDS = {
    "bodies",
    "joints",
    "actuators",
    "sensors",
    "cameras",
}


def matches_sim_load_result(payload: dict[str, Any]) -> bool:
    if set(payload) != set(SIM_LOAD_SCALAR_TYPES) | SIM_LOAD_LIST_FIELDS:
        return False
    if any(
        type(payload[name]) is not expected
        for name, expected in SIM_LOAD_SCALAR_TYPES.items()
    ):
        return False
    return all(
        type(payload[name]) is list and all(type(item) is str for item in payload[name])
        for name in SIM_LOAD_LIST_FIELDS
    )


def _nullable(type_name: str, title: str) -> dict[str, Any]:
    return {
        "anyOf": [{"type": type_name}, {"type": "null"}],
        "default": None,
        "title": title,
    }


def _nullable_array(item_type: str, title: str) -> dict[str, Any]:
    return {
        "anyOf": [
            {"items": {"type": item_type}, "type": "array"},
            {"type": "null"},
        ],
        "default": None,
        "title": title,
    }


def _object_schema(
    title: str,
    properties: dict[str, Any],
    required: tuple[str, ...] = (),
) -> dict[str, Any]:
    schema: dict[str, Any] = {
        "properties": properties,
        "title": title,
        "type": "object",
    }
    if required:
        schema["required"] = list(required)
    return schema


REQUIRED_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "validate_mjcf": _object_schema(
        "validate_mjcfArguments",
        {
            "xml_path": _nullable("string", "Xml Path"),
            "xml_string": _nullable("string", "Xml String"),
        },
    ),
    "sim_load": _object_schema(
        "sim_loadArguments",
        {
            "name": {"default": "default", "title": "Name", "type": "string"},
            "xml_path": _nullable("string", "Xml Path"),
            "xml_string": _nullable("string", "Xml String"),
        },
    ),
    "sim_reset": _object_schema(
        "sim_resetArguments",
        {"sim_name": _nullable("string", "Sim Name")},
    ),
    "sim_set_state": _object_schema(
        "sim_set_stateArguments",
        {
            "ctrl": _nullable_array("number", "Ctrl"),
            "keyframe": _nullable("integer", "Keyframe"),
            "qpos": _nullable_array("number", "Qpos"),
            "qvel": _nullable_array("number", "Qvel"),
            "sim_name": _nullable("string", "Sim Name"),
        },
    ),
    "run_and_analyze": _object_schema(
        "run_and_analyzeArguments",
        {
            "camera": _nullable("string", "Camera"),
            "capture_every_n": {
                "default": 200,
                "title": "Capture Every N",
                "type": "integer",
            },
            "ctrl": _nullable_array("number", "Ctrl"),
            "n_steps": {"default": 1000, "title": "N Steps", "type": "integer"},
            "sim_name": _nullable("string", "Sim Name"),
            "track": {
                "anyOf": [
                    {"items": {"type": "string"}, "type": "array"},
                    {"type": "null"},
                ],
                "default": None,
                "title": "Track",
            },
        },
    ),
    "model_summary": _object_schema(
        "model_summaryArguments",
        {
            "ctx": {"title": "ctx", "type": "string"},
            "sim_name": _nullable("string", "Sim Name"),
        },
        required=("ctx",),
    ),
    "render_snapshot": _object_schema(
        "render_snapshotArguments",
        {
            "camera": _nullable("string", "Camera"),
            "height": _nullable("integer", "Height"),
            "show_contacts": {
                "default": False,
                "title": "Show Contacts",
                "type": "boolean",
            },
            "sim_name": _nullable("string", "Sim Name"),
            "width": _nullable("integer", "Width"),
        },
    ),
}


PRIMITIVE_XML = """<mujoco model="phase0-primitive">
  <option timestep="0.002"/>
  <worldbody>
    <geom name="primitive" type="box" size="0.1 0.1 0.1"/>
  </worldbody>
</mujoco>"""


INVALID_XML = "<mujoco><worldbody>"
