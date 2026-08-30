# Asset Autopsy

Asset Autopsy is a tools-first 3D repair harness built on TrueForge for the
[Agent Harness Hackathon](https://www.wemakedevs.org/hackathons/trueforge). An agent must
observe a failing MuJoCo asset, choose experiments that distinguish competing causal
explanations, choose how to analyze the resulting 256-row traces in the TrueForge Sandbox,
and support every single-attribute revision with that evidence.

SC1 is a submission candidate, not a published product. Its accepted product contract ends at
TrueForge's `tool.approval_required` event for `publish_revision`: the submission does not click
approval and makes no publication-materialization claim. This contract is recorded in
[`docs/decisions/sc1-approval-request-endpoint.md`](docs/decisions/sc1-approval-request-endpoint.md).
Its real-model gate is complete only when a sanitized evidence record from the fixed one-prompt
run proves every acceptance check below. Unit, integration, and scripted event-evaluator tests
exercise the contracts but do not stand in for that run. If the saved provider, TrueForge,
MuJoCo, or review services block the gate, the pull request stays draft and reports the
reproducible blocker; it must not present a scripted success as real-model evidence.

## Architecture

```text
TrueForge 0.1.4 + saved asset-autopsy-sc1 agent
    |
    | exact seven tools, Streamable HTTP, bearer + allowed Origin
    v
Asset Autopsy MCP facade (127.0.0.1:8712/mcp)
    |
    | strict schema/auth/error mapping; direct Python calls
    v
AssetAutopsyService
    |-- immutable compound-arm-01 fixture
    |-- evidence store, metrics, one-attribute patcher, hidden verifier
    |
    | private child process; no public TCP port
    v
pinned stdio MuJoCo MCP -> MuJoCo 3.5.0
```

The HTTP handler does not invoke a CLI. It validates the request boundary and calls the Python
domain service directly. Only physics execution is delegated to the private, commit-pinned
MuJoCo MCP child. TrueForge remains the general agent harness: it supplies the model loop,
Large Tool Response handling, Sandbox execution, saved session state, and the approval
pause.

SC1 deliberately does not add a product CLI, a Skill, a TrueForge fork, a separate UI, arbitrary
3D formats, site observations, or automatic qualification recovery. The developer-only
`scripts/run_sc1_e2e.py` entry point drives and evaluates the fixed submission run; it is not an
agent-facing product interface.

## Public tool contract

The saved agent can resolve exactly these seven Asset Autopsy tools:

| Tool | Contract |
| --- | --- |
| `open_case` | Return the pre-provisioned case contract, budgets, topology, current head, and patch policy. |
| `inspect_asset` | Return authored and compiled public values without fault labels, repair advice, hidden values, XML, or host paths. |
| `run_task` | Execute the fixed public scenario. A child revision also returns a same-condition parent `BehaviorDiff`. |
| `run_experiment` | Preregister a claim, competing explanation, prediction, and falsifier, then run one bounded agent-defined experiment. |
| `create_revision` | Create one immutable child from a completed experiment by changing one allowed joint attribute. |
| `verify_revision` | Run the public gate and the one-shot three-scenario hidden qualification, returning aggregates and a promotion ticket only. |
| `publish_revision` | Request human approval for the exact qualified revision. This remains the only destructive and approval-gated tool; accepted SC1 stops at TrueForge's approval request before the server call. |

Post-approval materialization and promotion persistence have been removed. If an approved request
reaches the domain boundary, it fails closed with the sanitized, non-retryable
`PUBLICATION_DEFERRED` error and performs no storage or filesystem mutation.

The demo fixture has budgets of 10 total runs, 5 experiments, 2 child revisions, and 1 hidden
qualification. `run_experiment` requires every hinge joint and every position actuator exactly
once in each applicable input. For `compound-arm-01`, all joint positions and control values must
stay within the ranges advertised by `open_case` (`-1.2` to `1.2` radians/control units). The
model may choose one to sixteen constant-control segments totaling 256 to 100,000 simulation
steps and one to eight public observables. The tool returns a deterministic, evenly sampled
256-row trace for Sandbox analysis. Every row is a named object with `time_s` and a `values`
mapping, using canonical keys such as `qpos:<joint>`, `energy:potential`,
`body_position:<body>:x`, and `control:<actuator>`. The tool does not decide whether the agent's
prediction or falsifier was satisfied.

## Safety and approval boundary

- The facade binds only to `127.0.0.1`, requires a per-run bearer token, and accepts only the
  configured loopback TrueForge Origin.
- All seven argument schemas reject unknown top-level fields. Public requests cannot supply
  XML, file paths, URLs, seeds, timesteps, hidden targets, or controller/test replacements.
- Public errors are bounded and sanitized. The private fixture, raw hidden scenarios, host paths,
  and upstream exceptions are not tool results.
- `verify_revision` exposes public `1/1`, hidden `n/3`, violated public clause IDs, and the bound
  ticket. It does not expose hidden targets or individual hidden traces.
- The AgentSpec uses high reasoning effort, disables parallel tool calls, limits the run to 30
  iterations, enables Sandbox file downloads and Large Tool Response, preloads the MCP, and
  requires approval only for `publish_revision`.
- The accepted demonstration stops at `tool.approval_required`. No `publish_revision` response or
  domain invocation may exist at that point, and post-approval materialization is not implemented.
  Do not approve the call during the submission run.
- If hidden qualification is interrupted, the case fails closed. SC1 does not retry it; start a
  fresh case for another evidence run.

## Fresh local setup

Requirements:

- macOS with a working MuJoCo CGL renderer for the render gate
- Node.js 22.14 or later
- Python 3.12
- `uv`
- a saved OpenAI provider in the normal TrueForge standalone runtime with
  `openai/gpt-5-4-mini` available
- the existing saved `hackathon-starter` agent, which provisioning verifies but does not modify

From a fresh checkout of the SC1 branch:

```bash
npm ci
uv sync --frozen
```

Do not commit provider keys, MCP bearer tokens, TrueForge's standalone database, or generated
private runtime data. TrueForge's local state lives outside this repository under
`~/Library/Application Support/trueforge/`.

## Reproduce the verification suite

Run the same dependency, upstream, TrueForge boundary, and repository checks used for the
submission candidate:

```bash
npm ci
uv sync --frozen
uv run pytest tests/phase0/upstream -q
uv run pytest tests/phase0/trueforge -q
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
git diff --check
```

GitHub CI runs the frozen install and full repository checks on a CGL-capable hosted macOS
runner for pull requests and pushes to `main`. It does not load provider secrets or run the
real-model evidence driver; that remains a post-merge exact-`main` gate.

The Phase 0 TrueForge test exercises its original transport placeholder and historical measured
boundary. The SC1 contract is the generic `run_experiment` flow implemented by the current
domain, MCP, integration, and E2E event-evaluator tests. Neither Phase 0 nor a fully green local
suite is a claim that the saved real model completed SC1.

## Run the real one-prompt gate

Start the pinned normal TrueForge runtime in one terminal:

```bash
npm run trueforge
```

Keep it on `localhost:8790` with the saved provider configured. In a second terminal, run the
developer evidence driver:

```bash
uv run python scripts/run_sc1_e2e.py
```

The driver is expected to create an isolated fresh runtime root and bearer, start the SC1 MCP on
`127.0.0.1:8712/mcp`, provision only the dedicated `asset-autopsy-sc1` agent and connector, and
send exactly:

> Autopsy compound-arm-01. Do not change its controller or tests. Qualify and publish the repaired asset.

The gate passes only if the resulting raw TrueForge events prove all of the following before
sanitization:

1. The first public task fails.
2. The model chooses competing hypotheses, experiment conditions, observables, Sandbox analysis,
   and patches within the public budgets and patch policy.
3. Before each revision, the cited current-base experiment completes, its trace is offloaded by
   Large Tool Response, and a successful Sandbox analysis occurs after the offload.
4. Each revision cites that run and hypothesis, returns one matching canonical diff, and reconciles
   with the completed ledger run and stored trace hash.
5. The final public task passes and its `BehaviorDiff` says `public_pass` with a real change.
6. Qualification reports public `1/1` and hidden `3/3` without hidden details.
7. The qualified `publish_revision` request produces the matching approval-required event and no
   tool response; the facade and domain publish invocation counts remain zero.
8. No bearer, private path, fixture XML, hidden target, or hidden trace leaked into the event or
   Sandbox boundary.

Only a sanitized artifact that records the cited run and hypothesis hashes, observed tool sequence,
Sandbox executions, approval event, zero publish-call counters, and exact Git commit can support a
PASS claim. If the driver or evidence artifact is absent, or any check fails, report SC1 as
draft/blocked.

The three-minute walkthrough is in
[`docs/sc1-demo-runbook.md`](docs/sc1-demo-runbook.md). Development and review rules, including
current-head Codex/Qodo review and the human-only merge decision, remain in
[`AGENTS.md`](AGENTS.md).
