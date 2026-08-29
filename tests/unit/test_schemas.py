import json

import pytest
from pydantic import ValidationError

from asset_autopsy.schemas import (
    AxisPatch,
    CreateRevisionInput,
    OpenCaseInput,
    ScalarPatch,
    TOOL_INPUT_MODELS,
    TOOL_OUTPUT_MODELS,
)


def test_the_public_surface_has_exactly_seven_input_and_output_models() -> None:
    assert len(TOOL_INPUT_MODELS) == 7
    assert len(TOOL_OUTPUT_MODELS) == 7
    assert [model.__name__ for model in TOOL_INPUT_MODELS] == [
        "OpenCaseInput",
        "InspectAssetInput",
        "RunTaskInput",
        "RunProbeInput",
        "CreateRevisionInput",
        "VerifyRevisionInput",
        "PublishRevisionInput",
    ]


def test_unknown_fields_are_rejected_at_the_top_level_and_in_a_patch() -> None:
    with pytest.raises(ValidationError):
        OpenCaseInput.model_validate({"case_id": "case_demo", "private": "value"})
    with pytest.raises(ValidationError):
        AxisPatch.model_validate(
            {
                "target": {"kind": "joint", "name": "elbow"},
                "attribute": "axis",
                "expected_old_value": [0, 0, 1],
                "new_value": [1, 0, 0],
                "private": "value",
            }
        )


def test_numbers_are_strict_and_finite() -> None:
    with pytest.raises(ValidationError):
        ScalarPatch.model_validate(
            {
                "target": {"kind": "joint", "name": "elbow"},
                "attribute": "damping",
                "expected_old_value": "0.3",
                "new_value": 0.5,
            }
        )
    with pytest.raises(ValidationError):
        ScalarPatch.model_validate(
            {
                "target": {"kind": "joint", "name": "elbow"},
                "attribute": "damping",
                "expected_old_value": 0.3,
                "new_value": float("nan"),
            }
        )


def test_patch_and_basis_probe_are_single_objects() -> None:
    with pytest.raises(ValidationError):
        CreateRevisionInput.model_validate(
            {
                "case_id": "case_demo",
                "base_revision_id": "r000",
                "expected_base_sha256": "0" * 64,
                "basis_hypothesis_id": "hyp_demo",
                "basis_probe_run_id": ["run_probe_001"],
                "patch": {
                    "target": {"kind": "joint", "name": "elbow"},
                    "attribute": "damping",
                    "expected_old_value": 0.3,
                    "new_value": 0.5,
                },
                "rationale": "The probe separates the proposed change.",
                "expected_effect": {
                    "scenario_id": "public_center",
                    "predicates": [{"metric": "hold_error_p95_m", "op": "lte", "value": 0.03}],
                },
            }
        )
    with pytest.raises(ValidationError):
        CreateRevisionInput.model_validate(
            {
                "case_id": "case_demo",
                "base_revision_id": "r000",
                "expected_base_sha256": "0" * 64,
                "basis_hypothesis_id": "hyp_demo",
                "basis_probe_run_id": "run_probe_001",
                "patch": [
                    {
                        "target": {"kind": "joint", "name": "elbow"},
                        "attribute": "damping",
                        "expected_old_value": 0.3,
                        "new_value": 0.5,
                    }
                ],
                "rationale": "The probe separates the proposed change.",
                "expected_effect": {
                    "scenario_id": "public_center",
                    "predicates": [{"metric": "hold_error_p95_m", "op": "lte", "value": 0.03}],
                },
            }
        )


def test_axis_is_normalized_and_family_ranges_are_enforced() -> None:
    axis = AxisPatch.model_validate(
        {
            "target": {"kind": "joint", "name": "elbow"},
            "attribute": "axis",
            "expected_old_value": [0, 0, 2],
            "new_value": [3, 0, 0],
        }
    )
    assert axis.expected_old_value == (0.0, 0.0, 1.0)
    assert axis.new_value == (1.0, 0.0, 0.0)

    with pytest.raises(ValidationError):
        AxisPatch.model_validate(
            {
                "target": {"kind": "joint", "name": "elbow"},
                "attribute": "axis",
                "expected_old_value": [0, 0, 0],
                "new_value": [1, 0, 0],
            }
        )
    with pytest.raises(ValidationError):
        ScalarPatch.model_validate(
            {
                "target": {"kind": "joint", "name": "elbow"},
                "attribute": "armature",
                "expected_old_value": 0.01,
                "new_value": -0.1,
            }
        )
    with pytest.raises(ValidationError):
        ScalarPatch.model_validate(
            {
                "target": {"kind": "joint", "name": "elbow"},
                "attribute": "frictionloss",
                "expected_old_value": 0.01,
                "new_value": 100.1,
            }
        )


def test_schema_has_no_private_contract_fields() -> None:
    schema = json.dumps(
        [model.model_json_schema() for model in TOOL_INPUT_MODELS + TOOL_OUTPUT_MODELS],
        sort_keys=True,
    ).lower()
    assert "fault_label" not in schema
    assert "golden_value" not in schema
    assert "seed" not in schema
    assert "slot_name" not in schema
