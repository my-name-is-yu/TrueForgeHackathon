# Decision: evaluation ends at the Agents SDK approval interruption

Status: Accepted
Date: 2026-08-31
Decision issue: [#31](https://github.com/my-name-is-yu/TrueForgeHackathon/issues/31)

## Decision

The accepted autonomy-evaluation contract ends when the OpenAI Agents SDK returns a
`RunResult` containing one `publish_revision` item in `interruptions`. The submission does not
approve or resume that `RunState`, invoke the domain publisher, or claim a publication response,
receipt, bundle, or public artifact.

Expose `publish_revision` as a local `function_tool(needs_approval=True)` and keep the server-side
tool filtered from the agent. This demonstrates the SDK's real approval pause while preventing an
MCP call from bypassing it.

## Consequence

Post-approval materialization and promotion persistence remain absent. The evaluation stops before
the local function runs, and facade plus service invocation counts must remain zero. The approval
interruption proves the human boundary without claiming that publication has occurred.
