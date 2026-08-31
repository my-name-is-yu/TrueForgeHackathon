from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from asset_autopsy.trueforge_client import evaluate_autonomy_events


CASE_ID = "case_compound-arm-01"
ASSET_R000 = "a" * 64
ASSET_R001 = "b" * 64
ASSET_R002 = "c" * 64


def _run_task_arguments(revision_id: str) -> dict[str, Any]:
    return {
        "case_id": CASE_ID,
        "revision_id": revision_id,
        "scenario_id": "public_center",
        "capture": "metrics",
    }


def _experiment_arguments(
    revision_id: str,
    *,
    primary: str,
    primary_attribute: str,
    competing: str,
    competing_attribute: str,
) -> dict[str, Any]:
    return {
        "case_id": CASE_ID,
        "revision_id": revision_id,
        "hypothesis": {
            "claim": f"{primary} {primary_attribute} controls the observed failure.",
            "suspected_elements": [
                {
                    "kind": "joint",
                    "name": primary,
                    "attributes": [primary_attribute],
                }
            ],
            "competing_explanation": {
                "claim": f"{competing} {competing_attribute} instead controls the failure.",
                "suspected_elements": [
                    {
                        "kind": "joint",
                        "name": competing,
                        "attributes": [competing_attribute],
                    }
                ],
                "discriminating_reason": "Direction and decay separate the explanations.",
            },
            "prediction": "The selected public signals separate the explanations.",
            "falsifier": "The selected public signals do not separate the explanations.",
        },
        "initial_joint_positions": [
            {"joint_name": "joint_a", "position_rad": 0.0},
            {"joint_name": "joint_b", "position_rad": 0.0},
            {"joint_name": "joint_c", "position_rad": 0.0},
        ],
        "segments": [
            {
                "label": "discriminate",
                "n_steps": 256,
                "controls": [
                    {"actuator_name": "motor_a", "value": 0.0},
                    {"actuator_name": "motor_b", "value": 0.2},
                    {"actuator_name": "motor_c", "value": 0.0},
                ],
            }
        ],
        "observables": [
            {"kind": "qpos"},
            {"kind": "qvel"},
            {"kind": "body_position", "body_name": "end_effector"},
        ],
        "capture_final_snapshot": False,
    }


def _revision_arguments(
    *,
    base_revision_id: str,
    base_hash: str,
    hypothesis_id: str,
    run_id: str,
    patch: dict[str, Any],
) -> dict[str, Any]:
    return {
        "case_id": CASE_ID,
        "base_revision_id": base_revision_id,
        "expected_base_sha256": base_hash,
        "basis_hypothesis_id": hypothesis_id,
        "basis_experiment_run_id": run_id,
        "patch": patch,
        "rationale": "The public experiment evidence supports this change.",
        "expected_effect": {
            "scenario_id": "public_center",
            "predicates": [{"metric": "hold_error_p95_m", "op": "lt", "value": 0.03}],
        },
    }


def _promotion_ticket() -> dict[str, Any]:
    return {
        "ticket_id": "evt_ticket-qualified-r002",
        "case_id": CASE_ID,
        "revision_id": "r002",
        "asset_sha256": ASSET_R002,
        "canonical_diff": [
            {
                "target": "joint_b",
                "attribute": "axis",
                "before": "0 0 1",
                "after": "0 1 0",
            },
            {
                "target": "joint_c",
                "attribute": "damping",
                "before": "0.01",
                "after": "0.4",
            },
        ],
        "public_result": {"passed": 1, "total": 1, "violated_clause_ids": []},
        "holdout_result": {"passed": 3, "total": 3, "violated_clause_ids": []},
        "export_name": "compound-arm-01-repaired",
        "qualified_core_sha256": "d" * 64,
        "ticket_digest": "e" * 64,
    }


def _tool_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments, sort_keys=True)},
    }


