import json

import pytest
from pydantic import ValidationError

from asset_autopsy.schemas import (
    AxisPatch,
    AggregateResult,
    BehaviorDiff,
    CreateRevisionInput,
    OpenCaseInput,
    PublicEventSummary,
    RunTaskOutput,
    ScalarPatch,
    RunProbeOutput,
    VerifyRevisionOutput,
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


def test_run_probe_output_supplies_both_revision_basis_identifiers() -> None:
    payload = _run_probe_output_payload()
    payload["trace"] = [
        _analysis_trace_point(index) for index in range(256)
    ]
    run = RunProbeOutput.model_validate(payload)
    revision = CreateRevisionInput.model_validate(
        {
            "case_id": run.case_id,
            "base_revision_id": run.revision_id,
            "expected_base_sha256": "0" * 64,
            "basis_hypothesis_id": run.hypothesis_id,
            "basis_probe_run_id": run.run_id,
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
    assert revision.basis_hypothesis_id == "hyp_demo"
    assert revision.basis_probe_run_id == "run_demo"


def _run_probe_output_payload() -> dict[str, object]:
    return {
        "schema_version": "asset-autopsy/v1",
        "request_id": "req_demo",
        "case_id": "case_demo",
        "event_ids": [],
        "warnings": [],
        "artifacts": [],
        "revision_id": "r000",
        "hypothesis_id": "hyp_demo",
        "run_id": "run_demo",
        "prediction_matched": True,
        "falsifier_triggered": False,
        "inconclusive": False,
        "conflicting": False,
        "observations": [{"metric": "abs_ee_dz_m", "value": 0.0}],
    }


@pytest.mark.parametrize("trace", [[], [{"time_s": 0.0, "values": (0.0,)}] * 255])
def test_run_probe_output_rejects_incomplete_analysis_trace(trace: list[dict[str, object]]) -> None:
    payload = _run_probe_output_payload()
    payload["trace"] = trace
    with pytest.raises(ValidationError):
        RunProbeOutput.model_validate(payload)


def test_run_probe_output_requires_analysis_trace() -> None:
    with pytest.raises(ValidationError):
        RunProbeOutput.model_validate(_run_probe_output_payload())


def _analysis_trace_point(index: int, *, time_s: float | None = None) -> dict[str, object]:
    return {
        "time_s": float(index) if time_s is None else time_s,
        "qpos": (0.0, 0.1),
        "qvel": (0.0, 0.2),
        "control": (0.0,),
        "end_effector_xyz": (0.0, 0.1, 0.2),
    }


def test_run_probe_output_rejects_inconsistent_analysis_trace_rows() -> None:
    payload = _run_probe_output_payload()
    payload["trace"] = [_analysis_trace_point(index) for index in range(256)]
    payload["trace"][12]["qvel"] = (0.0,)
    with pytest.raises(ValidationError):
        RunProbeOutput.model_validate(payload)


def test_run_probe_output_rejects_nonuniform_analysis_trace_timestamps() -> None:
    payload = _run_probe_output_payload()
    payload["trace"] = [
        _analysis_trace_point(index, time_s=float(index) + (0.1 if index > 128 else 0.0))
        for index in range(256)
    ]
    with pytest.raises(ValidationError):
        RunProbeOutput.model_validate(payload)


def test_behavior_diff_encodes_frozen_comparison_evidence() -> None:
    behavior_diff = BehaviorDiff.model_validate(
        {
            "changed": True,
            "first_divergence": {
                "step": 12,
                "time_s": 0.12,
                "signal": "qpos",
                "magnitude": 0.002,
            },
            "metric_deltas": [
                {"metric": "hold_error_p95_m", "before": 0.04, "after": 0.02, "delta": -0.02}
            ],
            "clause_outcomes": [{"clause_id": "hold_error", "outcome": "improved"}],
            "verdict": "improved",
        }
    )
    assert behavior_diff.first_divergence is not None
    assert behavior_diff.metric_deltas[0].delta == -0.02
    assert behavior_diff.clause_outcomes[0].outcome == "improved"

    with pytest.raises(ValidationError):
        behavior_diff.model_validate({"changed": True, "verdict": "unknown"})

    with pytest.raises(ValidationError):
        BehaviorDiff.model_validate(
            {
                "changed": True,
                "metric_deltas": [
                    {"metric": "hold_error_p95_m", "before": 0.04, "after": 0.02, "delta": -0.02}
                ],
                "clause_outcomes": [{"clause_id": "hold_error", "outcome": "improved"}],
                "verdict": "improved",
            }
        )


def test_behavior_diff_rejects_false_metric_deltas_and_contradictory_state() -> None:
    with pytest.raises(ValidationError):
        BehaviorDiff.model_validate(
            {
                "changed": True,
                "first_divergence": {
                    "step": 12,
                    "time_s": 0.12,
                    "signal": "qpos",
                    "magnitude": 0.002,
                },
                "metric_deltas": [
                    {"metric": "hold_error_p95_m", "before": 0.04, "after": 0.02, "delta": 0.02}
                ],
                "clause_outcomes": [{"clause_id": "hold_error", "outcome": "improved"}],
                "verdict": "improved",
            }
        )

    with pytest.raises(ValidationError):
        BehaviorDiff.model_validate(
            {
                "changed": False,
                "first_divergence": {
                    "step": 12,
                    "time_s": 0.12,
                    "signal": "qpos",
                    "magnitude": 0.002,
                },
                "metric_deltas": [
                    {"metric": "hold_error_p95_m", "before": 0.04, "after": 0.04, "delta": 0.0}
                ],
                "clause_outcomes": [{"clause_id": "hold_error", "outcome": "unchanged"}],
                "verdict": "public_pass",
            }
        )

    with pytest.raises(ValidationError):
        BehaviorDiff.model_validate(
            {
                "changed": False,
                "metric_deltas": [
                    {"metric": "hold_error_p95_m", "before": 0.04, "after": 0.04, "delta": 0.0}
                ],
                "clause_outcomes": [{"clause_id": "hold_error", "outcome": "unchanged"}],
                "verdict": "improved",
            }
        )


def test_run_task_output_accepts_behavior_diff_evidence() -> None:
    RunTaskOutput.model_validate(
        {
            "schema_version": "asset-autopsy/v1",
            "request_id": "req_demo",
            "case_id": "case_demo",
            "event_ids": [],
            "warnings": [],
            "artifacts": [],
            "revision_id": "r000",
            "scenario_id": "public_center",
            "result": "pass",
            "observations": [{"metric": "hold_error_p95_m", "value": 0.02}],
            "behavior_diff": {
                "changed": False,
                "metric_deltas": [
                    {"metric": "hold_error_p95_m", "before": 0.02, "after": 0.02, "delta": 0.0}
                ],
                "clause_outcomes": [{"clause_id": "hold_error", "outcome": "unchanged"}],
                "verdict": "public_pass",
            },
        }
    )

    with pytest.raises(ValidationError):
        RunTaskOutput.model_validate(
            {
                "schema_version": "asset-autopsy/v1",
                "request_id": "req_demo",
                "case_id": "case_demo",
                "revision_id": "r000",
                "scenario_id": "public_center",
                "result": "fail",
                "observations": [{"metric": "hold_error_p95_m", "value": 0.02}],
                "behavior_diff": {
                    "changed": False,
                    "metric_deltas": [
                        {"metric": "hold_error_p95_m", "before": 0.02, "after": 0.02, "delta": 0.0}
                    ],
                    "clause_outcomes": [{"clause_id": "hold_error", "outcome": "unchanged"}],
                    "verdict": "public_pass",
                },
            }
        )

    RunTaskOutput.model_validate(
        {
            "schema_version": "asset-autopsy/v1",
            "request_id": "req_demo",
            "case_id": "case_demo",
            "revision_id": "r000",
            "scenario_id": "public_center",
            "result": "fail",
            "observations": [{"metric": "hold_error_p95_m", "value": 0.02}],
            "behavior_diff": {
                "changed": False,
                "metric_deltas": [
                    {"metric": "hold_error_p95_m", "before": 0.02, "after": 0.02, "delta": 0.0}
                ],
                "clause_outcomes": [{"clause_id": "hold_error", "outcome": "unchanged"}],
                "verdict": "changed",
            },
        }
    )


def test_metric_observation_allows_only_nullable_settling_time() -> None:
    RunTaskOutput.model_validate(
        {
            "schema_version": "asset-autopsy/v1",
            "request_id": "req_demo",
            "case_id": "case_demo",
            "revision_id": "r000",
            "scenario_id": "public_center",
            "result": "fail",
            "observations": [
                {"metric": "settling_time_s", "value": None},
                {"metric": "hold_error_p95_m", "value": 0.02},
            ],
        }
    )

    with pytest.raises(ValidationError):
        RunTaskOutput.model_validate(
            {
                "schema_version": "asset-autopsy/v1",
                "request_id": "req_demo",
                "case_id": "case_demo",
                "revision_id": "r000",
                "scenario_id": "public_center",
                "result": "fail",
                "observations": [{"metric": "hold_error_p95_m", "value": None}],
            }
        )
    with pytest.raises(ValidationError):
        RunTaskOutput.model_validate(
            {
                "schema_version": "asset-autopsy/v1",
                "request_id": "req_demo",
                "case_id": "case_demo",
                "revision_id": "r000",
                "scenario_id": "public_center",
                "result": "fail",
                "observations": [{"metric": "settling_time_s", "value": float("nan")}],
            }
        )


def _verify_revision_payload() -> dict[str, object]:
    return {
        "schema_version": "asset-autopsy/v1",
        "request_id": "req_demo",
        "case_id": "case_demo",
        "revision_id": "r001",
        "asset_sha256": "1" * 64,
        "integrity": {
            "original": True,
            "controller": True,
            "contract": True,
            "runner": True,
            "lineage": True,
        },
        "public_result": {"passed": 1, "total": 1},
        "holdout_result": {"passed": 3, "total": 3},
    }


def _promotion_ticket_payload() -> dict[str, object]:
    return {
        "ticket_id": "evt_ticket",
        "case_id": "case_demo",
        "revision_id": "r001",
        "asset_sha256": "1" * 64,
        "canonical_diff": [
            {"target": "elbow", "attribute": "damping", "before": "0.3", "after": "0.5"}
        ],
        "public_result": {"passed": 1, "total": 1},
        "holdout_result": {"passed": 3, "total": 3},
        "export_name": "repaired-asset",
        "qualified_core_sha256": "2" * 64,
        "ticket_digest": "3" * 64,
    }


def test_verify_revision_requires_successful_bound_promotion_ticket() -> None:
    payload = _verify_revision_payload()
    payload["promotion_ticket"] = _promotion_ticket_payload()
    output = VerifyRevisionOutput.model_validate(payload)
    assert output.promotion_ticket is not None

    failed = _verify_revision_payload()
    failed["public_result"] = {"passed": 0, "total": 1}
    failed["promotion_ticket"] = _promotion_ticket_payload()
    with pytest.raises(ValidationError):
        VerifyRevisionOutput.model_validate(failed)

    for field, value in (
        ("case_id", "case_other"),
        ("revision_id", "r002"),
        ("asset_sha256", "4" * 64),
    ):
        mismatched = _verify_revision_payload()
        ticket = _promotion_ticket_payload()
        ticket[field] = value
        mismatched["promotion_ticket"] = ticket
        with pytest.raises(ValidationError):
            VerifyRevisionOutput.model_validate(mismatched)


def test_verify_revision_rejects_promotion_ticket_count_mismatches() -> None:
    for result_name, result in (
        ("public_result", {"passed": 0, "total": 1}),
        ("holdout_result", {"passed": 2, "total": 3}),
    ):
        mismatched = _verify_revision_payload()
        mismatched["promotion_ticket"] = _promotion_ticket_payload()
        ticket = mismatched["promotion_ticket"]
        assert isinstance(ticket, dict)
        ticket[result_name] = result
        with pytest.raises(ValidationError):
            VerifyRevisionOutput.model_validate(mismatched)


def test_public_event_tail_accepts_hypothesis_preregistration() -> None:
    event = PublicEventSummary.model_validate(
        {"event_id": "evt_demo", "kind": "HYPOTHESIS_RECORDED", "summary": "Hypothesis recorded."}
    )
    assert event.kind == "HYPOTHESIS_RECORDED"


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


def test_aggregate_result_rejects_passed_above_total_regardless_of_field_order() -> None:
    with pytest.raises(ValidationError):
        AggregateResult.model_validate({"passed": 4, "total": 3})


def test_schema_has_no_private_contract_fields() -> None:
    schema = json.dumps(
        [model.model_json_schema() for model in TOOL_INPUT_MODELS + TOOL_OUTPUT_MODELS],
        sort_keys=True,
    ).lower()
    assert "fault_label" not in schema
    assert "golden_value" not in schema
    assert "seed" not in schema
    assert "slot_name" not in schema
