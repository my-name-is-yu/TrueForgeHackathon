import json

import pytest
from pydantic import ValidationError

from asset_autopsy.schemas import (
    AxisPatch,
    AggregateResult,
    BehaviorDiff,
    CreateRevisionOutput,
    CreateRevisionInput,
    FirstDivergence,
    JointSummary,
    OpenCaseOutput,
    OpenCaseInput,
    PatchPolicy,
    PromotionTicket,
    PublicEventSummary,
    RunExperimentInput,
    RunExperimentOutput,
    RunTaskOutput,
    ScalarPatch,
    RevisionSummary,
    VerifyRevisionOutput,
    TOOL_INPUT_MODELS,
    TOOL_OUTPUT_MODELS,
    experiment_trace_columns,
)


def test_public_surface_has_six_inputs_and_six_success_outputs() -> None:
    assert len(TOOL_INPUT_MODELS) == 6
    assert len(TOOL_OUTPUT_MODELS) == 6
    assert [model.__name__ for model in TOOL_INPUT_MODELS] == [
        "OpenCaseInput",
        "InspectAssetInput",
        "RunTaskInput",
        "RunExperimentInput",
        "CreateRevisionInput",
        "VerifyRevisionInput",
    ]
    assert [model.__name__ for model in TOOL_OUTPUT_MODELS] == [
        "OpenCaseOutput",
        "InspectAssetOutput",
        "RunTaskOutput",
        "RunExperimentOutput",
        "CreateRevisionOutput",
        "VerifyRevisionOutput",
    ]


def test_public_case_handle_is_normalized_to_the_canonical_case_id() -> None:
    assert (
        OpenCaseInput.model_validate({"case_id": "compound-arm-01"}).case_id
        == "case_compound-arm-01"
    )
    assert (
        OpenCaseInput.model_validate({"case_id": "case_compound-arm-01"}).case_id
        == "case_compound-arm-01"
    )

    for case_id in ("", "x", "other-public-handle"):
        with pytest.raises(ValidationError):
            OpenCaseInput.model_validate({"case_id": case_id})


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


