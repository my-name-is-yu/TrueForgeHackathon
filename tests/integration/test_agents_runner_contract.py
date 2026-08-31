from __future__ import annotations

import asyncio
import json
import math
import xml.etree.ElementTree as ET
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from agents import RunConfig, Runner
from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.models.interface import Model, ModelTracing
from agents.mcp import MCPServer
from agents.tool import Tool
from agents.usage import Usage
from mcp.types import CallToolResult
from openai.types.responses import (
    ResponseCodeInterpreterToolCall,
    ResponseFunctionToolCall,
)
from openai.types.responses.response_code_interpreter_tool_call import OutputLogs
from openai.types.responses.response_prompt_param import ResponsePromptParam

from asset_autopsy.agents_runner import (
    CodeInterpreterRecord,
    EXACT_PROMPT,
    RunTranscript,
    build_agent,
    collect_run_transcript,
    evaluate_autonomy_run,
    approval_request_from_result,
)
from asset_autopsy.fixture import CASE_ID, clean_end_effector_position
from asset_autopsy.mcp_server import MCPRuntimeConfig, TOOL_NAMES, create_mcp_facade
from asset_autopsy.runner import RunRecord, SegmentRecord
from asset_autopsy.service import AssetAutopsyService


class DeterministicFakeRunner:
    async def validate(self, xml_string: str) -> bool:
        return ET.fromstring(xml_string).tag == "mujoco"

    async def run(self, configuration: Any) -> RunRecord:
        root = ET.fromstring(configuration.xml_string)
        joints = {joint.attrib["name"]: joint for joint in root.findall(".//joint")}
        repaired = tuple(
            float(item) for item in joints["joint_b"].attrib["axis"].split()
        ) == (0.0, 1.0, 0.0)
        elapsed = 0
        segments = []
        for segment in configuration.segments:
            rows = []
            for _ in range(segment.n_steps):
                elapsed += 1
                target = tuple(float(item) for item in segment.ctrl)
                public = segment.label in {"public_center", "qualification"}
                error = 0.0 if repaired or not public else 0.1
                speed = 0.0 if repaired or not public else 0.1
                qpos = (target[0], target[1], target[2] + error)
                body = clean_end_effector_position(target)
                row = {
                    "t": 0.002 * elapsed,
                    "E_pot": 0.0,
                    "E_kin": 3 * speed * speed,
                    "qpos": qpos,
                    "qvel": (speed, speed, speed),
                    "ctrl": target,
                }
                for selected in configuration.track:
                    if selected.startswith("body_xpos:"):
                        row[selected] = (body[0] + error, body[1], body[2])
                if "contact_count" in configuration.track:
                    row["ncon"] = 0
                rows.append(row)
            segments.append(
                SegmentRecord(segment.label, segment.n_steps, segment.ctrl, tuple(rows))
            )
        return RunRecord(step_count=elapsed, segments=tuple(segments))


class InProcessMCPServer(MCPServer):
    def __init__(self, facade: Any) -> None:
        super().__init__(use_structured_content=True)
        self.facade = facade

    @property
    def name(self) -> str:
        return "asset-autopsy-test"

    async def connect(self) -> None:
        return None

    async def cleanup(self) -> None:
        return None

    async def list_tools(self, run_context=None, agent=None):
        del run_context, agent
        return [
            tool
            for tool in await self.facade.mcp.list_tools()
            if tool.name != "publish_revision"
        ]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None):
        if tool_name == "publish_revision":
            raise AssertionError(
                "publish_revision must use the local approval boundary"
            )
        content, structured = await self.facade.mcp.call_tool(
            tool_name, arguments or {}
        )
        return CallToolResult(
            content=list(content),
            structuredContent=structured,
        )

    async def list_prompts(self):
        return await self.facade.mcp.list_prompts()

    async def get_prompt(self, name: str, arguments: dict[str, Any] | None = None):
        return await self.facade.mcp.get_prompt(name, arguments)