def _model_call(call_id: str, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return {
        "event": {
            "type": "model.message",
            "tool_calls": [_tool_call(call_id, name, arguments)],
        }
    }


def _tool_response(call_id: str, content: Any) -> dict[str, Any]:
    return {
        "event": {
            "type": "tool.response",
            "tool_call_id": call_id,
            "content": content
            if isinstance(content, str)
            else json.dumps(content, sort_keys=True),
        }
    }


def _approval_required(call_id: str) -> dict[str, Any]:
    return {
        "event": {
            "type": "tool.approval_required",
            "tool_calls": [{"id": call_id}],
        }
    }


def _analysis_arguments(style: str, path: str, signal: str) -> dict[str, Any]:
    if style == "python_code":
        return {
            "intent": "Analyze the offloaded public experiment trace.",
            "language": "python",
            "code": (
                "import json\n"
                "from pathlib import Path\n"
                f"data = json.loads(Path({path!r}).read_text())\n"
                f"values = [row['values'][{signal!r}] for row in data['trace']['rows']]\n"
                "print(max(values) - min(values))\n"
            ),
        }
    return {
        "intent": "Analyze the offloaded public experiment trace.",
        "command": (
            'python -c "import json,statistics; '
            f"p=json.load(open('{path}')); "
            f"v=[r['values']['{signal}'] for r in p['trace']['rows']]; "
            'print(statistics.fmean(v))"'
        ),
    }


def _analysis_response(style: str, run_id: str) -> Any:
    if style == "python_code":
        return {
            "status": "success",
            "exit_code": 0,
            "stdout": f"analyzed public trace for {run_id}",
        }
    return {
        "success": True,
        "response": {
            "exitCode": 0,
            "result": f"analysis completed for public experiment {run_id}",
        },
    }


def _successful_events(style: str = "python_code") -> list[dict[str, Any]]:
    trace_one = "/sandbox/large_tool_responses/experiment-axis.json"
    trace_two = "/sandbox/large_tool_responses/experiment-damping.json"
    promotion_ticket = _promotion_ticket()
    return [
        _model_call("open", "open_case", {"case_id": CASE_ID}),
        _tool_response("open", {"schema_version": "asset-autopsy/v1", "head": "r000"}),
        _model_call("baseline", "run_task", _run_task_arguments("r000")),
        _tool_response(
            "baseline",
            {
                "schema_version": "asset-autopsy/v1",
                "revision_id": "r000",
                "result": "fail",
            },
        ),
        _model_call(
            "inspect",
            "inspect_asset",
            {"case_id": CASE_ID, "revision_id": "r000", "view": "both"},
        ),
        _tool_response(
            "inspect", {"schema_version": "asset-autopsy/v1", "revision_id": "r000"}
        ),
        _model_call(
            "experiment-1",
            "run_experiment",
            _experiment_arguments(
                "r000",
                primary="joint_b",
                primary_attribute="axis",
                competing="joint_c",
                competing_attribute="damping",
            ),
        ),
        _tool_response(
            "experiment-1", f"Content too large. Result saved to: {trace_one}"
        ),
        _model_call(
            "sandbox-1",
            "exec",
            _analysis_arguments(style, trace_one, "body_position:end_effector:z"),
        ),
        _tool_response("sandbox-1", _analysis_response(style, "run_axis_evidence")),
        _model_call(
            "revision-1",
            "create_revision",
            _revision_arguments(
                base_revision_id="r000",
                base_hash=ASSET_R000,
                hypothesis_id="hyp_axis_evidence",
                run_id="run_axis_evidence",
                patch={
                    "target": {"kind": "joint", "name": "joint_b"},
                    "attribute": "axis",
                    "expected_old_value": [0.0, 0.0, 1.0],
                    "new_value": [0.0, 1.0, 0.0],
                },
            ),
        ),
        _tool_response(
            "revision-1",
            {
                "schema_version": "asset-autopsy/v1",
                "revision_id": "r001",
                "canonical_diff": [
                    {
                        "target": "joint_b",
                        "attribute": "axis",
                        "before": "0 0 1",
                        "after": "0 1 0",
                    }
                ],
            },
        ),
        _model_call("task-r001", "run_task", _run_task_arguments("r001")),
        _tool_response(
            "task-r001",
            {
                "schema_version": "asset-autopsy/v1",
                "revision_id": "r001",
                "result": "fail",
                "behavior_diff": {"verdict": "improved_but_failing", "changed": True},
            },
        ),
        _model_call(
            "experiment-2",
            "run_experiment",
            _experiment_arguments(
                "r001",
                primary="joint_c",
                primary_attribute="damping",
                competing="joint_b",
                competing_attribute="axis",
            ),
        ),
        _tool_response(
            "experiment-2", f"Content too large. Result saved to: {trace_two}"
        ),
        _model_call(
            "sandbox-2",
            "exec",
            _analysis_arguments(style, trace_two, "qvel:joint_c"),
        ),
        _tool_response("sandbox-2", _analysis_response(style, "run_damping_evidence")),
        _model_call(
            "revision-2",
            "create_revision",
            _revision_arguments(
                base_revision_id="r001",
                base_hash=ASSET_R001,
                hypothesis_id="hyp_damping_evidence",
                run_id="run_damping_evidence",
                patch={
                    "target": {"kind": "joint", "name": "joint_c"},
                    "attribute": "damping",
                    "expected_old_value": 0.01,
                    "new_value": 0.4,
                },
            ),
        ),
        _tool_response(
            "revision-2",
            {
                "schema_version": "asset-autopsy/v1",
                "revision_id": "r002",
                "canonical_diff": [
                    {
                        "target": "joint_c",
                        "attribute": "damping",
                        "before": "0.01",
                        "after": "0.4",
                    }
                ],
            },
        ),
        _model_call("task-r002", "run_task", _run_task_arguments("r002")),
        _tool_response(
            "task-r002",
            {
                "schema_version": "asset-autopsy/v1",
                "revision_id": "r002",
                "result": "pass",
                "behavior_diff": {"verdict": "public_pass", "changed": True},
            },
        ),
        _model_call(
            "verify",
            "verify_revision",
            {
                "case_id": CASE_ID,
                "revision_id": "r002",
                "expected_asset_sha256": ASSET_R002,
            },
        ),
        _tool_response(
            "verify",
            {
                "schema_version": "asset-autopsy/v1",
                "public_result": {"passed": 1, "total": 1},
                "holdout_result": {"passed": 3, "total": 3},
                "promotion_ticket": promotion_ticket,
            },
        ),
        _model_call(
            "publish",
            "publish_revision",
            {"case_id": CASE_ID, "promotion_ticket": promotion_ticket},
        ),
        _approval_required("publish"),
    ]


def _call_event(events: list[dict[str, Any]], call_id: str) -> dict[str, Any]:
    return next(
        item["event"]
        for item in events
        if any(
            call.get("id") == call_id for call in item["event"].get("tool_calls", [])
        )
    )


def _response_event(events: list[dict[str, Any]], call_id: str) -> dict[str, Any]:
    return next(
        item["event"] for item in events if item["event"].get("tool_call_id") == call_id
    )


def _arguments(events: list[dict[str, Any]], call_id: str) -> dict[str, Any]:
    call = _call_event(events, call_id)["tool_calls"][0]
    return json.loads(call["function"]["arguments"])


def _set_arguments(
    events: list[dict[str, Any]], call_id: str, arguments: dict[str, Any]
) -> None:
    call = _call_event(events, call_id)["tool_calls"][0]
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)


