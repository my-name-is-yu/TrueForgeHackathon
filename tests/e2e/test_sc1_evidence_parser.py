from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from asset_autopsy.trueforge_client import evaluate_sc1_events


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
                "discriminating_reason": "Direction and decay separate the two explanations.",
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
        "rationale": "The Sandbox analysis supports this one-attribute change.",
        "expected_effect": {
            "scenario_id": "public_center",
            "predicates": [
                {"metric": "hold_error_p95_m", "op": "lt", "value": 0.03}
            ],
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
            "content": content if isinstance(content, str) else json.dumps(content, sort_keys=True),
        }
    }


def _approval_required(call_id: str) -> dict[str, Any]:
    return {
        "event": {
            "type": "tool.approval_required",
            "tool_calls": [{"id": call_id}],
        }
    }


def _successful_events() -> list[dict[str, Any]]:
    trace_one = "/sandbox/large_tool_responses/experiment-axis.json"
    trace_two = "/sandbox/large_tool_responses/experiment-damping.json"
    promotion_ticket = _promotion_ticket()
    events = [
        _model_call("open", "open_case", {"case_id": "case_compound-arm-01"}),
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
            "inspect",
            {"schema_version": "asset-autopsy/v1", "revision_id": "r000"},
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
            "experiment-1",
            f"Content too large. Result saved to: {trace_one}",
        ),
        _model_call(
            "sandbox-1",
            "exec",
            {
                "intent": "Analyze the first offloaded experiment trace.",
                "command": (
                    "python - <<'PY'\n"
                    "import json\n"
                    f"payload = json.load(open('{trace_one}'))\n"
                    "rows = payload['trace']['rows']\n"
                    "plane = [row['values']['body_position:end_effector:z'] for row in rows]\n"
                    "residual = max(plane) - min(plane)\n"
                    "print(json.dumps({\n"
                    "  'rows': len(rows),\n"
                    "  'run_id': payload['run_id'],\n"
                    "  'metric': f'body_position:end_effector:z={residual:.8g}',\n"
                    "  'finding': 'The isolated motion plane supports the axis hypothesis.',\n"
                    "  'candidate_attribute': 'joint_b.axis',\n"
                    "}))\n"
                    "PY"
                ),
            },
        ),
        _tool_response(
            "sandbox-1",
            {
                "rows": 256,
                "run_id": "run_axis_evidence",
                "metric": "body_position:end_effector:z=0.125",
                "finding": "The isolated motion plane supports the axis hypothesis.",
                "candidate_attribute": "joint_b.axis",
            },
        ),
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
                "behavior_diff": {
                    "verdict": "improved_but_failing",
                    "changed": True,
                },
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
            "experiment-2",
            f"Content too large. Result saved to: {trace_two}",
        ),
        _model_call(
            "sandbox-2",
            "exec",
            {
                "intent": "Analyze the second offloaded experiment trace.",
                "command": (
                    "python - <<'PY'\n"
                    "import json\n"
                    f"payload = json.load(open('{trace_two}'))\n"
                    "rows = payload['trace']['rows']\n"
                    "velocity = [abs(row['values']['qvel:joint_c']) for row in rows]\n"
                    "peak = max(velocity)\n"
                    "print(json.dumps({\n"
                    "  'rows': len(rows),\n"
                    "  'run_id': payload['run_id'],\n"
                    "  'metric': f'qvel:joint_c={peak:.8g}',\n"
                    "  'finding': 'The decay trace supports the damping hypothesis.',\n"
                    "  'candidate_attribute': 'joint_c.damping',\n"
                    "}))\n"
                    "PY"
                ),
            },
        ),
        _tool_response(
            "sandbox-2",
            {
                "rows": 256,
                "run_id": "run_damping_evidence",
                "metric": "qvel:joint_c=0.75",
                "finding": "The decay trace supports the damping hypothesis.",
                "candidate_attribute": "joint_c.damping",
            },
        ),
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
            {
                "case_id": "case_compound-arm-01",
                "promotion_ticket": promotion_ticket,
            },
        ),
        _approval_required("publish"),
    ]
    return events


