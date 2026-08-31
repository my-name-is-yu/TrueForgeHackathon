# Asset Autopsy autonomy milestone

Status: implemented design

Date: 2026-08-31

Primary case: `compound-arm-01`

## Product claim

Asset Autopsy tests whether a robot-repair loop can become operationally closed without forcing a
model through a scripted workflow. The agent receives a goal and protected boundaries. It chooses
what to inspect, which competing explanation to test, how to excite the system, which signals to
analyze, what single attribute to change, and when to verify.

The harness enforces identity, causality, bounded mutation, comparison, qualification, and human
approval. It does not enforce a tool order or supply a diagnosis.

```text
goal-only request
    <-> public observations and requirements
    <-> agent-defined experiments and trace analysis
    <-> immutable evidence-backed one-attribute revisions
    <-> same-condition BehaviorDiff
    <-> one-shot hidden qualification
    <-> human approval pause
```

This is a narrow proof for one existing MuJoCo asset, not a general robot-design platform.

## Responsibility boundaries

TrueForge owns the Sol model loop, generic Sandbox, Large Tool Response handling, session state,
and approval pause. The Streamable HTTP facade owns transport authentication, strict public
schemas, tool annotations, and sanitized error mapping. `AssetAutopsyService` owns the fixture,
budgets, evidence lineage, patch policy, public evaluation, and hidden qualification. A private
commit-pinned MuJoCo MCP child owns physics execution.

Neither the saved instructions nor the user prompt names an Asset Autopsy tool, a faulty element,
an experiment, a trace-analysis method, an expected number of revisions, or a required order.

## Fixed case and budgets

`compound-arm-01` contains three hinge joints and three position actuators. The public patch
allowlist is `joint.axis`, `joint.damping`, `joint.armature`, and `joint.frictionloss`.

| Resource | Initial budget |
| --- | ---: |
| Task and experiment runs | 10 |
| Agent-defined experiments | 5 |
| Child revisions | 2 |
| Hidden qualification attempts | 1 |

A successful promotion ticket binds the complete one-to-two entry change history and the final
asset hash.

## Public capability contract

- `open_case` exposes declared requirements, observable topology, current head, budgets, history,
  and permitted changes.
- `inspect_asset` exposes authored and compiled public values for an exact revision without fault
  labels, raw XML, hidden data, or repair advice.
- `run_task` evaluates the fixed public scenario. Child responses contain the parent comparison,
  including first trace divergence, metric deltas, clause outcomes, and verdict.
- `run_experiment` records a hypothesis, competing explanation, prediction, falsifier, complete
  initial joint state, bounded control segments, and selected observables. A completed run returns
  a 256-row named trace and provenance IDs without diagnosing the result.
- `create_revision` accepts only the current head, exact base hash, completed finite current-base
  experiment, owning hypothesis, and one allowlisted attribute patch.
- `verify_revision` requires a public-passing current child, independently reruns the public case,
  checks stored identities and lineage, and consumes the committed hidden suite once.
- `publish_revision` is destructive and approval-gated. The accepted endpoint is the matching
  approval request; post-approval materialization is intentionally absent.

Errors distinguish invalid inputs, stale evidence, public requirement failures, non-finite design
outcomes, upstream simulation failures, stored-evidence corruption, and exhausted budgets. Raw
upstream details and hidden qualification conditions do not cross the public boundary.

## Evidence binding

Every experiment records its revision, asset hash, condition hash, execution fingerprint, trace
hash, hypothesis ID, and ledger events. Revision creation revalidates that:

1. the cited base is the current head and its hash matches;
2. the cited run completed finitely on that same base;
3. the hypothesis ID owns that experiment;
4. the changed target and attribute were preregistered by the hypothesis or its competitor;
5. the patch changes exactly one permitted authored value;
6. the patched model is accepted by the pinned simulator before commit.

`run_task` stores the complete evaluation trace privately and returns public metrics. For a child,
it reruns or retrieves the matching parent condition and returns a machine-validated
`BehaviorDiff`. Old results therefore cannot silently become evidence for a new revision.

## Autonomy evaluation

`scripts/run_autonomy_eval.py` runs three independent goal-only attempts. Each receives a new
temporary data root, service, bearer, and TrueForge session. The saved agent is provisioned with
serial tool calls, high reasoning effort, generic Sandbox access, Large Tool Response, and human
approval for `publish_revision`.

The event evaluator accepts additional exploration, differing valid call orders, and corrected
invalid requests. It requires causal temporal relationships rather than a recipe:

- each committed child follows a completed current-base experiment;
- successful Sandbox output reports the corresponding offloaded trace's run ID, hypothesis ID,
  and trace hash before the child;
- the revision response proves one matching canonical diff;
- the final public head passes with a changed `public_pass` comparison;
- qualification returns public `1/1` and hidden `3/3`;
- the exact ticket reaches the final approval request with no response or server invocation;
- the ledger, facade calls, service calls, stored trace hashes, and revision provenance reconcile;
- no private value crosses the raw event boundary.

At least two of the three attempts must satisfy every gate. The evidence artifact contains the
exact Git commit, model, prompt hash, AgentSpec hash, tool-schema hash, per-attempt gate results,
tool counts, revision and experiment counts, and hashed evidence identifiers. It contains no raw
trace, credential, private path, fixture XML, or hidden scenario.

## Verification

```bash
uv run pytest -q -m "not cgl"
uv run pytest -q -m cgl
uv run pytest -q
uv run ruff check .
uv run ruff format --check .
git diff --check
```

After committing the exact candidate and starting the configured TrueForge runtime:

```bash
uv run python scripts/run_autonomy_eval.py
```

A green deterministic suite is necessary but is not a substitute for at least two real-model
successes recorded by that driver.

## Deferred work

WebMCP, a browser workbench, public hosting, multiple robot families, arbitrary CAD/URDF import,
FEA, real hardware actions, and post-approval publication remain outside this milestone.