@pytest.mark.parametrize("style", ["python_code", "shell_command"])
def test_accepts_semantically_valid_sandbox_programs_without_source_or_stdout_shape(
    style: str,
) -> None:
    evidence = evaluate_autonomy_events(_successful_events(style))

    assert evidence["passed"] is True
    assert evidence["failures"] == []
    assert evidence["large_tool_response"] == {
        "offloaded_experiments": 2,
        "revisions_with_offloaded_evidence": 2,
    }
    assert evidence["sandbox"]["revisions_with_successful_analysis"] == 2
    assert evidence["revisions"] == {
        "evidence_backed": 2,
        "single_attribute_diffs": 2,
    }


def test_accepts_additional_exploration_without_a_fixed_experiment_count() -> None:
    events = _successful_events()
    insertion = next(
        index
        for index, item in enumerate(events)
        if any(
            call.get("id") == "experiment-1"
            for call in item["event"].get("tool_calls", [])
        )
    )
    path = "/sandbox/large_tool_responses/exploration.json"
    events[insertion:insertion] = [
        _model_call(
            "exploration",
            "run_experiment",
            _experiment_arguments(
                "r000",
                primary="joint_a",
                primary_attribute="damping",
                competing="joint_b",
                competing_attribute="axis",
            ),
        ),
        _tool_response("exploration", f"Content too large. Result saved to: {path}"),
        _model_call(
            "sandbox-exploration",
            "exec",
            _analysis_arguments("shell_command", path, "qvel:joint_a"),
        ),
        _tool_response("sandbox-exploration", "exploration complete"),
    ]

    evidence = evaluate_autonomy_events(events)

    assert evidence["passed"] is True
    assert evidence["experiment_count"] == 3
    assert evidence["revision_count"] == 2