def _use_guarded_alias_analysis(
    events: list[dict[str, Any]],
    *,
    membership_signal: str = "body_position:end_effector:z",
) -> None:
    trace_path = "/sandbox/large_tool_responses/experiment-axis.json"
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "sandbox-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = (
        "python - <<'PY'\n"
        "import json\n"
        f"path = '{trace_path}'\n"
        "payload = json.load(open(path))\n"
        "rows = payload['trace']['rows']\n"
        "signal = 'body_position:end_effector:z'\n"
        "values = [abs(row['values'][signal]) for row in rows "
        f"if '{membership_signal}' in row['values']]\n"
        "metric = max(values) if values else None\n"
        "out = {\n"
        "  'rows': len(rows),\n"
        "  'run_id': payload['run_id'],\n"
        "  'metric': f'{signal}={metric:.8g}' if metric is not None "
        "else f'{signal}=null',\n"
        "  'finding': 'The isolated motion plane supports the axis hypothesis.',\n"
        "  'candidate_attribute': 'joint_b.axis',\n"
        "}\n"
        "print(json.dumps(out, separators=(',', ':')))\n"
        "PY"
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)
    response = next(
        item["event"]
        for item in events
        if item["event"].get("tool_call_id") == "sandbox-1"
    )
    response["content"] = json.dumps(
        {
            "rows": 256,
            "run_id": "run_axis_evidence",
            "metric": "body_position:end_effector:z=0.125",
            "finding": "The isolated motion plane supports the axis hypothesis.",
            "candidate_attribute": "joint_b.axis",
        },
        sort_keys=True,
    )


def test_accepts_complete_one_prompt_evidence_sequence() -> None:
    evidence = evaluate_sc1_events(_successful_events())

    assert evidence["passed"] is True
    assert evidence["failures"] == []
    assert evidence["experiment_count"] == 2
    assert evidence["revision_count"] == 2
    assert evidence["rejected_attempts"] == {"count": 0, "tools": []}
    assert evidence["large_tool_response"] == {
        "offloaded_experiments": 2,
        "all_read_by_sandbox_python": True,
    }
    assert evidence["sandbox"]["exec_calls"] == 2
    assert evidence["sandbox"]["all_rows_256"] is True
    assert evidence["public"] == {
        "baseline_failed": True,
        "final_passed": True,
        "behavior_diff_improved": True,
    }
    assert evidence["hidden"] == {"passed": 3, "total": 3}
    assert evidence["approval"] == {
        "publish_requests": 1,
        "publish_is_final_public_call": True,
        "qualification_precedes_publish": True,
        "verified_ticket_used": True,
        "matching_approval_required": True,
        "publish_response_absent": True,
    }
    assert evidence["tool_order"] == [
        "open_case",
        "run_task",
        "inspect_asset",
        "run_experiment",
        "create_revision",
        "run_task",
        "run_experiment",
        "create_revision",
        "run_task",
        "verify_revision",
        "publish_revision",
    ]


def test_accepts_guarded_nonempty_aggregate_with_safe_string_aliases() -> None:
    events = _successful_events()
    _use_guarded_alias_analysis(events)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is True
    assert evidence["sandbox"]["runs"][0]["signal_key"] == (
        "body_position:end_effector:z"
    )


def test_rejects_guard_that_checks_a_different_trace_signal() -> None:
    events = _successful_events()
    _use_guarded_alias_analysis(events, membership_signal="qvel:joint_a")

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