def test_patch_and_basis_experiment_are_single_objects() -> None:
    with pytest.raises(ValidationError):
        CreateRevisionInput.model_validate(
            {
                "case_id": "case_demo",
                "base_revision_id": "r000",
                "expected_base_sha256": "0" * 64,
                "basis_hypothesis_id": "hyp_demo",
                "basis_experiment_run_id": ["run_experiment_001"],
                "patch": {
                    "target": {"kind": "joint", "name": "elbow"},
                    "attribute": "damping",
                    "expected_old_value": 0.3,
                    "new_value": 0.5,
                },
                "rationale": "The experiment separates the proposed change.",
                "expected_effect": {
                    "scenario_id": "public_center",
                    "predicates": [
                        {"metric": "hold_error_p95_m", "op": "lte", "value": 0.03}
                    ],
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
                "basis_experiment_run_id": "run_experiment_001",
                "patch": [
                    {
                        "target": {"kind": "joint", "name": "elbow"},
                        "attribute": "damping",
                        "expected_old_value": 0.3,
                        "new_value": 0.5,
                    }
                ],
                "rationale": "The experiment separates the proposed change.",
                "expected_effect": {
                    "scenario_id": "public_center",
                    "predicates": [
                        {"metric": "hold_error_p95_m", "op": "lte", "value": 0.03}
                    ],
                },
            }
        )


def _run_experiment_input_payload() -> dict[str, object]:
    return {
        "case_id": "case_demo",
        "revision_id": "r000",
        "hypothesis": {
            "claim": "The elbow motion plane conflicts with the task plane.",
            "suspected_elements": [
                {"kind": "joint", "name": "elbow", "attributes": ["axis"]}
            ],
            "competing_explanation": {
                "claim": "The elbow actuator mapping is inverted.",
                "suspected_elements": [
                    {
                        "kind": "actuator",
                        "name": "elbow_motor",
                        "attributes": ["joint"],
                    }
                ],
                "discriminating_reason": "The observed motion plane separates the causes.",
            },
            "prediction": "The elbow moves toward the target.",
            "falsifier": "The elbow remains stationary.",
        },
        "initial_joint_positions": [
            {"joint_name": "shoulder", "position_rad": 0.0},
            {"joint_name": "elbow", "position_rad": 0.1},
        ],
        "segments": [
            {
                "label": "excitation",
                "n_steps": 128,
                "controls": [
                    {"actuator_name": "shoulder_motor", "value": 0.2},
                    {"actuator_name": "elbow_motor", "value": 0.3},
                ],
            },
            {
                "label": "recovery",
                "n_steps": 128,
                "controls": [
                    {"actuator_name": "shoulder_motor", "value": 0.0},
                    {"actuator_name": "elbow_motor", "value": 0.1},
                ],
            },
        ],
        "observables": [
            {"kind": "qpos"},
            {"kind": "qvel"},
            {"kind": "energy"},
            {"kind": "contact_count"},
            {"kind": "body_position", "body_name": "hand"},
        ],
        "capture_final_snapshot": True,
    }


def _run_experiment_output_payload() -> dict[str, object]:
    return {
        "schema_version": "asset-autopsy/v1",
        "request_id": "req_demo",
        "case_id": "case_demo",
        "event_ids": ["evt_demo"],
        "warnings": [],
        "artifacts": [],
        "revision_id": "r000",
        "hypothesis_id": "hyp_demo",
        "run_id": "run_demo",
        "asset_sha256": "1" * 64,
        "condition_sha256": "2" * 64,
        "execution_fingerprint_sha256": "3" * 64,
        "trace_sha256": "4" * 64,
        "outcome": {"kind": "completed", "budget_consumed": True},
        "requested_steps": 256,
        "completed_steps": 256,
        "segment_boundaries": [
            {"segment_index": 0, "start_step": 0, "end_step": 128},
            {"segment_index": 1, "start_step": 128, "end_step": 256},
        ],
        "trace": {
            "columns": [
                {"kind": "time"},
                {"kind": "qpos", "joint_name": "elbow"},
                {"kind": "qvel", "joint_name": "elbow"},
                {"kind": "energy", "component": "potential"},
                {"kind": "contact_count"},
                {"kind": "body_position", "body_name": "hand", "axis": "x"},
                {"kind": "control", "actuator_name": "shoulder_motor"},
                {"kind": "control", "actuator_name": "elbow_motor"},
            ],
            "rows": [
                {
                    "time_s": index * 0.01,
                    "values": {
                        "qpos:elbow": 0.0,
                        "qvel:elbow": 0.0,
                        "energy:potential": 1.0,
                        "contact_count": 0.0,
                        "body_position:hand:x": 0.0,
                        "control:shoulder_motor": 0.2 if index < 128 else 0.0,
                        "control:elbow_motor": 0.3 if index < 128 else 0.1,
                    },
                }
                for index in range(256)
            ],
        },
        "final_snapshot": {
            "artifact_id": "art_snapshot",
            "uri": "autopsy://case_demo/art_snapshot",
            "sha256": "5" * 64,
            "bytes": 42,
            "step": 255,
            "width_px": 160,
            "height_px": 120,
        },
    }


def test_run_experiment_output_supplies_revision_basis_identifiers() -> None:
    run = RunExperimentOutput.model_validate(_run_experiment_output_payload())
    revision = CreateRevisionInput.model_validate(
        {
            "case_id": run.case_id,
            "base_revision_id": run.revision_id,
            "expected_base_sha256": "0" * 64,
            "basis_hypothesis_id": run.hypothesis_id,
            "basis_experiment_run_id": run.run_id,
            "patch": {
                "target": {"kind": "joint", "name": "elbow"},
                "attribute": "damping",
                "expected_old_value": 0.3,
                "new_value": 0.5,
            },
            "rationale": "The experiment separates the proposed change.",
            "expected_effect": {
                "scenario_id": "public_center",
                "predicates": [
                    {"metric": "hold_error_p95_m", "op": "lte", "value": 0.03}
                ],
            },
        }
    )
    assert revision.basis_hypothesis_id == "hyp_demo"
    assert revision.basis_experiment_run_id == "run_demo"


def test_run_experiment_accepts_bounded_generic_contract() -> None:
    experiment = RunExperimentInput.model_validate(_run_experiment_input_payload())
    assert len(experiment.segments) == 2
    assert experiment.observables[-1].kind == "body_position"
    assert experiment.hypothesis.suspected_elements[0].name == "elbow"
    assert experiment.hypothesis.competing_explanation.suspected_elements[0].name == (
        "elbow_motor"
    )


def test_experiment_trace_columns_expand_the_accepted_named_selection() -> None:
    experiment = RunExperimentInput.model_validate(_run_experiment_input_payload())

    columns = experiment_trace_columns(
        observables=experiment.observables,
        joint_names=("shoulder", "elbow"),
        actuator_names=("shoulder_motor", "elbow_motor"),
    )

    assert [column.model_dump(mode="json") for column in columns] == [
        {"kind": "time"},
        {"kind": "qpos", "joint_name": "shoulder"},
        {"kind": "qpos", "joint_name": "elbow"},
        {"kind": "qvel", "joint_name": "shoulder"},
        {"kind": "qvel", "joint_name": "elbow"},
        {"kind": "energy", "component": "potential"},
        {"kind": "energy", "component": "kinetic"},
        {"kind": "contact_count"},
        {"kind": "body_position", "body_name": "hand", "axis": "x"},
        {"kind": "body_position", "body_name": "hand", "axis": "y"},
        {"kind": "body_position", "body_name": "hand", "axis": "z"},
        {"kind": "control", "actuator_name": "shoulder_motor"},
        {"kind": "control", "actuator_name": "elbow_motor"},
    ]


def test_run_experiment_rejects_old_or_incomplete_input_names() -> None:
    old_position = _run_experiment_input_payload()
    old_position["initial_joint_positions"][0]["position"] = old_position[
        "initial_joint_positions"
    ][0].pop("position_rad")
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(old_position)

    old_steps = _run_experiment_input_payload()
    old_steps["segments"][0]["steps"] = old_steps["segments"][0].pop("n_steps")
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(old_steps)

    old_body_name = _run_experiment_input_payload()
    old_body_name["observables"][-1]["name"] = old_body_name["observables"][-1].pop(
        "body_name"
    )
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(old_body_name)

    missing_claim = _run_experiment_input_payload()
    del missing_claim["hypothesis"]["claim"]
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(missing_claim)


def test_run_experiment_rejects_invalid_hypothesis_reference_or_segment_label() -> None:
    invalid_attribute = _run_experiment_input_payload()
    invalid_attribute["hypothesis"]["suspected_elements"][0]["attributes"] = ["target"]
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(invalid_attribute)

    long_label = _run_experiment_input_payload()
    long_label["segments"][0]["label"] = "x" * 65
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(long_label)

    nonscalar_prediction = _run_experiment_input_payload()
    nonscalar_prediction["hypothesis"]["prediction"] = {"matches": True}
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(nonscalar_prediction)


def test_run_experiment_enforces_collection_bounds_and_finite_positions() -> None:
    no_positions = _run_experiment_input_payload()
    no_positions["initial_joint_positions"] = []
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(no_positions)

    too_many_segments = _run_experiment_input_payload()
    too_many_segments["segments"] = [
        {
            "n_steps": 16,
            "controls": [{"actuator_name": "elbow_motor", "value": 0.0}],
        }
        for _ in range(17)
    ]
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(too_many_segments)

    too_many_observables = _run_experiment_input_payload()
    too_many_observables["observables"] = [
        {"kind": "body_position", "body_name": f"body_{index}"} for index in range(9)
    ]
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(too_many_observables)

    nonfinite_position = _run_experiment_input_payload()
    nonfinite_position["initial_joint_positions"][0]["position_rad"] = float("inf")
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(nonfinite_position)


def test_run_experiment_rejects_duplicate_initial_joint_positions() -> None:
    payload = _run_experiment_input_payload()
    payload["initial_joint_positions"] = [
        {"joint_name": "elbow", "position_rad": 0.0},
        {"joint_name": "elbow", "position_rad": 0.1},
    ]
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(payload)


def test_run_experiment_rejects_duplicate_or_incomplete_segment_controls() -> None:
    duplicate = _run_experiment_input_payload()
    duplicate["segments"][0]["controls"][1]["actuator_name"] = "shoulder_motor"
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(duplicate)

    incomplete = _run_experiment_input_payload()
    incomplete["segments"][1]["controls"] = incomplete["segments"][1]["controls"][:-1]
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(incomplete)


@pytest.mark.parametrize("steps", [255, 100_001])
def test_run_experiment_rejects_total_steps_outside_bounds(steps: int) -> None:
    payload = _run_experiment_input_payload()
    payload["segments"] = [
        {
            "n_steps": steps,
            "controls": [{"actuator_name": "elbow_motor", "value": 0.0}],
        }
    ]
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(payload)


def test_run_experiment_rejects_duplicate_observables_and_nonfinite_controls() -> None:
    duplicate = _run_experiment_input_payload()
    duplicate["observables"] = [{"kind": "qpos"}, {"kind": "qpos"}]
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(duplicate)

    nonfinite = _run_experiment_input_payload()
    nonfinite["segments"][0]["controls"][0]["value"] = float("nan")
    with pytest.raises(ValidationError):
        RunExperimentInput.model_validate(nonfinite)


def test_run_experiment_output_rejects_invalid_columnar_trace() -> None:
    short_trace = _run_experiment_output_payload()
    short_trace["trace"]["rows"] = short_trace["trace"]["rows"][:-1]
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(short_trace)

    narrow_row = _run_experiment_output_payload()
    narrow_row["trace"]["rows"][0]["values"].pop("control:elbow_motor")
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(narrow_row)

    missing_time_column = _run_experiment_output_payload()
    missing_time_column["trace"]["columns"][0] = {
        "kind": "qpos",
        "joint_name": "shoulder",
    }
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(missing_time_column)

    duplicate_column = _run_experiment_output_payload()
    duplicate_column["trace"]["columns"][2] = duplicate_column["trace"]["columns"][1]
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(duplicate_column)

    duplicate_control = _run_experiment_output_payload()
    duplicate_control["trace"]["columns"][-1]["actuator_name"] = "shoulder_motor"
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(duplicate_control)

    nonuniform_time = _run_experiment_output_payload()
    nonuniform_time["trace"]["rows"][128]["time_s"] += 0.001
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(nonuniform_time)

    nonfinite_row = _run_experiment_output_payload()
    nonfinite_row["trace"]["rows"][0]["values"]["qpos:elbow"] = float("nan")
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(nonfinite_row)

    no_controls = _run_experiment_output_payload()
    no_controls["trace"]["columns"] = no_controls["trace"]["columns"][:-2]
    for row in no_controls["trace"]["rows"]:
        row["values"].pop("control:shoulder_motor")
        row["values"].pop("control:elbow_motor")
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(no_controls)


def test_run_experiment_output_rejects_invalid_boundaries() -> None:
    payload = _run_experiment_output_payload()
    payload["segment_boundaries"][1]["start_step"] = 127
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(payload)

    wrong_requested_steps = _run_experiment_output_payload()
    wrong_requested_steps["requested_steps"] = 257
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(wrong_requested_steps)


def test_run_experiment_output_supports_budgeted_non_finite_domain_outcome() -> None:
    payload = _run_experiment_output_payload()
    payload["outcome"] = {
        "kind": "non_finite_state",
        "budget_consumed": True,
        "first_bad_step": 0,
    }
    payload["completed_steps"] = 0
    payload["trace_sha256"] = None
    payload["trace"] = None
    payload["final_snapshot"] = None
    result = RunExperimentOutput.model_validate(payload)
    assert result.outcome.kind == "non_finite_state"
    assert result.outcome.budget_consumed is True
    assert result.outcome.first_bad_step == 0
    assert result.requested_steps == 256
    assert result.completed_steps == 0
    assert result.trace is None


def test_run_experiment_output_rejects_non_finite_trace_or_missing_bad_step() -> None:
    trace_leak = _run_experiment_output_payload()
    trace_leak["outcome"] = {
        "kind": "non_finite_state",
        "budget_consumed": True,
        "first_bad_step": 64,
    }
    trace_leak["completed_steps"] = 64
    trace_leak["final_snapshot"] = None
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(trace_leak)

    missing_bad_step = _run_experiment_output_payload()
    missing_bad_step["outcome"] = {
        "kind": "non_finite_state",
        "budget_consumed": True,
    }
    missing_bad_step["completed_steps"] = 0
    missing_bad_step["trace_sha256"] = None
    missing_bad_step["trace"] = None
    missing_bad_step["final_snapshot"] = None
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(missing_bad_step)


def test_run_experiment_output_requires_completion_evidence_without_diagnosis() -> None:
    incomplete = _run_experiment_output_payload()
    incomplete["completed_steps"] = 255
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(incomplete)

    missing_trace = _run_experiment_output_payload()
    missing_trace["trace_sha256"] = None
    missing_trace["trace"] = None
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(missing_trace)

    unconsumed = _run_experiment_output_payload()
    unconsumed["outcome"]["budget_consumed"] = False
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(unconsumed)

    diagnosed = _run_experiment_output_payload()
    diagnosed["prediction_matched"] = True
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(diagnosed)


def test_run_experiment_output_exposes_identity_and_typed_selected_signals() -> None:
    result = RunExperimentOutput.model_validate(_run_experiment_output_payload())
    assert result.run_id == "run_demo"
    assert result.asset_sha256 == "1" * 64
    assert result.condition_sha256 == "2" * 64
    assert result.execution_fingerprint_sha256 == "3" * 64
    assert {column.kind for column in result.trace.columns} == {
        "time",
        "qpos",
        "qvel",
        "energy",
        "contact_count",
        "body_position",
        "control",
    }
    assert len(result.trace.rows) == 256
    assert all(
        set(row.values)
        == {
            "qpos:elbow",
            "qvel:elbow",
            "energy:potential",
            "contact_count",
            "body_position:hand:x",
            "control:shoulder_motor",
            "control:elbow_motor",
        }
        for row in result.trace.rows
    )


def test_run_experiment_output_rejects_old_run_identifier() -> None:
    payload = _run_experiment_output_payload()
    payload["experiment_run_id"] = payload.pop("run_id")
    with pytest.raises(ValidationError):
        RunExperimentOutput.model_validate(payload)


def test_open_case_rejects_reversed_position_actuator_control_range() -> None:
    payload = _open_case_payload()
    payload["actuators"][0]["control_range"] = (1.0, -1.0)
    with pytest.raises(ValidationError):
        OpenCaseOutput.model_validate(payload)


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
            "verdict": "public_pass",
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


@pytest.mark.parametrize(
    ("before", "after", "claimed_outcome"),
    [(0.04, 0.035, "improved"), (0.02, 0.025, "regressed")],
)
def test_behavior_diff_clause_outcomes_use_contract_state_transitions(
    before: float, after: float, claimed_outcome: str
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
                "metric_deltas": _metric_deltas(hold_before=before, hold_after=after),
                "clause_outcomes": _clause_outcomes(reach_error=claimed_outcome),
                "verdict": claimed_outcome,
            }
        )


