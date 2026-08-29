from __future__ import annotations

import base64
import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from spikes.phase0.trueforge.protocol import (
    BOUNDARY_HELPER_COMMAND,
    DUMMY_TOOLS,
    PLANNED_TOOLS,
    _contains_image_content,
    inspection_payload,
    make_png,
    planned_tool_schemas,
    png_dimensions,
)
from spikes.phase0.trueforge.runner import (
    TrueForgeProcess,
    _approval_evidence,
    _boundary_helper_source,
    _contains_prohibited_boundary_data,
    _sandbox_analysis_evidence,
    _sanitized_events_payload,
    _stage_boundary_helper,
    evaluate_phase0_gates,
)


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


def test_string_encoded_image_payload_is_detected() -> None:
    image_data = base64.b64encode(make_png()).decode("ascii")
    assert _contains_image_content({"content": json.dumps({"data": image_data})}, image_data)


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
            "sentinels_intact": True,
            "helper_staged": True,
            "private_data_clear": True,
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
        ("sandbox", lambda evidence: evidence["sandbox"].update(sentinels_intact=False)),
        ("sandbox", lambda evidence: evidence["sandbox"].update(helper_staged=False)),
        ("sandbox", lambda evidence: evidence["sandbox"].update(private_data_clear=False)),
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
        "helper_status": "ok",
        "rows": 256,
        "analyzed": True,
        "checkout_metadata": "readable",
        "checkout_content": "readable",
        "private_runtime_metadata": "readable",
        "private_runtime_content": "readable",
        "network_attempted": True,
        "network": "reachable",
    }
    unrelated = {
        "helper_status": "ok",
        "rows": 256,
        "analyzed": True,
        "checkout_metadata": "blocked",
        "checkout_content": "blocked",
        "private_runtime_metadata": "blocked",
        "private_runtime_content": "blocked",
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


def test_protected_path_in_raw_event_blocks_before_sanitization(tmp_path) -> None:
    protected_path = tmp_path / "private-runtime" / "sentinel"
    raw = {"data": [{"event": {"type": "sandbox.exec", "path": str(protected_path)}}]}
    assert _contains_prohibited_boundary_data(raw, [protected_path], []) is True
    assert _contains_prohibited_boundary_data(_sanitized_events_payload(raw), [protected_path], []) is False

    evidence = deepcopy(_passing_evidence())
    evidence["sandbox"]["private_data_clear"] = not _contains_prohibited_boundary_data(raw, [protected_path], [])

    gates, all_pass = evaluate_phase0_gates(evidence)

    assert gates["sandbox"]["result"] == "BLOCKED_HARD_GATE"
    assert all_pass is False


@pytest.mark.parametrize(
    "field",
    ["checkout_metadata", "checkout_content", "private_runtime_metadata", "private_runtime_content"],
)
@pytest.mark.parametrize("status", ["missing", "readable", "error"])
def test_each_original_sentinel_access_failure_blocks(field: str, status: str) -> None:
    analysis = {
        "helper_status": "ok",
        "rows": 256,
        "analyzed": True,
        "checkout_metadata": "blocked",
        "checkout_content": "blocked",
        "private_runtime_metadata": "blocked",
        "private_runtime_content": "blocked",
        "network_attempted": True,
        "network": "blocked",
    }
    analysis[field] = status
    evidence = deepcopy(_passing_evidence())
    evidence["sandbox"].update(
        _sandbox_analysis_evidence({"data": [_tool_response("sandbox-call", analysis)]}, "sandbox-call")
    )

    gates, all_pass = evaluate_phase0_gates(evidence)

    assert gates["sandbox"]["result"] == "BLOCKED_HARD_GATE"
    assert all_pass is False


def test_boundary_tool_call_uses_only_the_stable_relative_helper() -> None:
    assert BOUNDARY_HELPER_COMMAND == "python .phase0-boundary-probe.py"
    assert "/" not in BOUNDARY_HELPER_COMMAND


def test_staged_helper_contains_paths_but_not_sentinel_values(tmp_path: Path) -> None:
    sandbox_parent = tmp_path / "sandboxes" / "session"
    sandbox_root = sandbox_parent / "sandbox"
    sandbox_root.mkdir(parents=True)
    checkout = tmp_path / "checkout-sentinel"
    private = tmp_path / "private-sentinel"
    checkout_value = b"checkout-secret"
    private_value = b"private-secret"
    checkout.write_bytes(checkout_value)
    private.write_bytes(private_value)

    helper = _stage_boundary_helper(sandbox_parent, "ltr.json", checkout, private)
    source = helper.read_bytes()

    assert helper == sandbox_root / ".phase0-boundary-probe.py"
    assert str(checkout).encode() in source
    assert str(private).encode() in source
    assert checkout_value not in source
    assert private_value not in source


def test_helper_failure_is_bounded_without_traceback(tmp_path: Path) -> None:
    helper = tmp_path / ".phase0-boundary-probe.py"
    helper.write_text(
        _boundary_helper_source(
            "missing-ltr.json",
            tmp_path / "checkout-sentinel",
            tmp_path / "private-sentinel",
        )
    )

    result = subprocess.run(
        [sys.executable, helper.name],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert json.loads(result.stdout) == {"helper_status": "error"}
    assert result.stderr == ""


def test_partial_start_cleanup_removes_owned_tmpdir_and_home_alias(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    runtime_root.mkdir()
    home = runtime_root / "home"
    home.mkdir()
    temp = runtime_root / "tmp"
    temp.mkdir()
    (temp / "startup-artifact").write_text("transient")
    home_alias = tmp_path / "home-alias"
    home_alias.symlink_to(home, target_is_directory=True)
    trueforge = TrueForgeProcess(0, runtime_root, home_alias)
    trueforge.temp_directory = temp
    trueforge.home_alias_created = True

    trueforge.stop()

    assert not temp.exists()
    assert not home_alias.exists()
