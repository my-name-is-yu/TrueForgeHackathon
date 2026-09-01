# Asset Autopsy

Asset Autopsy is a tools-first robot design harness for one bounded MuJoCo case. It tests a
specific claim: when Codex and a person share precise observation, experiment, revision,
comparison, and qualification tools, they can close the design loop without a prescribed
workflow.

The current milestone uses `compound-arm-01`. Codex receives a goal and safety boundaries, then
chooses what to inspect, which hypothesis to test, how to analyze the resulting trace, and what
single attribute to change. The browser is a visual design surface, not a second chat client.

## Closed-loop contract

The harness enforces invariants instead of tool order:

- every result names the exact immutable revision and asset hash it describes;
- a revision can cite only a completed finite experiment from its current base;
- hypotheses, conditions, selected signals, and observations are machine-readable;
- every child changes exactly one permitted joint attribute;
- child task results include a same-condition parent `BehaviorDiff`;
- simulation failures remain distinct from public requirement failures;
- qualification uses one committed hidden suite and returns aggregates only;
- final acceptance is a visible human-only action bound to the successful promotion ticket.

## Architecture

```text
Codex conversation <-> WebMCP tools in the live page
                         |
Human <-> Three.js workbench + one-change shared draft
                         |
                 AssetAutopsyService
                    |-- immutable revisions and evidence ledger
                    |-- one-attribute patcher and hidden verifier
                    v
             pinned stdio MuJoCo MCP -> MuJoCo 3.5.0
```

`AssetAutopsyService` owns robot-specific policy and evidence integrity. Physics execution stays
in a private commit-pinned MuJoCo child process. A generic six-tool Streamable HTTP MCP facade is
also retained as an integration boundary for non-browser clients; it does not own an agent loop.

## Local design workbench

The workbench is local-first and visitor-isolated. Each browser session receives a temporary
service, evidence ledger, draft, traces, feedback, and qualification lifecycle that disappear
when the server stops. The server retains up to eight sessions and evicts the least recently used
idle session when a ninth is created. Reset replaces only that browser session with a fresh case.

```bash
cd web
npm ci
npm run build
cd ..
uv run uvicorn asset_autopsy.workbench:create_workbench_app --factory --host 127.0.0.1 --port 8713
```

Open `http://127.0.0.1:8713` in the Codex desktop browser. The visual editor remains usable when
WebMCP is unavailable and shows a compatibility banner in that case. During frontend development,
run `npm run dev` in `web/` alongside the Python server and open `http://127.0.0.1:5173`.

The shared draft previews exactly one `axis`, `damping`, `armature`, or `frictionloss` change. It
does not touch the immutable revision ledger. Codex must cite a completed experiment from the same
base before converting the draft into a revision. Subjective human feedback is bound to the exact
current revision and hash. Successful hidden qualification locks editing; only the visible human
**Accept** button can accept the promotion ticket. Accept is deliberately not a site tool.

The registered WebMCP tools are `get_design_context`, `inspect_design`, `run_task`,
`run_experiment`, `query_trace`, `set_draft_patch`, `create_revision_from_draft`,
`verify_revision`, and `record_design_feedback`. Full experiment traces are available to the
visible UI API; Codex uses the bounded `query_trace` operations (`sample`, `min_max`, `delta`,
`sum`, and `settling`).

## Streamable HTTP MCP tools

| Tool | Capability |
| --- | --- |
| `open_case` | Read requirements, topology, current head, budgets, history, and patch policy. |
| `inspect_asset` | Inspect authored and compiled public values for an exact revision. |
| `run_task` | Measure a revision and compare a child with its parent under the same condition. |
| `run_experiment` | Test a preregistered causal hypothesis using selected controls and observables. |
| `create_revision` | Create one immutable current-head child bound to experiment evidence. |
| `verify_revision` | Recheck the public pass and consume the one-shot hidden qualification. |

`compound-arm-01` starts with these budgets:

| Resource | Budget |
| --- | ---: |
| Public task and experiment runs | 10 |
| Agent-defined experiments | 5 |
| Immutable child revisions | 2 |
| Hidden qualification attempts | 1 |

`run_experiment` accepts one to sixteen constant-control segments totaling 256 to 100,000 steps
and one to eight selected observables. A completed run returns an evenly sampled 256-row trace,
condition and execution hashes, plus the hypothesis and run IDs needed to support a revision. It
does not diagnose the cause or decide whether the hypothesis was satisfied.

## Local verification

Requirements are Python 3.12, `uv`, Node.js 22, and MuJoCo CGL support for the render gate.

```bash
uv sync --frozen
uv run pytest -q -m "not cgl"
uv run ruff check .
uv run ruff format --check .
git diff --check
cd web && npm test && npm run build && npm audit --audit-level=high
```

On a CGL-capable Mac, also run:

```bash
uv run pytest -q -m cgl
uv run pytest -q
```

The deterministic suites prove the service and browser contracts. A final end-to-end design trial
should additionally be run from Codex against the live page using only a goal and the exposed
capabilities. See [the implemented design](docs/asset-autopsy-mvp-design.md).

## Current limits

This milestone intentionally supports one fixed simulated arm and local browser sessions. It does
not include deployment, durable accounts, arbitrary CAD or URDF import, multiple robots, FEA, real
hardware, or post-accept export/materialization.