def test_uses_last_compact_analysis_when_sandbox_evidence_changes_conclusion() -> None:
    events = _successful_events()
    first_call_event = next(
        item
        for item in events
        if any(
            call.get("id") == "sandbox-1"
            for call in item["event"].get("tool_calls", [])
            if isinstance(call, dict)
        )
    )
    first_response_event = next(
        item
        for item in events
        if item["event"].get("tool_call_id") == "sandbox-1"
    )
    final_call_event = copy.deepcopy(first_call_event)
    final_response_event = copy.deepcopy(first_response_event)
    final_call_event["event"]["tool_calls"][0]["id"] = "sandbox-1-final"
    final_response_event["event"]["tool_call_id"] = "sandbox-1-final"

    first_call = first_call_event["event"]["tool_calls"][0]
    first_arguments = json.loads(first_call["function"]["arguments"])
    first_arguments["command"] = first_arguments["command"].replace(
        "'joint_b.axis'", "'joint_c.frictionloss'"
    )
    first_call["function"]["arguments"] = json.dumps(
        first_arguments, sort_keys=True
    )
    first_response = json.loads(first_response_event["event"]["content"])
    first_response["candidate_attribute"] = "joint_c.frictionloss"
    first_response_event["event"]["content"] = json.dumps(
        first_response, sort_keys=True
    )

    revision_index = next(
        index
        for index, item in enumerate(events)
        if any(
            call.get("id") == "revision-1"
            for call in item["event"].get("tool_calls", [])
            if isinstance(call, dict)
        )
    )
    events[revision_index:revision_index] = [final_call_event, final_response_event]

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is True
    assert evidence["sandbox"]["runs"][0]["candidate_attribute"] == "joint_b.axis"


def test_uses_last_compact_analysis_after_an_intervening_public_call() -> None:
    events = _successful_events()
    call_event = copy.deepcopy(
        next(
            item
            for item in events
            if any(
                call.get("id") == "sandbox-1"
                for call in item["event"].get("tool_calls", [])
                if isinstance(call, dict)
            )
        )
    )
    response_event = copy.deepcopy(
        next(
            item
            for item in events
            if item["event"].get("tool_call_id") == "sandbox-1"
        )
    )
    call = call_event["event"]["tool_calls"][0]
    call["id"] = "sandbox-1-final-after-inspect"
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = arguments["command"].replace(
        "'joint_b.axis'", "'joint_c.damping'"
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)
    response_event["event"]["tool_call_id"] = "sandbox-1-final-after-inspect"
    response = json.loads(response_event["event"]["content"])
    response["candidate_attribute"] = "joint_c.damping"
    response_event["event"]["content"] = json.dumps(response, sort_keys=True)
    revision_index = next(
        index
        for index, item in enumerate(events)
        if any(
            candidate.get("id") == "revision-1"
            for candidate in item["event"].get("tool_calls", [])
            if isinstance(candidate, dict)
        )
    )
    events[revision_index:revision_index] = [
        _model_call(
            "inspect-late",
            "inspect_asset",
            {"case_id": CASE_ID, "revision_id": "r000", "view": "both"},
        ),
        _tool_response(
            "inspect-late",
            {"schema_version": "asset-autopsy/v1", "revision_id": "r000"},
        ),
        call_event,
        response_event,
    ]

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "a revision patch does not match its Sandbox candidate attribute" in evidence[
        "failures"
    ]


@pytest.mark.parametrize("experiment_id", ("experiment-1", "experiment-2"))
def test_rejects_candidate_outside_preregistered_hypotheses(
    experiment_id: str,
) -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == experiment_id
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["hypothesis"] = _experiment_arguments(
        arguments["revision_id"],
        primary="joint_a",
        primary_attribute="armature",
        competing="joint_b",
        competing_attribute="frictionloss",
    )["hypothesis"]
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "a Sandbox candidate was not preregistered by its experiment" in evidence[
        "failures"
    ]