def test_behavior_diff_verdict_matches_clause_outcomes() -> None:
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
                "verdict": "regressed",
            }
        )


def test_joint_summary_rejects_reversed_position_range() -> None:
    with pytest.raises(ValidationError):
        JointSummary.model_validate(
            {
                "name": "elbow",
                "axis": [0.0, 0.0, 1.0],
                "damping": 0.3,
                "armature": 0.0,
                "frictionloss": 0.0,
                "position_range": [1.0, -1.0],
                "body_parent": "forearm",
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
            "clause_outcomes": _clause_outcomes(
                reach_error="unchanged", settling="improved"
            ),
            "verdict": "improved",
        }
    )
    settling_delta = next(
        delta
        for delta in behavior_diff.metric_deltas
        if delta.metric == "settling_time_s"
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
        payload[result_name] = {
            "passed": 1 if result_name == "public_result" else 3,
            "total": 1 if result_name == "public_result" else 3,
            "violated_clause_ids": ["hold_error"],
        }
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
    valid = {
        **base,
        "trace": [{"time_s": float(index), "values": (0.0,)} for index in range(3)],
    }
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
        {**observation, "value": value}
        if observation["metric"] == metric
        else observation
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
        "revision_id": "r002",
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
        "revision_id": "r002",
        "asset_sha256": "1" * 64,
        "canonical_diff": [
            {
                "target": "elbow",
                "attribute": "axis",
                "before": "1 0 0",
                "after": "0 1 0",
            },
            {
                "target": "elbow",
                "attribute": "damping",
                "before": "0.3",
                "after": "0.5",
            },
        ],
        "public_result": {"passed": 1, "total": 1},
        "holdout_result": {"passed": 3, "total": 3},
        "export_name": "repaired-asset",
        "qualified_core_sha256": "2" * 64,
        "ticket_digest": "3" * 64,
    }


