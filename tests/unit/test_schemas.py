import json

import pytest
from pydantic import ValidationError

from asset_autopsy.schemas import (
    ArtifactRef,
    AxisPatch,
    AggregateResult,
    BehaviorDiff,
    CreateRevisionInput,
    FirstDivergence,
    OpenCaseOutput,
    OpenCaseInput,
    PatchPolicy,
    PublishRevisionInput,
    PublishRevisionOutput,
    PublicEventSummary,
    RunTaskOutput,
    ScalarPatch,
    RunProbeOutput,
    RevisionSummary,
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


def test_patch_policy_requires_axis_unit_vectors() -> None:
    with pytest.raises(ValidationError):
        PatchPolicy.model_validate(
            {
                "editable_attributes": ("axis", "damping", "armature", "frictionloss"),
                "axis_unit_vector": False,
                "damping": {"minimum": 0.0, "maximum": 100.0},
                "armature": {"minimum": 0.0, "maximum": 10.0},
                "frictionloss": {"minimum": 0.0, "maximum": 100.0},
            }
        )


@pytest.mark.parametrize(
    ("attribute", "minimum", "maximum"),
    [
        ("damping", 0.0, 99.0),
        ("damping", 1.0, 100.0),
        ("armature", 0.0, 9.0),
        ("armature", 1.0, 10.0),
        ("frictionloss", 0.0, 99.0),
        ("frictionloss", 1.0, 100.0),
    ],
)
def test_patch_policy_ranges_match_scalar_patch_limits(
    attribute: str, minimum: float, maximum: float
) -> None:
    payload = {
        "editable_attributes": ("axis", "damping", "armature", "frictionloss"),
        "axis_unit_vector": True,
        "damping": {"minimum": 0.0, "maximum": 100.0},
        "armature": {"minimum": 0.0, "maximum": 10.0},
        "frictionloss": {"minimum": 0.0, "maximum": 100.0},
    }
    payload[attribute] = {"minimum": minimum, "maximum": maximum}

    with pytest.raises(ValidationError):
        PatchPolicy.model_validate(payload)


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


@pytest.mark.parametrize(
    ("prediction_matched", "falsifier_triggered", "inconclusive", "conflicting"),
    [
        (True, True, False, False),
        (True, True, True, True),
        (False, False, False, False),
        (False, False, True, True),
    ],
)
def test_run_probe_output_rejects_contradictory_predicate_state(
    prediction_matched: bool,
    falsifier_triggered: bool,
    inconclusive: bool,
    conflicting: bool,
) -> None:
    payload = _run_probe_output_payload()
    payload.update(
        {
            "prediction_matched": prediction_matched,
            "falsifier_triggered": falsifier_triggered,
            "inconclusive": inconclusive,
            "conflicting": conflicting,
            "trace": [_analysis_trace_point(index) for index in range(256)],
        }
    )
    with pytest.raises(ValidationError):
        RunProbeOutput.model_validate(payload)


def _metric_deltas(
    *,
    hold_before: float = 0.04,
    hold_after: float = 0.02,
    settling_before: float | None = 1.5,
    settling_after: float | None = 1.5,
) -> list[dict[str, object]]:
    endpoints = [
        ("hold_error_p95_m", hold_before, hold_after),
        ("final_target_error_m", 0.01, 0.01),
        ("joint_speed_rms_rad_s", 0.03, 0.03),
        ("settling_time_s", settling_before, settling_after),
        ("peak_energy_j", 0.04, 0.04),
        ("joint_limit_violation_count", 0.0, 0.0),
        ("non_finite_count", 0.0, 0.0),
    ]
    return [
        {
            "metric": metric,
            "before": before,
            "after": after,
            "delta": None if before is None or after is None else after - before,
        }
        for metric, before, after in endpoints
    ]


def _clause_outcomes(**overrides: str) -> list[dict[str, str]]:
    outcomes = {
        "reach_error": "improved",
        "stable_hold": "unchanged",
        "settling": "unchanged",
        "finite_state": "unchanged",
        "joint_limits": "unchanged",
    }
    outcomes.update(overrides)
    return [
        {"clause_id": clause_id, "outcome": outcome}
        for clause_id, outcome in outcomes.items()
    ]


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
            "metric_deltas": _metric_deltas(),
            "clause_outcomes": _clause_outcomes(),
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
                "metric_deltas": _metric_deltas(),
                "clause_outcomes": _clause_outcomes(),
                "verdict": "improved",
            }
        )