@pytest.mark.parametrize(
    ("sandbox_id", "signal_key"),
    (
        ("sandbox-1", "body_position:end_effector:z"),
        ("sandbox-2", "qvel:joint_c"),
    ),
)
def test_rejects_control_input_as_causal_observation(
    sandbox_id: str, signal_key: str
) -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == sandbox_id
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = arguments["command"].replace(
        signal_key, "control:motor_b"
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)
    response = next(
        item["event"]
        for item in events
        if item["event"].get("tool_call_id") == sandbox_id
    )
    content = json.loads(response["content"])
    content["metric"] = content["metric"].replace(
        signal_key, "control:motor_b"
    )
    response["content"] = json.dumps(content, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert (
        "a Sandbox metric is not a requested non-control experiment observable"
        in evidence["failures"]
    )


def test_rejects_json_dumps_keyword_that_mutates_compact_evidence() -> None:
    events = _successful_events()
    _use_guarded_alias_analysis(events)
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "sandbox-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = arguments["command"].replace(
        "print(json.dumps(out, separators=(',', ':')))",
        "print(json.dumps(out, sort_keys=(out.update({'run_id':'forged'}) or False)))",
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


@pytest.mark.parametrize("suffix", ("null", "nan", "inf", "-inf"))
def test_rejects_nonfinite_or_empty_compact_metric(suffix: str) -> None:
    events = _successful_events()
    _use_guarded_alias_analysis(events)
    response = next(
        item["event"]
        for item in events
        if item["event"].get("tool_call_id") == "sandbox-1"
    )
    content = json.loads(response["content"])
    content["metric"] = f"body_position:end_effector:z={suffix}"
    response["content"] = json.dumps(content, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "a Sandbox Python response does not match its analyzed stdout shape" in evidence[
        "failures"
    ]


def test_rejects_later_compact_response_without_valid_trace_dataflow() -> None:
    events = _successful_events()
    call_event = copy.deepcopy(
        next(
            item
            for item in events
            if any(
                call.get("id") == "sandbox-1"
                for call in item["event"].get("tool_calls", [])
                if isinstance(call, dict)
            )
        )
    )
    response_event = copy.deepcopy(
        next(
            item
            for item in events
            if item["event"].get("tool_call_id") == "sandbox-1"
        )
    )
    call = call_event["event"]["tool_calls"][0]
    call["id"] = "sandbox-1-invalid-final"
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = arguments["command"].replace(".8g", ".7g")
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)
    response_event["event"]["tool_call_id"] = "sandbox-1-invalid-final"
    revision_index = next(
        index
        for index, item in enumerate(events)
        if any(
            candidate.get("id") == "revision-1"
            for candidate in item["event"].get("tool_calls", [])
            if isinstance(candidate, dict)
        )
    )
    events[revision_index:revision_index] = [call_event, response_event]

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


def test_rejects_later_compact_response_with_an_extra_key() -> None:
    events = _successful_events()
    call_event = copy.deepcopy(
        next(
            item
            for item in events
            if any(
                call.get("id") == "sandbox-1"
                for call in item["event"].get("tool_calls", [])
                if isinstance(call, dict)
            )
        )
    )
    response_event = copy.deepcopy(
        next(
            item
            for item in events
            if item["event"].get("tool_call_id") == "sandbox-1"
        )
    )
    call = call_event["event"]["tool_calls"][0]
    call["id"] = "sandbox-1-extra-key"
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = arguments["command"].replace(".8g", ".7g")
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)
    response_event["event"]["tool_call_id"] = "sandbox-1-extra-key"
    response = json.loads(response_event["event"]["content"])
    response["debug"] = "must not make the latest compact attempt disappear"
    response_event["event"]["content"] = json.dumps(response, sort_keys=True)
    revision_index = next(
        index
        for index, item in enumerate(events)
        if any(
            candidate.get("id") == "revision-1"
            for candidate in item["event"].get("tool_calls", [])
            if isinstance(candidate, dict)
        )
    )
    events[revision_index:revision_index] = [call_event, response_event]

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