def test_excludes_domain_error_retry_from_successful_revision_evidence() -> None:
    events = _successful_events()
    insertion = next(
        index
        for index, item in enumerate(events)
        if any(
            call.get("id") == "revision-1"
            for call in item["event"].get("tool_calls", [])
        )
    )
    events[insertion:insertion] = [
        _model_call(
            "rejected-revision",
            "create_revision",
            _revision_arguments(
                base_revision_id="r000",
                base_hash=ASSET_R000,
                hypothesis_id="hyp_invented",
                run_id="run_invented",
                patch={
                    "target": {"kind": "joint", "name": "joint_b"},
                    "attribute": "axis",
                    "expected_old_value": [0.0, 0.0, 1.0],
                    "new_value": [0.0, 1.0, 0.0],
                },
            ),
        ),
        _tool_response(
            "rejected-revision",
            {
                "error": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "code": "CAUSAL_EXPERIMENT_NOT_FOUND",
                                "message": "The cited experiment run was not found.",
                            }
                        ),
                    }
                ]
            },
        ),
    ]

    evidence = evaluate_autonomy_events(events)

    assert evidence["passed"] is True
    assert evidence["revision_count"] == 2
    assert evidence["rejected_attempts"] == {
        "count": 1,
        "tools": ["create_revision"],
    }
    assert evidence["invoked_tool_order"].count("create_revision") == 3


def test_rejects_revision_without_large_tool_response_provenance() -> None:
    events = _successful_events()
    _response_event(events, "experiment-1")["content"] = "inline experiment result"

    evidence = evaluate_autonomy_events(events)

    assert evidence["passed"] is False
    assert (
        "a revision lacks successful Sandbox analysis of a preceding offloaded current-base experiment"
        in evidence["failures"]
    )


def test_rejects_unrelated_successful_exec_as_revision_evidence() -> None:
    events = _successful_events()
    _set_arguments(
        events,
        "sandbox-1",
        {"intent": "Inspect the sandbox environment.", "command": "pwd"},
    )

    evidence = evaluate_autonomy_events(events)

    assert evidence["passed"] is False
    assert (
        "a revision lacks successful Sandbox analysis of a preceding offloaded current-base experiment"
        in evidence["failures"]
    )


def test_rejects_exec_that_references_a_different_experiment_path() -> None:
    events = _successful_events()
    _set_arguments(
        events,
        "sandbox-1",
        _analysis_arguments(
            "shell_command",
            "/sandbox/large_tool_responses/experiment-damping.json",
            "qvel:joint_c",
        ),
    )

    evidence = evaluate_autonomy_events(events)

    assert evidence["passed"] is False
    assert (
        "a revision lacks successful Sandbox analysis of a preceding offloaded current-base experiment"
        in evidence["failures"]
    )


@pytest.mark.parametrize(
    ("violation", "content"),
    [
        ("missing", None),
        (
            "failed",
            {"exit_code": 1, "status": "failed", "stderr": "analysis failed"},
        ),
        (
            "trueforge_failed",
            {
                "success": True,
                "response": {"exitCode": 1, "result": "analysis failed"},
            },
        ),
        (
            "trueforge_outer_failed",
            {
                "success": False,
                "response": {"exitCode": 0, "result": "analysis complete"},
            },
        ),
        (
            "structured_without_outcome",
            {"success": True, "response": {"result": "analysis complete"}},
        ),
        (
            "conflicting_exit_codes",
            {"success": True, "response": {"exit_code": None, "exitCode": 1}},
        ),
        ("plain_text", "analysis complete"),
    ],
)
def test_rejects_revision_without_successful_sandbox_outcome(
    violation: str, content: Any
) -> None:
    events = _successful_events()
    if violation == "missing":
        events[:] = [
            item for item in events if item["event"].get("tool_call_id") != "sandbox-1"
        ]
    else:
        _response_event(events, "sandbox-1")["content"] = (
            content if isinstance(content, str) else json.dumps(content)
        )

    evidence = evaluate_autonomy_events(events)

    assert evidence["passed"] is False
    assert (
        "a revision lacks successful Sandbox analysis of a preceding offloaded current-base experiment"
        in evidence["failures"]
    )