def test_promotion_ticket_requires_successful_qualification() -> None:
    ticket = _promotion_ticket_payload()
    PromotionTicket.model_validate(ticket)

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
        PromotionTicket.model_validate(failed_ticket)


@pytest.mark.parametrize("diff_count", [1, 2])
def test_promotion_ticket_accepts_each_qualifiable_diff_count(diff_count: int) -> None:
    ticket = _promotion_ticket_payload()
    ticket["canonical_diff"] = [
        {
            "target": f"joint_{index}",
            "attribute": "damping",
            "before": "0.3",
            "after": "0.5",
        }
        for index in range(diff_count)
    ]
    PromotionTicket.model_validate(ticket)


@pytest.mark.parametrize("diff_count", [0, 3])
def test_promotion_ticket_rejects_diff_counts_outside_revision_budget(
    diff_count: int,
) -> None:
    ticket = _promotion_ticket_payload()
    ticket["canonical_diff"] = [
        {
            "target": f"joint_{index}",
            "attribute": "damping",
            "before": "0.3",
            "after": "0.5",
        }
        for index in range(diff_count)
    ]
    with pytest.raises(ValidationError):
        PromotionTicket.model_validate(ticket)


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
        ("revision_id", "r001"),
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
    payload["public_result"] = {
        "passed": 1,
        "total": 1,
        "violated_clause_ids": ["hold_error"],
    }
    payload["promotion_ticket"] = _promotion_ticket_payload()
    with pytest.raises(ValidationError):
        VerifyRevisionOutput.model_validate(payload)