def test_rejects_metric_label_for_a_different_signal() -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "sandbox-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = arguments["command"].replace(
        "body_position:end_effector:z={residual:.8g}",
        "qpos:joint_a={residual:.8g}",
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)
    response = next(
        item["event"]
        for item in events
        if item["event"].get("tool_call_id") == "sandbox-1"
    )
    content = json.loads(response["content"])
    content["metric"] = "qpos:joint_a=0.125"
    response["content"] = json.dumps(content, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


def test_accepts_ordered_schema_rejection_before_corrected_public_call() -> None:
    events = _successful_events()
    invalid = _experiment_arguments(
        "r000",
        primary="joint_b",
        primary_attribute="axis",
        competing="joint_c",
        competing_attribute="damping",
    )
    invalid["hypothesis"]["initial_joint_positions"] = invalid.pop(
        "initial_joint_positions"
    )
    experiment_index = next(
        index
        for index, item in enumerate(events)
        if any(
            call.get("id") == "experiment-1"
            for call in item["event"].get("tool_calls", [])
            if isinstance(call, dict)
        )
    )
    events[experiment_index:experiment_index] = [
        _model_call("experiment-invalid", "run_experiment", invalid),
        _tool_response(
            "experiment-invalid",
            {
                "error": [
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "code": "INVALID_REQUEST",
                                "message": "The request did not satisfy the public tool contract.",
                                "validation_errors": [
                                    {
                                        "path": "$.initial_joint_positions",
                                        "type": "missing",
                                    }
                                ],
                            }
                        ),
                    }
                ]
            },
        ),
    ]

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is True
    assert evidence["rejected_attempts"] == {
        "count": 1,
        "tools": ["run_experiment"],
    }
    assert evidence["experiment_count"] == 2
    assert evidence["tool_order"].count("run_experiment") == 2


def test_rejects_experiment_without_large_tool_response_offload() -> None:
    events = _successful_events()
    response = next(
        item["event"]
        for item in events
        if item["event"].get("tool_call_id") == "experiment-1"
    )
    response["content"] = json.dumps({"rows": [[0.0]], "run_id": "inline-run"})

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "an experiment response was not moved by Large Tool Response" in evidence["failures"]
    assert evidence["large_tool_response"]["offloaded_experiments"] == 1


def test_rejects_offloaded_experiment_without_sandbox_python_read() -> None:
    events = [
        item
        for item in _successful_events()
        if not (
            item["event"].get("tool_call_id") == "sandbox-1"
            or any(
                call.get("id") == "sandbox-1"
                for call in item["event"].get("tool_calls", [])
                if isinstance(call, dict)
            )
        )
    ]

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "an offloaded experiment was not read by Sandbox Python" in evidence["failures"]
    assert evidence["large_tool_response"]["all_read_by_sandbox_python"] is False


