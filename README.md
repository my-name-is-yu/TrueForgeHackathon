# Asset Autopsy

Asset Autopsy is a tools-first robot repair harness for one bounded MuJoCo case. It tests a
specific claim: when an agent has precise observation, experiment, revision, comparison, and
qualification tools, it can choose how to close the repair loop without receiving a prescribed
procedure.

The current milestone uses `compound-arm-01` and `gpt-5.6-sol`. The agent receives only a
goal and safety boundaries. Its saved instructions do not name tools, prescribe their order,
identify faulty attributes, specify an experiment, or explain how to analyze a trace.

## Closed-loop contract

The agent may choose its own order and number of observations and experiments. The harness
enforces invariants instead:

- every result names the exact immutable revision and asset hash it describes;
- a revision can cite only a completed finite experiment from its current base;
- hypotheses, conditions, selected signals, and observations are machine-readable;
- every child changes exactly one permitted joint attribute;
- child task results include a same-condition parent `BehaviorDiff`;
- simulation failures remain distinct from public requirement failures;
- qualification uses one committed hidden suite and returns aggregates only;
- publication stops at a human approval request before any domain side effect.

## Architecture

```text
OpenAI Agents SDK Runner + asset-autopsy-autonomy agent
    |
    | seven discoverable tools; goal-only prompt
    v
Asset Autopsy MCP facade (127.0.0.1:8712/mcp)
    |
    | strict schemas, auth, annotations, sanitized errors
    v
AssetAutopsyService
    |-- immutable compound-arm-01 revisions
    |-- evidence ledger and content-addressed objects
    |-- one-attribute patcher and hidden verifier
    v
pinned stdio MuJoCo MCP -> MuJoCo 3.5.0
```

The Agents SDK supplies the model loop, hosted Code Interpreter, tracing, run state, and a real
approval interruption. Asset Autopsy owns robot-specific policy and evidence integrity. Physics
execution stays in a private commit-pinned MuJoCo child process.

## Public tools

| Tool | Capability |
| --- | --- |
| `open_case` | Read declared requirements, topology, current head, budgets, history, and patch policy. |
| `inspect_asset` | Inspect authored and compiled public values for an exact revision. |
| `run_task` | Measure a revision against the public scenario and compare a child with its parent. |
| `run_experiment` | Test a preregistered causal hypothesis using agent-selected controls and observables. |
| `create_revision` | Create one immutable current-head child bound to a completed experiment and base hash. |
| `verify_revision` | Recheck the public pass and consume the one-shot three-scenario hidden qualification. |
| `publish_revision` | Request human approval for the exact successful qualification ticket. |

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

## Autonomy evaluation

The real-model gate runs three independent attempts and requires at least two complete successes.
Every attempt uses a fresh temporary service root, evidence ledger, bearer, and Agents SDK run.
The exact user request is:

> Repair compound-arm-01 so it satisfies its declared requirements, then submit the qualified
> revision for human approval.

The evaluator grades observable outcomes and evidence, not a fixed tool sequence or experiment
count. A successful attempt must prove:

1. the final head passes the public task with a changed `public_pass` `BehaviorDiff`;
2. every revision has one matching diff and completed current-base experiment provenance;
3. a completed Code Interpreter call reports the corresponding trace's run ID, hypothesis ID,
   trace hash, row/time bounds, and per-signal sums derived from every row before each revision;
4. the hidden suite passes `3/3` without exposing its conditions;
5. the exact qualification ticket reaches `RunResult.interruptions` with no publish response;
6. facade and domain publish invocation counts remain zero;
7. the ledger verifies and no bearer, private path, fixture XML, or hidden condition leaked.

Extra exploration and rejected invalid requests are allowed within the public budgets. The
sanitized aggregate is written to `evidence/autonomy-eval.json` only after two successes. Fewer
than two successes invalidate stale PASS evidence and write `evidence/autonomy-blocker.json`.

Run the exact-head evaluation with `OPENAI_API_KEY` set:

```bash
uv run python scripts/run_autonomy_eval.py
```

Do not approve the final request during an evaluation.

## Local verification

Requirements are Python 3.12, `uv`, an OpenAI API key with access to `gpt-5.6-sol`, and MuJoCo
CGL support for the render gate. The Agents SDK is pinned to an exact upstream commit because the
approval-capable API used here has not yet reached the compatible stable release line.

```bash
uv sync --frozen
uv run pytest -q -m "not cgl"
uv run ruff check .
uv run ruff format --check .
git diff --check
```

On a CGL-capable Mac, also run:

```bash
uv run pytest -q -m cgl
uv run pytest -q
```

Tests prove the deterministic contracts but do not substitute for the three real-model attempts
on the recorded Git commit. See [the implemented design](docs/asset-autopsy-mvp-design.md) and
[the approval boundary decision](docs/decisions/approval-request-endpoint.md) for details.

## Current limits

This milestone intentionally supports one fixed simulated arm. It does not yet include WebMCP,
a browser UI, arbitrary CAD or URDF import, multiple robots, FEA, real hardware, or post-approval
publication materialization.