def test_public_event_tail_accepts_hypothesis_preregistration() -> None:
    event = PublicEventSummary.model_validate(
        {
            "event_id": "evt_demo",
            "kind": "HYPOTHESIS_RECORDED",
            "summary": "Hypothesis recorded.",
        }
    )
    assert event.kind == "HYPOTHESIS_RECORDED"

    completed = PublicEventSummary.model_validate(
        {
            "event_id": "evt_experiment",
            "kind": "EXPERIMENT_COMPLETED",
            "summary": "Experiment completed.",
        }
    )
    assert completed.kind == "EXPERIMENT_COMPLETED"

    with pytest.raises(ValidationError):
        PublicEventSummary.model_validate(
            {
                "event_id": "evt_old",
                "kind": "PROBE_COMPLETED",
                "summary": "Retired event.",
            }
        )


def test_open_case_output_covers_case_commitments() -> None:
    required = set(OpenCaseOutput.model_json_schema()["required"])
    assert {
        "original_asset_sha256",
        "controller_sha256",
        "public_contract_sha256",
        "runner_sha256",
        "holdout_commitment_sha256",
    } <= required


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
        "qualification_state": "unused",
        "original_revision_id": "r000",
        "original_asset_sha256": "1" * 64,
        "controller_sha256": "2" * 64,
        "public_contract_sha256": "3" * 64,
        "runner_sha256": "4" * 64,
        "holdout_commitment_sha256": "5" * 64,
        "public_scenarios": [
            {
                "scenario_id": "public_center",
                "initial_joint_positions": [
                    {"joint_name": "elbow", "position_rad": 0.1}
                ],
                "target_joint_positions": [
                    {"joint_name": "elbow", "position_rad": 0.2}
                ],
                "target_body_name": "end_effector",
                "target_body_position_m": [0.4, 0.2, 0.0],
                "observable_metrics": metrics,
            }
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
        "actuators": [
            {
                "name": "elbow_motor",
                "joint_name": "elbow",
                "control_kind": "position",
                "control_range": (-1.0, 1.0),
            }
        ],
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
            "experiments_remaining": 5,
            "revisions_remaining": 2,
            "qualification_remaining": 1,
        },
        "revision_history": [{"revision_id": "r000", "asset_sha256": "1" * 64}],
    }


