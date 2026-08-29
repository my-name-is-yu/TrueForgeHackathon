from __future__ import annotations

import json
from copy import deepcopy

import pytest

from spikes.phase0.trueforge.protocol import DUMMY_TOOLS, PLANNED_TOOLS, inspection_payload, make_png, planned_tool_schemas, png_dimensions
from spikes.phase0.trueforge.runner import _approval_evidence, _sandbox_analysis_evidence, evaluate_phase0_gates


def test_resolved_tool_boundary_is_exact() -> None:
    assert PLANNED_TOOLS == (
        "open_case",
        "inspect_asset",
        "run_task",
        "run_probe",
        "create_revision",
        "verify_revision",
        "publish_revision",
    )
    assert DUMMY_TOOLS == ("inspect_asset", "publish_revision")
    schemas = {schema["name"]: schema for schema in planned_tool_schemas()}
    assert list(schemas) == list(PLANNED_TOOLS)
    assert [name for name, schema in schemas.items() if schema["annotations"]["destructiveHint"]] == ["publish_revision"]


def test_large_tool_fixture_is_exactly_256_rows() -> None:
    payload = inspection_payload()
    assert len(payload["rows"]) == 256
    serialized = json.dumps(payload, separators=(",", ":"))
    assert len(serialized) > 20_000
    assert "traceback" not in serialized
    assert "boundary_targets" not in serialized


def test_image_fixture_is_160_by_120_png() -> None:
    assert png_dimensions(make_png()) == (160, 120)


def _passing_evidence() -> dict:
    return {
        "turn_status": "done",
        "http": {
            "saved_connection": True,
            "streamable_http_tools": True,
            "wrong_bearer": {
                "trueforge_status": 401,
                "request_count": 1,
                "request": {"path": "/mcp", "auth_ok": False, "origin_ok": True, "response_status": 401},
            },
            "wrong_origin": {
                "trueforge_status": 403,
                "request_count": 1,
                "request": {"path": "/mcp", "auth_ok": True, "origin_ok": False, "response_status": 403},
            },
        },
        "ltr": {"offloaded_reference_seen": True},
        "sandbox": {
            "matching_response_count": 1,
            "successful": True,
            "rows": True,
            "analyzed": True,
            "checkout_isolated": True,
            "private_runtime_isolated": True,
            "network_attempted": True,
            "network": "blocked",
        },
        "approval": {"approval_event_seen": True, "publish_approval_call_match": True},
        "exact_spec": True,
        "only_publish_destructive": True,
        "publish_calls": 0,
        "image": {
            "image_blocks": 1,
            "mime_type": "image/png",
            "width": 160,
            "height": 120,
            "host_path_exposed": False,
            "model_context_image_data": False,
        },
    }


@pytest.mark.parametrize(
    ("gate", "change"),
    [
        ("http_auth_origin", lambda evidence: evidence["http"].update(saved_connection=False)),
        ("http_auth_origin", lambda evidence: evidence["http"].update(streamable_http_tools=False)),
        ("http_auth_origin", lambda evidence: evidence["http"]["wrong_bearer"]["request"].update(response_status=200)),
        ("http_auth_origin", lambda evidence: evidence["http"]["wrong_origin"]["request"].update(response_status=200)),
        ("large_tool_response", lambda evidence: evidence["ltr"].update(offloaded_reference_seen=False)),
        ("large_tool_response", lambda evidence: evidence["sandbox"].update(rows=False)),
        ("large_tool_response", lambda evidence: evidence["sandbox"].update(analyzed=False)),
        ("sandbox", lambda evidence: evidence["sandbox"].update(checkout_isolated=False)),
        ("sandbox", lambda evidence: evidence["sandbox"].update(private_runtime_isolated=False)),
        ("sandbox", lambda evidence: evidence["sandbox"].update(network_attempted=False)),
        ("sandbox", lambda evidence: evidence["sandbox"].update(network="reachable")),
        ("agent_spec_approval", lambda evidence: evidence["approval"].update(publish_approval_call_match=False)),
        ("image_transport", lambda evidence: evidence["image"].update(model_context_image_data=True)),
        ("image_transport", lambda evidence: evidence["image"].update(host_path_exposed=True)),
        ("agent_spec_approval", lambda evidence: evidence.update(turn_status="failed")),
    ],
)
def test_each_failed_phase0_assertion_blocks(gate: str, change) -> None:
    evidence = deepcopy(_passing_evidence())
    change(evidence)

    gates, all_pass = evaluate_phase0_gates(evidence)

    assert gates[gate]["result"] == "BLOCKED_HARD_GATE"
    assert all_pass is False


def _tool_response(call_id: str, analysis: dict) -> dict:
    return {
        "event": {
            "type": "tool.response",
            "tool_call_id": call_id,
            "content": json.dumps(
                {
                    "success": True,
                    "response": {"exitCode": 0, "result": json.dumps(analysis, separators=(",", ":"))},
                }
            ),
        }
    }


def test_mixed_sandbox_responses_cannot_combine_into_a_pass() -> None:
    matching = {
        "rows": 256,
        "analyzed": True,
        "checkout_isolated": False,
        "private_runtime_isolated": False,
        "network_attempted": True,
        "network": "reachable",
    }
    unrelated = {
        "rows": 256,
        "analyzed": True,
        "checkout_isolated": True,
        "private_runtime_isolated": True,
        "network_attempted": True,
        "network": "blocked",
    }
    evidence = deepcopy(_passing_evidence())
    evidence["sandbox"] = _sandbox_analysis_evidence(
        {"data": [_tool_response("sandbox-call", matching), _tool_response("other-call", unrelated)]}, "sandbox-call"
    )

    gates, all_pass = evaluate_phase0_gates(evidence)

    assert gates["sandbox"]["result"] == "BLOCKED_HARD_GATE"
    assert all_pass is False


def test_approval_from_another_tool_call_cannot_satisfy_publish_gate() -> None:
    payload = {
        "data": [
            {
                "event": {
                    "type": "model.message",
                    "tool_calls": [{"id": "publish-call", "function": {"name": "publish_revision"}}],
                }
            },
            {"event": {"type": "tool.approval_required", "tool_calls": [{"id": "inspect-call"}]}},
        ]
    }
    evidence = deepcopy(_passing_evidence())
    evidence["approval"] = _approval_evidence(payload, ["publish-call"])

    gates, all_pass = evaluate_phase0_gates(evidence)

    assert gates["agent_spec_approval"]["result"] == "BLOCKED_HARD_GATE"
    assert all_pass is False