def hypothesis() -> dict[str, Any]:
    return {
        "claim": "The response is controlled by joint_b axis.",
        "suspected_elements": [
            {"kind": "joint", "name": "joint_b", "attributes": ["axis"]}
        ],
        "competing_explanation": {
            "claim": "The response instead comes from joint_c damping.",
            "suspected_elements": [
                {"kind": "joint", "name": "joint_c", "attributes": ["damping"]}
            ],
            "discriminating_reason": "Direction and decay distinguish the explanations.",
        },
        "prediction": "The selected signals change with the commanded direction.",
        "falsifier": "The predicted directional separation is absent.",
    }


class ScriptedRepairModel(Model):
    def __init__(self) -> None:
        self.step = 0
        self.calls: dict[str, str] = {}
        self.processed_outputs: set[str] = set()
        self.outputs: dict[str, dict[str, Any]] = {}
        self.available_tools: set[str] = set()

    def _function_call(self, name: str, arguments: dict[str, Any]) -> ModelResponse:
        call_id = f"call_{self.step}_{name}"
        self.calls[call_id] = name
        return ModelResponse(
            output=[
                ResponseFunctionToolCall(
                    arguments=json.dumps(arguments),
                    call_id=call_id,
                    name=name,
                    type="function_call",
                )
            ],
            usage=Usage(),
            response_id=f"response_{self.step}",
        )

    def _capture_outputs(self, value: str | list[TResponseInputItem]) -> None:
        if not isinstance(value, list):
            return
        for item in value:
            if not isinstance(item, dict) or item.get("type") != "function_call_output":
                continue
            call_id = item.get("call_id")
            if not isinstance(call_id, str) or call_id in self.processed_outputs:
                continue
            self.processed_outputs.add(call_id)
            name = self.calls[call_id]
            output = item.get("output")
            if isinstance(output, str):
                decoded = json.loads(output)
                if isinstance(decoded, dict):
                    self.outputs[name] = decoded

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: Any,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        del model_settings, output_schema, handoffs, tracing
        del previous_response_id, conversation_id, prompt
        assert system_instructions is not None
        self.available_tools.update(tool.name for tool in tools)
        self._capture_outputs(input)
        step = self.step
        self.step += 1
        if step == 0:
            return self._function_call("open_case", {"case_id": CASE_ID})
        if step == 1:
            return self._function_call(
                "run_task",
                {
                    "case_id": CASE_ID,
                    "revision_id": "r000",
                    "scenario_id": "public_center",
                    "capture": "metrics",
                },
            )
        if step == 2:
            return self._function_call(
                "inspect_asset",
                {"case_id": CASE_ID, "revision_id": "r000", "view": "both"},
            )
        if step == 3:
            return self._function_call(
                "run_experiment",
                {
                    "case_id": CASE_ID,
                    "revision_id": "r000",
                    "hypothesis": hypothesis(),
                    "initial_joint_positions": [
                        {"joint_name": name, "position_rad": 0.0}
                        for name in ("joint_a", "joint_b", "joint_c")
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
                },
            )
        if step == 4:
            experiment = self.outputs["run_experiment"]
            trace = experiment["trace"]
            rows = trace["rows"]
            keys = sorted(rows[0]["values"])
            attestation = {
                "analysis_status": "completed",
                "run_id": experiment["run_id"],
                "hypothesis_id": experiment["hypothesis_id"],
                "trace_sha256": experiment["trace_sha256"],
                "trace_row_count": len(rows),
                "trace_first_time_s": rows[0]["time_s"],
                "trace_last_time_s": rows[-1]["time_s"],
                "trace_value_sums": {
                    key: math.fsum(float(row["values"][key]) for row in rows)
                    for key in keys
                },
            }
            return ModelResponse(
                output=[
                    ResponseCodeInterpreterToolCall(
                        id="ci_axis",
                        code=(
                            f"trace = {json.dumps(trace)}\n"
                            "# Compute row count, time bounds, and every per-signal sum.\n"
                            "print(analyze(trace))"
                        ),
                        container_id="container_test",
                        outputs=[OutputLogs(logs=json.dumps(attestation), type="logs")],
                        status="completed",
                        type="code_interpreter_call",
                    )
                ],
                usage=Usage(),
                response_id="response_ci",
            )
        if step == 5:
            opened = self.outputs["open_case"]
            experiment = self.outputs["run_experiment"]
            return self._function_call(
                "create_revision",
                {
                    "case_id": CASE_ID,
                    "base_revision_id": "r000",
                    "expected_base_sha256": opened["original_asset_sha256"],
                    "basis_hypothesis_id": experiment["hypothesis_id"],
                    "basis_experiment_run_id": experiment["run_id"],
                    "patch": {
                        "target": {"kind": "joint", "name": "joint_b"},
                        "attribute": "axis",
                        "expected_old_value": [0.0, 0.0, 1.0],
                        "new_value": [0.0, 1.0, 0.0],
                    },
                    "rationale": "The directional experiment isolates the authored axis.",
                    "expected_effect": {
                        "scenario_id": "public_center",
                        "predicates": [
                            {"metric": "hold_error_p95_m", "op": "lt", "value": 0.03}
                        ],
                    },
                },
            )
        if step == 6:
            return self._function_call(
                "run_task",
                {
                    "case_id": CASE_ID,
                    "revision_id": "r001",
                    "scenario_id": "public_center",
                    "capture": "metrics",
                },
            )
        if step == 7:
            revision = self.outputs["create_revision"]
            return self._function_call(
                "verify_revision",
                {
                    "case_id": CASE_ID,
                    "revision_id": "r001",
                    "expected_asset_sha256": revision["asset_sha256"],
                },
            )
        if step == 8:
            return self._function_call(
                "publish_revision",
                {
                    "case_id": CASE_ID,
                    "promotion_ticket": self.outputs["verify_revision"][
                        "promotion_ticket"
                    ],
                },
            )
        raise AssertionError("the scripted model should stop at publication approval")

    def stream_response(
        self, *args: Any, **kwargs: Any
    ) -> AsyncIterator[TResponseStreamEvent]:
        del args, kwargs

        async def empty() -> AsyncIterator[TResponseStreamEvent]:
            if False:
                yield None  # type: ignore[misc]

        return empty()


def test_agents_sdk_closes_the_real_mcp_service_loop_and_stops_before_publish(
    tmp_path: Path,
) -> None:
    service = AssetAutopsyService(tmp_path, runner=DeterministicFakeRunner())
    config = MCPRuntimeConfig(
        bearer_token="agents-sdk-test-bearer", allowed_origin="http://localhost:8712"
    )
    facade = asyncio.run(create_mcp_facade(service, config))
    model = ScriptedRepairModel()

    async def execute():
        agent = build_agent(InProcessMCPServer(facade), model=model)
        return await Runner.run(
            agent,
            EXACT_PROMPT,
            max_turns=12,
            run_config=RunConfig(tracing_disabled=True),
        )

    result = asyncio.run(execute())

    transcript = collect_run_transcript(result)
    approval_request = approval_request_from_result(result)
    evidence = evaluate_autonomy_run(transcript, approval_request)

    assert evidence["passed"] is True
    assert evidence["tool_order"] == [
        "open_case",
        "run_task",
        "inspect_asset",
        "run_experiment",
        "create_revision",
        "run_task",
        "verify_revision",
        "publish_revision",
    ]
    assert set(TOOL_NAMES).issubset(model.available_tools)
    assert "code_interpreter" in model.available_tools
    assert len(result.interruptions) == 1
    assert approval_request is not None
    assert approval_request.arguments.promotion_ticket.revision_id == "r001"
    assert facade.recorder.counts["publish_revision"] == 0
    assert service.publish_invocation_count == 0
    assert service.store.get_case(CASE_ID).qualification_state == "passed"
    assert service.store.verify_ledger()

    shallow_transcript = RunTranscript(
        tools=transcript.tools,
        code_interpreter=(
            CodeInterpreterRecord(
                index=transcript.code_interpreter[0].index,
                payload={
                    "type": "code_interpreter_call",
                    "status": "completed",
                    "outputs": [
                        {
                            "type": "logs",
                            "logs": json.dumps(
                                {
                                    "run_id": model.outputs["run_experiment"]["run_id"],
                                    "hypothesis_id": model.outputs["run_experiment"][
                                        "hypothesis_id"
                                    ],
                                    "trace_sha256": model.outputs["run_experiment"][
                                        "trace_sha256"
                                    ],
                                }
                            ),
                        }
                    ],
                },
            ),
        ),
        final_output=transcript.final_output,
    )
    assert (
        evaluate_autonomy_run(shallow_transcript, approval_request)["passed"] is False
    )
