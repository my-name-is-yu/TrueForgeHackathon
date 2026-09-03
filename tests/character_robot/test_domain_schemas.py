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


def _leaf_first_dependency_nodes(kind: str, *, depth: int) -> list[dict[str, object]]:
    nodes: list[dict[str, object]] = [
        {
            "kind": "rounded_solid",
            "node_id": "base",
            "role": "chassis_shell",
            "label": "base",
            "size_mm": {"x": 40.0, "y": 40.0, "z": 40.0},
            "corner_radius_mm": 4.0,
        }
    ]
    if kind == "csg":
        nodes.append(
            {
                "kind": "rounded_solid",
                "node_id": "shared",
                "role": "ornament",
                "label": "shared operand",
                "size_mm": {"x": 10.0, "y": 10.0, "z": 10.0},
                "corner_radius_mm": 1.0,
            }
        )
    dependency = "base"
    for index in range(1, depth):
        node_id = f"node_{index}"
        common = {
            "node_id": node_id,
            "role": "ornament",
            "label": node_id,
            "visible": False,
        }
        if kind == "attachment":
            node = {
                **common,
                "kind": "rounded_solid",
                "size_mm": {"x": 10.0, "y": 10.0, "z": 10.0},
                "corner_radius_mm": 1.0,
                "attachment": {
                    "parent_node_id": dependency,
                    "parent_anchor": "top",
                },
            }
        elif kind == "mirror":
            node = {
                **common,
                "kind": "mirror",
                "source_node_id": dependency,
                "plane": "x",
            }
        else:
            node = {
                **common,
                "kind": "csg",
                "operation": "union",
                "operand_node_ids": [dependency, "shared"],
            }
        nodes.append(node)
        dependency = node_id
    return nodes


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


def test_character_spec_collections_are_deeply_immutable() -> None:
    spec = CharacterRobotSpec.model_validate(_spec_payload())

    collections = (
        spec.appearance.style_tags,
        spec.morphology.nodes,
        spec.morphology.nodes[1].sections,
        spec.face.supported_expressions,
        spec.behavior.scenarios,
        spec.behavior.scenarios[0].keyframes,
    )
    assert all(isinstance(value, tuple) for value in collections)
    with pytest.raises(AttributeError):
        spec.morphology.nodes.append(spec.morphology.nodes[0])  # type: ignore[attr-defined]


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

    csg_cycle = copy.deepcopy(payload)
    csg_cycle["nodes"].extend(
        [
            {
                "node_id": "csg_a",
                "role": "ornament",
                "label": "CSG A",
                "kind": "csg",
                "operation": "union",
                "operand_node_ids": ["csg_b", "body"],
                "attachment": None,
                "visible": False,
            },
            {
                "node_id": "csg_b",
                "role": "ornament",
                "label": "CSG B",
                "kind": "csg",
                "operation": "union",
                "operand_node_ids": ["csg_a", "head"],
                "attachment": None,
                "visible": False,
            },
        ]
    )
    with pytest.raises(ValidationError, match="cycle"):
        MorphologyGraph.model_validate(csg_cycle)

    radius = copy.deepcopy(payload)
    radius["nodes"][0]["corner_radius_mm"] = 40.0
    with pytest.raises(ValidationError, match="shortest side"):
        MorphologyGraph.model_validate(radius)


@pytest.mark.parametrize("kind", ["attachment", "mirror", "csg"])
def test_morphology_rejects_leaf_first_dependency_chains_deeper_than_eight(
    kind: str,
) -> None:
    nodes = _leaf_first_dependency_nodes(kind, depth=9)

    with pytest.raises(ValidationError, match="dependency depth exceeds 8"):
        MorphologyGraph.model_validate({"nodes": nodes})


def test_morphology_accepts_shared_dag_at_dependency_depth_limit() -> None:
    nodes = _leaf_first_dependency_nodes("csg", depth=8)

    graph = MorphologyGraph.model_validate({"nodes": nodes})

    assert graph.nodes[-1].node_id == "node_7"


def test_behavior_timeline_must_start_at_zero_and_be_ascending() -> None:
    payload = _spec_payload()
    payload["behavior"]["scenarios"][0]["keyframes"][0]["at_ms"] = 700
    with pytest.raises(ValidationError, match="start at 0"):
        CharacterRobotSpec.model_validate(payload)