def test_accepts_trueforge_local_sandbox_zero_exit_code() -> None:
    events = _successful_events()
    _response_event(events, "sandbox-1")["content"] = json.dumps(
        {
            "success": True,
            "response": {"exitCode": 0, "result": "analysis complete"},
        }
    )

    evidence = evaluate_autonomy_events(events)

    assert evidence["passed"] is True


def test_rejects_experiment_from_a_different_revision_base() -> None:
    events = _successful_events()
    arguments = _arguments(events, "experiment-1")
    arguments["revision_id"] = "r999"
    _set_arguments(events, "experiment-1", arguments)

    evidence = evaluate_autonomy_events(events)

    assert evidence["passed"] is False
    assert (
        "a revision lacks successful Sandbox analysis of a preceding offloaded current-base experiment"
        in evidence["failures"]
    )


def test_rejects_revision_without_cited_run_identity() -> None:
    events = _successful_events()
    arguments = _arguments(events, "revision-1")
    arguments.pop("basis_experiment_run_id")
    _set_arguments(events, "revision-1", arguments)

    evidence = evaluate_autonomy_events(events)

    assert evidence["passed"] is False
    assert (
        "create_revision has arguments outside its exact public schema"
        in evidence["failures"]
    )
    assert "a revision lacks cited experiment provenance" in evidence["failures"]


def test_rejects_revision_without_matching_single_attribute_outcome() -> None:
    events = _successful_events()
    _response_event(events, "revision-1")["content"] = json.dumps(
        {"schema_version": "asset-autopsy/v1", "revision_id": "r001"}
    )

    evidence = evaluate_autonomy_events(events)

    assert evidence["passed"] is False
    assert (
        "a revision response does not prove one changed attribute"
        in evidence["failures"]
    )


@pytest.mark.parametrize("violation", ["baseline", "behavior_diff", "qualification"])
def test_rejects_missing_required_outcome(violation: str) -> None:
    events = _successful_events()
    if violation == "baseline":
        payload = json.loads(_response_event(events, "baseline")["content"])
        payload["result"] = "pass"
        _response_event(events, "baseline")["content"] = json.dumps(payload)
    elif violation == "behavior_diff":
        payload = json.loads(_response_event(events, "task-r002")["content"])
        payload["behavior_diff"]["changed"] = False
        _response_event(events, "task-r002")["content"] = json.dumps(payload)
    else:
        payload = json.loads(_response_event(events, "verify")["content"])
        payload["holdout_result"]["passed"] = 2
        _response_event(events, "verify")["content"] = json.dumps(payload)

    evidence = evaluate_autonomy_events(events)

    assert evidence["passed"] is False


@pytest.mark.parametrize(
    "violation", ["wrong_ticket", "publish_response", "later_call"]
)
def test_rejects_publication_that_does_not_pause_at_qualified_approval(
    violation: str,
) -> None:
    events = _successful_events()
    if violation == "wrong_ticket":
        arguments = copy.deepcopy(_arguments(events, "publish"))
        arguments["promotion_ticket"]["ticket_digest"] = "f" * 64
        _set_arguments(events, "publish", arguments)
    elif violation == "publish_response":
        events.append(
            _tool_response(
                "publish",
                {"schema_version": "asset-autopsy/v1", "publication": "unexpected"},
            )
        )
    else:
        events.append(
            _model_call(
                "late-inspect",
                "inspect_asset",
                {
                    "case_id": CASE_ID,
                    "revision_id": "r002",
                    "view": "both",
                },
            )
        )
        events.append(
            _tool_response(
                "late-inspect",
                {
                    "schema_version": "asset-autopsy/v1",
                    "revision_id": "r002",
                },
            )
        )

    evidence = evaluate_autonomy_events(events)

    assert evidence["passed"] is False