def test_behavior_diff_rejects_false_metric_deltas_and_contradictory_state() -> None:
    false_deltas = _metric_deltas()
    false_deltas[0]["delta"] = 0.02
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
                "metric_deltas": false_deltas,
                "clause_outcomes": _clause_outcomes(),
                "verdict": "improved",
            }
        )

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
                "metric_deltas": _metric_deltas(),
                "clause_outcomes": _clause_outcomes(),
                "verdict": "unchanged_failure",
            }
        )


@pytest.mark.parametrize(
    "metric_deltas",
    [
        _metric_deltas()[:-1],
        [*_metric_deltas()[:-1], _metric_deltas()[0]],
    ],
)
def test_behavior_diff_requires_each_fixed_metric_exactly_once(
    metric_deltas: list[dict[str, object]],
) -> None:
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
                "metric_deltas": metric_deltas,
                "clause_outcomes": _clause_outcomes(),
                "verdict": "improved",
            }
        )


@pytest.mark.parametrize(
    "clause_outcomes",
    [
        _clause_outcomes()[:-1],
        [*_clause_outcomes()[:-1], _clause_outcomes()[0]],
        [
            *_clause_outcomes()[:-1],
            {"clause_id": "not_a_contract_clause", "outcome": "regressed"},
        ],
    ],
)
def test_behavior_diff_requires_each_fixed_clause_exactly_once(
    clause_outcomes: list[dict[str, str]],
) -> None:
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
                "metric_deltas": _metric_deltas(),
                "clause_outcomes": clause_outcomes,
                "verdict": "improved",
            }
        )


@pytest.mark.parametrize(
    ("metric", "before", "after"),
    [
        ("final_target_error_m", -1.0, -1.0),
        ("joint_limit_violation_count", 0.5, 0.0),
    ],
)
def test_behavior_diff_endpoints_obey_metric_domains(
    metric: str, before: float, after: float
) -> None:
    metric_deltas = _metric_deltas()
    for delta in metric_deltas:
        if delta["metric"] == metric:
            delta.update({"before": before, "after": after, "delta": after - before})
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
                "metric_deltas": metric_deltas,
                "clause_outcomes": _clause_outcomes(),
                "verdict": "improved",
            }
        )


def test_behavior_diff_clause_outcomes_match_metric_direction() -> None:
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
                "metric_deltas": _metric_deltas(hold_before=0.02, hold_after=0.04),
                "clause_outcomes": _clause_outcomes(),
                "verdict": "changed",
            }
        )


