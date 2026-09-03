from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from character_robot.schemas import (
    CharacterRobotSpec,
    MorphologyGraph,
    TOOL_INPUT_MODELS,
    TOOL_NAMES,
    TOOL_OUTPUT_MODELS,
)


def _spec_payload() -> dict[str, object]:
    return {
        "identity": {
            "name": "Pip",
            "role": "desk guide",
            "motif": "duck",
            "design_brief": "A small shy but curious indoor guide robot.",
        },
        "hardware_profile_id": "m5-cores3-goplus2/v1",
        "appearance": {
            "primary_color": "#F2C94C",
            "secondary_color": "#FFF7D6",
            "accent_color": "#F2994A",
            "style_tags": ["soft", "friendly"],
        },
        "morphology": {
            "nodes": [
                {
                    "kind": "rounded_solid",
                    "node_id": "body",
                    "role": "chassis_shell",
                    "label": "rounded body",
                    "size_mm": {"x": 100.0, "y": 80.0, "z": 58.0},
                    "corner_radius_mm": 18.0,
                },
                {
                    "kind": "loft",
                    "node_id": "head",
                    "role": "head_shell",
                    "label": "duck head",
                    "attachment": {
                        "parent_node_id": "body",
                        "parent_anchor": "neck_mount",
                    },
                    "sections": [
                        {"z_mm": -20.0, "radius_x_mm": 38.0, "radius_y_mm": 33.0},
                        {"z_mm": 20.0, "radius_x_mm": 34.0, "radius_y_mm": 31.0},
                    ],
                },
            ]
        },
        "personality": {
            "curiosity": 0.8,
            "boldness": 0.2,
            "energy": 0.4,
            "sociability": 0.7,
            "voice_style": "shy",
            "motion_style": "careful",
        },
        "face": {
            "default_expression": "neutral",
            "supported_expressions": ["neutral", "happy", "listening"],
        },
        "behavior": {
            "scenarios": [
                {
                    "scenario_id": "greet",
                    "duration_ms": 1200,
                    "keyframes": [
                        {"at_ms": 0, "face_expression": "neutral"},
                        {
                            "at_ms": 600,
                            "face_expression": "happy",
                            "head_pan_deg": 18.0,
                        },
                    ],
                }
            ]
        },
        "manufacturing": {
            "material": "pla",
            "printer_volume_mm": {"x": 220.0, "y": 220.0, "z": 250.0},
        },
        "constraints": {
            "maximum_dimensions_mm": {"x": 180.0, "y": 180.0, "z": 220.0},
            "maximum_mass_g": 900.0,
        },
        "versions": {
            "compiler": "character-cad-v1",
            "catalog": "hardware-catalog-v1",
            "firmware_runtime": "character-runtime-v1",
        },
    }


def test_public_surface_has_exactly_eight_semantic_operations() -> None:
    assert TOOL_NAMES == (
        "get_studio_context",
        "set_design_draft",
        "revise_design_draft",
        "inspect_design",
        "preview_scenario",
        "validate_design",
        "create_revision_from_draft",
        "prepare_build_pack",
    )
    assert len(TOOL_INPUT_MODELS) == 8
    assert len(TOOL_OUTPUT_MODELS) == 8


def test_character_spec_is_strict_bounded_and_behavior_references_face_contract() -> (
    None
):
    spec = CharacterRobotSpec.model_validate(_spec_payload())
    assert spec.morphology.nodes[1].attachment.parent_node_id == "body"

    raw_code = _spec_payload()
    raw_code["python"] = "import os"
    with pytest.raises(ValidationError):
        CharacterRobotSpec.model_validate(raw_code)

    unsupported = _spec_payload()
    unsupported["behavior"]["scenarios"][0]["keyframes"][1]["face_expression"] = (
        "delighted"
    )
    with pytest.raises(ValidationError, match="unsupported expressions"):
        CharacterRobotSpec.model_validate(unsupported)


def test_morphology_rejects_unknown_dependencies_cycles_and_excessive_radius() -> None:
    payload = _spec_payload()["morphology"]
    unknown = copy.deepcopy(payload)
    unknown["nodes"][1]["attachment"]["parent_node_id"] = "missing"
    with pytest.raises(ValidationError, match="unknown nodes"):
        MorphologyGraph.model_validate(unknown)

    cycle = copy.deepcopy(payload)
    cycle["nodes"][0]["attachment"] = {
        "parent_node_id": "head",
        "parent_anchor": "body",
    }
    with pytest.raises(ValidationError, match="cycle"):
        MorphologyGraph.model_validate(cycle)

    radius = copy.deepcopy(payload)
    radius["nodes"][0]["corner_radius_mm"] = 40.0
    with pytest.raises(ValidationError, match="shortest side"):
        MorphologyGraph.model_validate(radius)


def test_behavior_timeline_must_start_at_zero_and_be_ascending() -> None:
    payload = _spec_payload()
    payload["behavior"]["scenarios"][0]["keyframes"][0]["at_ms"] = 700
    with pytest.raises(ValidationError, match="start at 0"):
        CharacterRobotSpec.model_validate(payload)