def test_rejects_valid_analyzer_hidden_in_an_unexecuted_shell_branch() -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "sandbox-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    valid_command = arguments["command"]
    arguments["command"] = (
        "if false; then\n"
        f"{valid_command}\n"
        "fi\n"
        "printf '%s\\n' '{\"rows\":256,\"run_id\":\"forged\"}'\n"
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


def test_rejects_ambiguous_sandbox_code_and_command_forms() -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "sandbox-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["language"] = "python"
    arguments["code"] = "print('not the command that produced the response')"
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


def test_rejects_unquoted_heredoc_with_shell_substitution() -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "sandbox-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = arguments["command"].replace(
        "<<'PY'\n",
        "<<PY\n# $(printf 'ignored\\nraise SystemExit')\n",
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


def test_rejects_python_c_with_shell_substitution() -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "sandbox-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    command = arguments["command"]
    source = command.split("\n", 1)[1].rsplit("\nPY", 1)[0]
    arguments["command"] = (
        "python -c \"# $(printf 'ignored\\nraise SystemExit')\n" f"{source}\""
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


def test_rejects_sandbox_code_that_only_mentions_the_trace_path() -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "sandbox-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = (
        "python - <<'PY'\n"
        "# /sandbox/large_tool_responses/experiment-axis.json\n"
        "import json\n"
        "print(json.dumps({'rows': 256}))\n"
        "PY"
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


def test_rejects_sandbox_code_that_loads_json_without_analyzing_trace_values() -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "sandbox-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = (
        "python - <<'PY'\n"
        "import json\n"
        "payload = json.load(open('/sandbox/large_tool_responses/experiment-axis.json'))\n"
        "print(json.dumps({'rows': 256, 'run_id': payload['run_id']}))\n"
        "PY"
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


def test_rejects_unreachable_trace_analysis_with_constant_aggregate() -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "sandbox-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = (
        "python - <<'PY'\n"
        "import json\n"
        "payload = json.load(open('/sandbox/large_tool_responses/experiment-axis.json'))\n"
        "if False:\n"
        "  rows = payload['trace']['rows']\n"
        "  signal = [row['values']['qvel:joint_c'] for row in rows]\n"
        "fake = max([1.0])\n"
        "print(json.dumps({'rows': 256, 'run_id': payload['run_id'], "
        "'metric': f'qvel:joint_c={fake:.8g}', 'finding': "
        "'The isolated motion plane supports the axis hypothesis.', "
        "'candidate_attribute': 'joint_b.axis'}))\n"
        "PY"
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


def test_rejects_named_trace_read_with_unrelated_aggregate() -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "sandbox-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = (
        "python - <<'PY'\n"
        "import json\n"
        "payload = json.load(open('/sandbox/large_tool_responses/experiment-axis.json'))\n"
        "rows = payload['trace']['rows']\n"
        "signal = [row['values']['qvel:joint_c'] for row in rows]\n"
        "fake = max([1.0])\n"
        "print(json.dumps({'rows': len(rows), 'run_id': payload['run_id'], "
        "'metric': f'qvel:joint_c={fake:.8g}', 'finding': "
        "'The isolated motion plane supports the axis hypothesis.', "
        "'candidate_attribute': 'joint_b.axis'}))\n"
        "PY"
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


@pytest.mark.parametrize(
    ("series_expression", "metric_expression"),
    (
        (
            "0 * row['values']['qvel:joint_c'] + 999.0",
            "max(signal)",
        ),
        (
            "row['values']['qvel:joint_c']",
            "max(signal if False else [999.0])",
        ),
        (
            "row['values']['qvel:joint_c']",
            "max(signal) * 0 + 999.0",
        ),
    ),
)
def test_rejects_signal_or_metric_expressions_that_erase_trace_dataflow(
    series_expression: str, metric_expression: str
) -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "sandbox-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = (
        "python - <<'PY'\n"
        "import json\n"
        "payload = json.load(open('/sandbox/large_tool_responses/experiment-axis.json'))\n"
        "rows = payload['trace']['rows']\n"
        f"signal = [{series_expression} for row in rows]\n"
        f"computed = {metric_expression}\n"
        "print(json.dumps({'rows': len(rows), 'run_id': payload['run_id'], "
        "'metric': f'qvel:joint_c={computed:.8g}', 'finding': "
        "'The isolated motion plane supports the axis hypothesis.', "
        "'candidate_attribute': 'joint_b.axis'}))\n"
        "PY"
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


@pytest.mark.parametrize(
    ("preamble", "series_expression", "metric_expression"),
    (
        (
            "max = lambda values: 999.0\n",
            "row['values']['qvel:joint_c']",
            "max(signal)",
        ),
        (
            "abs = lambda value: 999.0\n",
            "abs(row['values']['qvel:joint_c'])",
            "max(signal)",
        ),
        (
            "import builtins\nbuiltins.max = lambda values: 999.0\n",
            "row['values']['qvel:joint_c']",
            "max(signal)",
        ),
        (
            "mutation = setattr(__import__('builtins'), 'max', lambda values: 999.0)\n",
            "row['values']['qvel:joint_c']",
            "max(signal)",
        ),
        (
            "mutation = globals()['__builtins__'].__setattr__('max', lambda values: 999.0)\n",
            "row['values']['qvel:joint_c']",
            "max(signal)",
        ),
        (
            "mutation: setattr(__import__('builtins'), 'max', lambda values: 999.0) = None\n",
            "row['values']['qvel:joint_c']",
            "max(signal)",
        ),
        (
            "row = setattr(__import__('builtins'), 'max', lambda values: 999.0)\n",
            "row['values']['qvel:joint_c']",
            "max(signal)",
        ),
        (
            "len = (setattr(__import__('builtins'), 'max', lambda values: 999.0), lambda values: 256)[1]\n",
            "row['values']['qvel:joint_c']",
            "max(signal)",
        ),
    ),
)
def test_rejects_shadowed_or_mutated_analysis_functions(
    preamble: str, series_expression: str, metric_expression: str
) -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "sandbox-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = (
        "python - <<'PY'\n"
        "import json\n"
        f"{preamble}"
        "payload = json.load(open('/sandbox/large_tool_responses/experiment-axis.json'))\n"
        "rows = payload['trace']['rows']\n"
        f"signal = [{series_expression} for row in rows]\n"
        f"computed = {metric_expression}\n"
        "print(json.dumps({'rows': len(rows), 'run_id': payload['run_id'], "
        "'metric': f'qvel:joint_c={computed:.8g}', 'finding': "
        "'The isolated motion plane supports the axis hypothesis.', "
        "'candidate_attribute': 'joint_b.axis'}))\n"
        "PY"
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


def test_rejects_json_load_hook_even_when_it_reads_the_exact_trace_path() -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "sandbox-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = (
        "python - <<'PY'\n"
        "import json\n"
        "payload = json.load(\n"
        "  open('/sandbox/large_tool_responses/experiment-axis.json'),\n"
        "  object_hook=lambda value: value,\n"
        ")\n"
        "rows = payload['trace']['rows']\n"
        "signal = [row['values']['qvel:joint_c'] for row in rows]\n"
        "computed = max(signal)\n"
        "print(json.dumps({'rows': len(rows), 'run_id': payload['run_id'], "
        "'metric': f'qvel:joint_c={computed:.8g}', 'finding': "
        "'The isolated motion plane supports the axis hypothesis.', "
        "'candidate_attribute': 'joint_b.axis'}))\n"
        "PY"
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


def test_rejects_dynamic_metric_format_spec_with_side_effect() -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "sandbox-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = (
        "python - <<'PY'\n"
        "import json\n"
        "fmt = (setattr(__import__('builtins'), 'max', lambda values: 999.0), '.8g')[1]\n"
        "payload = json.load(open('/sandbox/large_tool_responses/experiment-axis.json'))\n"
        "rows = payload['trace']['rows']\n"
        "signal = [row['values']['qvel:joint_c'] for row in rows]\n"
        "computed = max(signal)\n"
        "print(json.dumps({'rows': len(rows), 'run_id': payload['run_id'], "
        "'metric': f'qvel:joint_c={computed:{fmt}}', 'finding': "
        "'The isolated motion plane supports the axis hypothesis.', "
        "'candidate_attribute': 'joint_b.axis'}))\n"
        "PY"
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


def test_rejects_overwritten_assignment_that_hides_an_earlier_side_effect() -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "sandbox-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["command"] = (
        "python - <<'PY'\n"
        "import json\n"
        "finding = setattr(__import__('builtins'), 'max', lambda values: 999.0)\n"
        "finding = 'The isolated motion plane supports the axis hypothesis.'\n"
        "payload = json.load(open('/sandbox/large_tool_responses/experiment-axis.json'))\n"
        "rows = payload['trace']['rows']\n"
        "signal = [row['values']['qvel:joint_c'] for row in rows]\n"
        "computed = max(signal)\n"
        "print(json.dumps({'rows': len(rows), 'run_id': payload['run_id'], "
        "'metric': f'qvel:joint_c={computed:.8g}', 'finding': finding, "
        "'candidate_attribute': 'joint_b.axis'}))\n"
        "PY"
    )
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "the last compact Sandbox analysis lacks provable trace dataflow" in evidence[
        "failures"
    ]


def test_rejects_sandbox_response_that_differs_from_printed_analysis() -> None:
    events = _successful_events()
    response = next(
        item["event"]
        for item in events
        if item["event"].get("tool_call_id") == "sandbox-1"
    )
    content = json.loads(response["content"])
    content["finding"] = "A fabricated finding not printed by the Sandbox code."
    response["content"] = json.dumps(content, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert (
        "a Sandbox Python response does not match its analyzed stdout shape"
        in evidence["failures"]
    )


def test_rejects_sandbox_response_before_its_exec_call() -> None:
    events = _successful_events()
    response_index = next(
        index
        for index, item in enumerate(events)
        if item["event"].get("tool_call_id") == "sandbox-1"
    )
    response = events.pop(response_index)
    call_index = next(
        index
        for index, item in enumerate(events)
        if any(
            call.get("id") == "sandbox-1"
            for call in item["event"].get("tool_calls", [])
            if isinstance(call, dict)
        )
    )
    events.insert(call_index, response)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "a Sandbox Python analysis lacks an ordered tool response" in evidence["failures"]


def test_rejects_schema_invalid_public_tool_arguments() -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "baseline"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments.pop("capture")
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "run_task has arguments outside its exact public schema" in evidence["failures"]


def test_rejects_revision_not_bound_to_its_sandbox_analysis() -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "revision-1"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["basis_experiment_run_id"] = "run_unanalyzed"
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert (
        "a revision is not causally bound to its Sandbox-analyzed experiment"
        in evidence["failures"]
    )


def test_rejects_revision_response_without_single_matching_diff() -> None:
    events = _successful_events()
    response = next(
        item["event"]
        for item in events
        if item["event"].get("tool_call_id") == "revision-2"
    )
    response["content"] = json.dumps(
        {
            "schema_version": "asset-autopsy/v1",
            "revision_id": "r002",
            "canonical_diff": [],
        },
        sort_keys=True,
    )

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert "a revision response does not prove exactly one changed attribute" in evidence["failures"]


def test_rejects_publish_before_qualification_response() -> None:
    events = _successful_events()
    verify_response_index = next(
        index
        for index, item in enumerate(events)
        if item["event"].get("tool_call_id") == "verify"
    )
    verify_response = events.pop(verify_response_index)
    events.append(verify_response)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert (
        "publish is not bound to the preceding successful qualification ticket"
        in evidence["failures"]
    )
    assert evidence["approval"]["qualification_precedes_publish"] is False


def test_rejects_publish_with_different_qualification_ticket() -> None:
    events = _successful_events()
    call = next(
        call
        for item in events
        for call in item["event"].get("tool_calls", [])
        if call.get("id") == "publish"
    )
    arguments = json.loads(call["function"]["arguments"])
    arguments["promotion_ticket"]["ticket_digest"] = "b" * 64
    call["function"]["arguments"] = json.dumps(arguments, sort_keys=True)

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert (
        "publish is not bound to the preceding successful qualification ticket"
        in evidence["failures"]
    )
    assert evidence["approval"]["verified_ticket_used"] is False


@pytest.mark.parametrize("violation", ["response", "second_invocation"])
def test_rejects_publish_activity_after_approval_requirement(violation: str) -> None:
    events = copy.deepcopy(_successful_events())
    if violation == "response":
        events.append(_tool_response("publish", {"published": True}))
    else:
        events.append(
            _model_call(
                "publish-again",
                "publish_revision",
                {
                    "case_id": "case_compound-arm-01",
                    "promotion_ticket": "ticket-qualified-r002",
                },
            )
        )

    evidence = evaluate_sc1_events(events)

    assert evidence["passed"] is False
    assert (
        "publish did not stop at exactly one matching approval requirement" in evidence["failures"]
    )