@pytest.mark.parametrize(
    "payload",
    [
        {"step": 1, "time_s": 0.1, "signal": "invented", "magnitude": 1.0},
        {"step": 1, "time_s": 0.1, "signal": "qpos", "magnitude": 1e-4},
        {"step": 1, "time_s": 0.1, "signal": "qvel", "magnitude": 1e-3},
    ],
)
def test_first_divergence_requires_a_supported_threshold_crossing(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        FirstDivergence.model_validate(payload)


def test_behavior_diff_represents_nullable_settling_time_transitions() -> None:
    behavior_diff = BehaviorDiff.model_validate(
        {
            "changed": True,
            "first_divergence": {
                "step": 12,
                "time_s": 0.12,
                "signal": "qvel",
                "magnitude": 0.002,
            },
            "metric_deltas": _metric_deltas(hold_after=0.04, settling_before=None),
            "clause_outcomes": _clause_outcomes(reach_error="unchanged", settling="improved"),
            "verdict": "improved",
        }
    )
    settling_delta = next(
        delta for delta in behavior_diff.metric_deltas if delta.metric == "settling_time_s"
    )
    assert settling_delta.before is None
    assert settling_delta.delta is None

    invalid = behavior_diff.model_dump()
    invalid_deltas = _metric_deltas()
    invalid_deltas[0] = {
        "metric": "hold_error_p95_m",
        "before": None,
        "after": 0.02,
        "delta": None,
    }
    invalid["metric_deltas"] = invalid_deltas
    with pytest.raises(ValidationError):
        BehaviorDiff.model_validate(invalid)

    with pytest.raises(ValidationError):
        BehaviorDiff.model_validate(
            {
                "changed": False,
                "metric_deltas": _metric_deltas(hold_after=0.04),
                "clause_outcomes": _clause_outcomes(reach_error="unchanged"),
                "verdict": "changed",
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
                "metric_deltas": _metric_deltas(hold_after=0.04),
                "clause_outcomes": _clause_outcomes(reach_error="unchanged"),
                "verdict": "public_pass",
            }
        )

    with pytest.raises(ValidationError):
        BehaviorDiff.model_validate(
            {
                "changed": False,
                "metric_deltas": _metric_deltas(hold_after=0.04),
                "clause_outcomes": _clause_outcomes(reach_error="unchanged"),
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
            "revision_id": "r001",
            "scenario_id": "public_center",
            "result": "pass",
            "observations": _run_task_observations(hold_error_p95_m=0.02),
            "behavior_diff": {
                "changed": False,
                "metric_deltas": _metric_deltas(hold_before=0.02),
                "clause_outcomes": _clause_outcomes(reach_error="unchanged"),
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
                "revision_id": "r001",
                "scenario_id": "public_center",
                "result": "fail",
                "observations": _run_task_observations(),
                "behavior_diff": {
                    "changed": False,
                    "metric_deltas": _metric_deltas(hold_before=0.02),
                    "clause_outcomes": _clause_outcomes(reach_error="unchanged"),
                    "verdict": "public_pass",
                },
            }
        )

    RunTaskOutput.model_validate(
        {
            "schema_version": "asset-autopsy/v1",
            "request_id": "req_demo",
            "case_id": "case_demo",
            "revision_id": "r001",
            "scenario_id": "public_center",
            "result": "fail",
            "observations": _run_task_observations(),
            "behavior_diff": {
                "changed": False,
                "metric_deltas": _metric_deltas(hold_after=0.04),
                "clause_outcomes": _clause_outcomes(reach_error="unchanged"),
                "verdict": "unchanged_failure",
            },
        }
    )

    child_without_diff = {
        "schema_version": "asset-autopsy/v1",
        "request_id": "req_demo",
        "case_id": "case_demo",
        "revision_id": "r001",
        "scenario_id": "public_center",
        "result": "fail",
        "observations": _run_task_observations(),
    }
    with pytest.raises(ValidationError):
        RunTaskOutput.model_validate(child_without_diff)

    child_with_unchanged_failure = {
        **child_without_diff,
        "behavior_diff": {
            "changed": False,
            "metric_deltas": _metric_deltas(hold_after=0.04),
            "clause_outcomes": _clause_outcomes(reach_error="unchanged"),
            "verdict": "unchanged_failure",
        },
    }
    RunTaskOutput.model_validate(child_with_unchanged_failure)

    root_with_diff = {**child_with_unchanged_failure, "revision_id": "r000"}
    with pytest.raises(ValidationError):
        RunTaskOutput.model_validate(root_with_diff)


def test_run_task_output_rejects_changed_public_pass_verdict_on_failure() -> None:
    payload = {
        "schema_version": "asset-autopsy/v1",
        "request_id": "req_demo",
        "case_id": "case_demo",
        "revision_id": "r001",
        "scenario_id": "public_center",
        "result": "fail",
        "observations": _run_task_observations(),
        "behavior_diff": {
            "changed": True,
            "first_divergence": {
                "step": 12,
                "time_s": 0.12,
                "signal": "qpos",
                "magnitude": 0.002,
            },
            "metric_deltas": _metric_deltas(),
            "clause_outcomes": _clause_outcomes(),
            "verdict": "public_pass",
        },
    }
    with pytest.raises(ValidationError):
        RunTaskOutput.model_validate(payload)


def test_run_task_output_binds_behavior_diff_after_values_to_observations() -> None:
    with pytest.raises(ValidationError):
        RunTaskOutput.model_validate(
            {
                "schema_version": "asset-autopsy/v1",
                "request_id": "req_demo",
                "case_id": "case_demo",
                "revision_id": "r001",
                "scenario_id": "public_center",
                "result": "pass",
                "observations": _run_task_observations(hold_error_p95_m=0.02),
                "behavior_diff": {
                    "changed": True,
                    "first_divergence": {
                        "step": 12,
                        "time_s": 0.12,
                        "signal": "qpos",
                        "magnitude": 0.002,
                    },
                    "metric_deltas": _metric_deltas(hold_after=0.99),
                    "clause_outcomes": _clause_outcomes(reach_error="regressed"),
                    "verdict": "public_pass",
                },
            }
        )


def _run_task_observations(
    *, settling_time_s: float | None = 1.5, hold_error_p95_m: float = 0.04
) -> list[dict[str, object]]:
    return [
        {"metric": "final_target_error_m", "value": 0.01},
        {"metric": "hold_error_p95_m", "value": hold_error_p95_m},
        {"metric": "joint_speed_rms_rad_s", "value": 0.03},
        {"metric": "settling_time_s", "value": settling_time_s},
        {"metric": "peak_energy_j", "value": 0.04},
        {"metric": "joint_limit_violation_count", "value": 0.0},
        {"metric": "non_finite_count", "value": 0.0},
    ]


def test_run_task_output_requires_each_fixed_metric_exactly_once() -> None:
    base = {
        "schema_version": "asset-autopsy/v1",
        "request_id": "req_demo",
        "case_id": "case_demo",
        "revision_id": "r000",
        "scenario_id": "public_center",
        "result": "fail",
    }
    observations = _run_task_observations()
    invalid_observations = (
        observations[:-1],
        [*observations, observations[0]],
        [*observations[:-1], {"metric": "unexpected_metric", "value": 0.0}],
    )
    for invalid in invalid_observations:
        with pytest.raises(ValidationError):
            RunTaskOutput.model_validate({**base, "observations": invalid})


def test_verify_revision_rejects_violated_clauses_as_successful_qualification() -> None:
    for result_name in ("public_result", "holdout_result"):
        payload = _verify_revision_payload()
        payload[result_name] = {"passed": 1 if result_name == "public_result" else 3,
                                "total": 1 if result_name == "public_result" else 3,
                                "violated_clause_ids": ["hold_error"]}
        ticket = _promotion_ticket_payload()
        ticket[result_name] = payload[result_name]
        payload["promotion_ticket"] = ticket
        with pytest.raises(ValidationError):
            VerifyRevisionOutput.model_validate(payload)


def test_verify_revision_requires_exactly_one_public_scenario() -> None:
    payload = _verify_revision_payload()
    payload["public_result"] = {"passed": 2, "total": 2}

    with pytest.raises(ValidationError):
        VerifyRevisionOutput.model_validate(payload)


def test_run_task_output_rejects_nonuniform_trace_timestamps() -> None:
    base = {
        "schema_version": "asset-autopsy/v1",
        "request_id": "req_demo",
        "case_id": "case_demo",
        "revision_id": "r000",
        "scenario_id": "public_center",
        "result": "fail",
        "observations": _run_task_observations(),
    }
    valid = {**base, "trace": [{"time_s": float(index), "values": (0.0,)} for index in range(3)]}
    RunTaskOutput.model_validate(valid)

    for timestamps in ((0.0, 0.0, 1.0), (0.0, 2.0, 1.0), (0.0, 1.0, 3.0)):
        invalid = {
            **base,
            "trace": [{"time_s": time_s, "values": (0.0,)} for time_s in timestamps],
        }
        with pytest.raises(ValidationError):
            RunTaskOutput.model_validate(invalid)


def test_metric_observation_allows_only_nullable_settling_time() -> None:
    RunTaskOutput.model_validate(
        {
            "schema_version": "asset-autopsy/v1",
            "request_id": "req_demo",
            "case_id": "case_demo",
            "revision_id": "r000",
            "scenario_id": "public_center",
            "result": "fail",
            "observations": _run_task_observations(settling_time_s=None),
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
                "observations": [
                    {**observation, "value": None}
                    if observation["metric"] == "hold_error_p95_m"
                    else observation
                    for observation in _run_task_observations()
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
                "observations": [
                    {**observation, "value": float("nan")}
                    if observation["metric"] == "settling_time_s"
                    else observation
                    for observation in _run_task_observations()
                ],
            }
        )


@pytest.mark.parametrize(
    ("metric", "value"),
    [
        ("hold_error_p95_m", 0.031),
        ("joint_speed_rms_rad_s", 0.051),
        ("settling_time_s", None),
        ("settling_time_s", 2.1),
        ("joint_limit_violation_count", 1.0),
        ("non_finite_count", 1.0),
    ],
)
def test_run_task_output_rejects_passes_that_violate_fixed_clauses(
    metric: str, value: float | None
) -> None:
    observations = [
        {**observation, "value": value} if observation["metric"] == metric else observation
        for observation in _run_task_observations(hold_error_p95_m=0.02)
    ]
    with pytest.raises(ValidationError):
        RunTaskOutput.model_validate(
            {
                "schema_version": "asset-autopsy/v1",
                "request_id": "req_demo",
                "case_id": "case_demo",
                "revision_id": "r000",
                "scenario_id": "public_center",
                "result": "pass",
                "observations": observations,
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


def test_publish_input_requires_a_successful_same_case_ticket() -> None:
    ticket = _promotion_ticket_payload()
    PublishRevisionInput.model_validate(
        {"case_id": "case_demo", "promotion_ticket": ticket}
    )

    with pytest.raises(ValidationError):
        PublishRevisionInput.model_validate(
            {"case_id": "case_other", "promotion_ticket": ticket}
        )

    failed_ticket = _promotion_ticket_payload()
    failed_ticket["public_result"] = {
        "passed": 0,
        "total": 1,
        "violated_clause_ids": ["reach_error"],
    }
    failed_ticket["holdout_result"] = {
        "passed": 2,
        "total": 3,
        "violated_clause_ids": ["reach_error"],
    }
    with pytest.raises(ValidationError):
        PublishRevisionInput.model_validate(
            {"case_id": "case_demo", "promotion_ticket": failed_ticket}
        )


def test_verify_revision_requires_successful_bound_promotion_ticket() -> None:
    payload = _verify_revision_payload()
    payload["promotion_ticket"] = _promotion_ticket_payload()
    output = VerifyRevisionOutput.model_validate(payload)
    assert output.promotion_ticket is not None

    with pytest.raises(ValidationError):
        VerifyRevisionOutput.model_validate(_verify_revision_payload())

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

    undersized = _verify_revision_payload()
    undersized["holdout_result"] = {"passed": 1, "total": 1}
    undersized["promotion_ticket"] = _promotion_ticket_payload()
    with pytest.raises(ValidationError):
        VerifyRevisionOutput.model_validate(undersized)

    incomplete_failure = _verify_revision_payload()
    incomplete_failure["public_result"] = {"passed": 0, "total": 1}
    incomplete_failure["holdout_result"] = {"passed": 0, "total": 1}
    with pytest.raises(ValidationError):
        VerifyRevisionOutput.model_validate(incomplete_failure)


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


def test_verify_revision_rejects_promotion_ticket_clause_mismatches() -> None:
    payload = _verify_revision_payload()
    payload["public_result"] = {"passed": 1, "total": 1, "violated_clause_ids": ["hold_error"]}
    payload["promotion_ticket"] = _promotion_ticket_payload()
    with pytest.raises(ValidationError):
        VerifyRevisionOutput.model_validate(payload)


def test_public_event_tail_accepts_hypothesis_preregistration() -> None:
    event = PublicEventSummary.model_validate(
        {"event_id": "evt_demo", "kind": "HYPOTHESIS_RECORDED", "summary": "Hypothesis recorded."}
    )
    assert event.kind == "HYPOTHESIS_RECORDED"


def test_public_outputs_cover_case_commitments_and_evidence_ledger() -> None:
    required = set(OpenCaseOutput.model_json_schema()["required"])
    assert {
        "original_asset_sha256",
        "controller_sha256",
        "public_contract_sha256",
        "runner_sha256",
        "holdout_commitment_sha256",
    } <= required

    artifact = ArtifactRef.model_validate(
        {
            "artifact_id": "art_ledger",
            "kind": "evidence_ledger",
            "uri": "autopsy://case_demo/art_ledger",
            "media_type": "application/jsonl",
            "sha256": "1" * 64,
            "bytes": 42,
        }
    )
    assert artifact.kind == "evidence_ledger"


def _open_case_payload() -> dict[str, object]:
    metrics = [
        "final_target_error_m",
        "hold_error_p95_m",
        "joint_speed_rms_rad_s",
        "settling_time_s",
        "peak_energy_j",
        "joint_limit_violation_count",
        "non_finite_count",
    ]
    return {
        "schema_version": "asset-autopsy/v1",
        "request_id": "req_demo",
        "case_id": "case_demo",
        "promotion_state": "open",
        "qualification_state": "unused",
        "original_revision_id": "r000",
        "original_asset_sha256": "1" * 64,
        "controller_sha256": "2" * 64,
        "public_contract_sha256": "3" * 64,
        "runner_sha256": "4" * 64,
        "holdout_commitment_sha256": "5" * 64,
        "public_scenarios": [
            {"scenario_id": "public_center", "observable_metrics": metrics}
        ],
        "contract_clauses": [
            {"clause_id": clause_id, "description": f"Fixed clause {clause_id}."}
            for clause_id in (
                "reach_error",
                "stable_hold",
                "settling",
                "finite_state",
                "joint_limits",
            )
        ],
        "compiled_dimensions": {"nq": 1, "nv": 1, "nu": 1, "timestep_s": 0.01},
        "joints": [
            {
                "name": "elbow",
                "axis": (0.0, 0.0, 1.0),
                "damping": 0.3,
                "armature": 0.01,
                "frictionloss": 0.0,
                "body_parent": "arm",
            }
        ],
        "bodies": [{"name": "arm"}],
        "actuators": [{"name": "elbow_motor", "joint_name": "elbow"}],
        "available_probe_kinds": ("joint_pulse", "pose_hold"),
        "observable_metric_names": metrics,
        "patch_policy": {
            "editable_attributes": ("axis", "damping", "armature", "frictionloss"),
            "axis_unit_vector": True,
            "damping": {"minimum": 0.0, "maximum": 100.0},
            "armature": {"minimum": 0.0, "maximum": 10.0},
            "frictionloss": {"minimum": 0.0, "maximum": 100.0},
        },
        "remaining_budgets": {
            "runs_remaining": 10,
            "probes_remaining": 5,
            "revisions_remaining": 2,
            "qualification_remaining": 1,
        },
        "revision_history": [
            {"revision_id": "r000", "asset_sha256": "1" * 64}
        ],
    }


def test_open_case_output_binds_fixed_contract_and_lifecycle() -> None:
    OpenCaseOutput.model_validate(_open_case_payload())

    promoted_unused = _open_case_payload()
    promoted_unused["promotion_state"] = "promoted"
    with pytest.raises(ValidationError):
        OpenCaseOutput.model_validate(promoted_unused)

    missing_clause = _open_case_payload()
    missing_clause["contract_clauses"] = missing_clause["contract_clauses"][:-1]
    with pytest.raises(ValidationError):
        OpenCaseOutput.model_validate(missing_clause)

    duplicate_probe = _open_case_payload()
    duplicate_probe["available_probe_kinds"] = ("joint_pulse", "joint_pulse")
    with pytest.raises(ValidationError):
        OpenCaseOutput.model_validate(duplicate_probe)

    inconsistent_budget = _open_case_payload()
    inconsistent_budget["qualification_state"] = "failed"
    with pytest.raises(ValidationError):
        OpenCaseOutput.model_validate(inconsistent_budget)


def _publication_artifact(kind: str, index: int) -> dict[str, object]:
    return {
        "artifact_id": f"art_{index}",
        "kind": kind,
        "uri": f"autopsy://case_demo/art_{index}",
        "media_type": "application/json",
        "sha256": str(index) * 64,
        "bytes": 42,
    }


def test_publish_revision_requires_the_complete_artifact_set() -> None:
    base = {
        "schema_version": "asset-autopsy/v1",
        "request_id": "req_demo",
        "case_id": "case_demo",
        "revision_id": "r001",
        "status": "published",
    }
    artifacts = [
        _publication_artifact(kind, index)
        for index, kind in enumerate(
            ("repaired_mjcf", "patch_manifest", "evidence_ledger", "qualification"),
            start=1,
        )
    ]
    PublishRevisionOutput.model_validate({**base, "artifacts": artifacts})

    with pytest.raises(ValidationError):
        PublishRevisionOutput.model_validate({**base, "artifacts": artifacts[:-1]})
    with pytest.raises(ValidationError):
        PublishRevisionOutput.model_validate(
            {**base, "artifacts": [*artifacts[:-1], artifacts[0]]}
        )


@pytest.mark.parametrize("value", [-1.0, 0.5])
def test_run_task_count_observations_are_nonnegative_integers(value: float) -> None:
    observations = [
        {**observation, "value": value}
        if observation["metric"] == "non_finite_count"
        else observation
        for observation in _run_task_observations()
    ]
    with pytest.raises(ValidationError):
        RunTaskOutput.model_validate(
            {
                "schema_version": "asset-autopsy/v1",
                "request_id": "req_demo",
                "case_id": "case_demo",
                "revision_id": "r000",
                "scenario_id": "public_center",
                "result": "fail",
                "observations": observations,
            }
        )


@pytest.mark.parametrize(
    "metric",
    [
        "final_target_error_m",
        "hold_error_p95_m",
        "joint_speed_rms_rad_s",
        "settling_time_s",
        "peak_energy_j",
    ],
)
def test_run_task_physical_observations_are_nonnegative(metric: str) -> None:
    observations = [
        {**observation, "value": -1.0} if observation["metric"] == metric else observation
        for observation in _run_task_observations()
    ]
    with pytest.raises(ValidationError):
        RunTaskOutput.model_validate(
            {
                "schema_version": "asset-autopsy/v1",
                "request_id": "req_demo",
                "case_id": "case_demo",
                "revision_id": "r000",
                "scenario_id": "public_center",
                "result": "fail",
                "observations": observations,
            }
        )


def test_run_task_output_rejects_failure_when_all_contract_clauses_pass() -> None:
    with pytest.raises(ValidationError):
        RunTaskOutput.model_validate(
            {
                "schema_version": "asset-autopsy/v1",
                "request_id": "req_demo",
                "case_id": "case_demo",
                "revision_id": "r000",
                "scenario_id": "public_center",
                "result": "fail",
                "observations": _run_task_observations(hold_error_p95_m=0.02),
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


def test_aggregate_result_rejects_passed_above_total_regardless_of_field_order() -> None:
    with pytest.raises(ValidationError):
        AggregateResult.model_validate({"passed": 4, "total": 3})


@pytest.mark.parametrize(
    "payload",
    [
        {"passed": 3, "total": 3, "violated_clause_ids": ["reach_error"]},
        {"passed": 2, "total": 3, "violated_clause_ids": []},
        {
            "passed": 2,
            "total": 3,
            "violated_clause_ids": ["reach_error", "reach_error"],
        },
        {"passed": 2, "total": 3, "violated_clause_ids": ["not_a_contract_clause"]},
    ],
)
def test_aggregate_result_binds_counts_to_unique_fixed_clause_ids(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        AggregateResult.model_validate(payload)

    AggregateResult.model_validate(
        {"passed": 2, "total": 3, "violated_clause_ids": ["reach_error"]}
    )


def test_revision_summary_requires_root_and_child_provenance() -> None:
    root = {
        "revision_id": "r000",
        "asset_sha256": "1" * 64,
    }
    child = {
        "revision_id": "r001",
        "asset_sha256": "2" * 64,
        "parent_revision_id": "r000",
        "canonical_diff": [
            {"target": "elbow", "attribute": "damping", "before": "0.3", "after": "0.5"}
        ],
    }
    RevisionSummary.model_validate(root)
    RevisionSummary.model_validate(child)

    with pytest.raises(ValidationError):
        RevisionSummary.model_validate({**root, **child, "revision_id": "r000"})
    with pytest.raises(ValidationError):
        RevisionSummary.model_validate({**child, "canonical_diff": []})


def test_schema_has_no_private_contract_fields() -> None:
    schema = json.dumps(
        [model.model_json_schema() for model in TOOL_INPUT_MODELS + TOOL_OUTPUT_MODELS],
        sort_keys=True,
    ).lower()
    assert "fault_label" not in schema
    assert "golden_value" not in schema
    assert "seed" not in schema
    assert "slot_name" not in schema