def test_open_case_output_binds_fixed_contract_and_lifecycle() -> None:
    OpenCaseOutput.model_validate(_open_case_payload())

    missing_clause = _open_case_payload()
    missing_clause["contract_clauses"] = missing_clause["contract_clauses"][:-1]
    with pytest.raises(ValidationError):
        OpenCaseOutput.model_validate(missing_clause)

    inconsistent_budget = _open_case_payload()
    inconsistent_budget["qualification_state"] = "failed"
    with pytest.raises(ValidationError):
        OpenCaseOutput.model_validate(inconsistent_budget)

    removed_recovery_state = _open_case_payload()
    removed_recovery_state["qualification_state"] = "recovering"
    with pytest.raises(ValidationError):
        OpenCaseOutput.model_validate(removed_recovery_state)


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
        {**observation, "value": -1.0}
        if observation["metric"] == metric
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


def test_aggregate_result_rejects_passed_above_total_regardless_of_field_order() -> (
    None
):
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


@pytest.mark.parametrize(
    ("revision_id", "parent_revision_id"),
    [("r001", "r001"), ("r000", "r001")],
)
def test_create_revision_output_rejects_invalid_lineage(
    revision_id: str, parent_revision_id: str
) -> None:
    with pytest.raises(ValidationError):
        CreateRevisionOutput.model_validate(
            {
                "schema_version": "asset-autopsy/v1",
                "request_id": "req_demo",
                "case_id": "case_demo",
                "revision_id": revision_id,
                "parent_revision_id": parent_revision_id,
                "asset_sha256": "1" * 64,
                "canonical_diff": [
                    {
                        "target": "elbow",
                        "attribute": "damping",
                        "before": "0.3",
                        "after": "0.5",
                    }
                ],
                "status": "created",
            }
        )


def test_create_revision_output_keeps_exactly_one_diff() -> None:
    entry = {
        "target": "elbow",
        "attribute": "damping",
        "before": "0.3",
        "after": "0.5",
    }
    payload = {
        "schema_version": "asset-autopsy/v1",
        "request_id": "req_demo",
        "case_id": "case_demo",
        "revision_id": "r001",
        "parent_revision_id": "r000",
        "asset_sha256": "1" * 64,
        "canonical_diff": [entry],
        "status": "created",
    }
    CreateRevisionOutput.model_validate(payload)

    for canonical_diff in ([], [entry, entry]):
        with pytest.raises(ValidationError):
            CreateRevisionOutput.model_validate(
                {**payload, "canonical_diff": canonical_diff}
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
