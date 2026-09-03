# Asset Autopsy Codex and WebMCP milestone

Status: implemented design

Date: 2026-09-02

Primary case: `compound-arm-01`

## Product claim

Asset Autopsy tests whether a human and Codex can design a robot through a shared visual and
machine-operable environment without forcing a scripted workflow. Codex receives a goal and
protected boundaries. It chooses what to inspect, which competing explanation to test, how to
excite the system, which signals to analyze, what single attribute to change, and when to verify.

The harness enforces identity, causality, bounded mutation, comparison, and qualification. It does
not enforce a tool order, supply a diagnosis, or duplicate Codex and Git review controls.

```text
goal in Codex
    <-> WebMCP observations and requirements
    <-> agent-defined experiments and trace queries
    <-> shared one-change draft
    <-> immutable evidence-backed revisions and BehaviorDiff
    <-> one-shot hidden qualification
    <-> qualified evidence inspected by the human in the same page
```

This is a narrow proof for one existing MuJoCo asset, not a general robot-design platform.

## Responsibility boundaries

The top-level Three.js workbench registers eight composable WebMCP capabilities and owns temporary
visitor-isolated sessions, visible design state, a single shared uncommitted patch, visible
experiment traces, the current canonical revision change, and parent-to-child `BehaviorDiff`
evidence. Codex remains in the external conversation; the browser does not embed another agent,
chat loop, feedback history, source-history UI, or approval workflow. Conversation feedback,
source edits, review, revert, commit, and merge remain with Codex and Git.

`AssetAutopsyService` owns the fixture, budgets, evidence lineage, patch policy, public evaluation,
and hidden qualification. A private commit-pinned MuJoCo MCP child owns physics execution. The
generic six-tool Streamable HTTP MCP facade owns transport authentication, strict public schemas,
tool annotations, and sanitized error mapping, but it does not run a model or expose acceptance.

Drafting never mutates the evidence ledger. `create_revision_from_draft` requires experiment
evidence from the exact draft base, and `query_trace` gives Codex bounded trace operations while
the complete trace remains available to the visible UI. The UI binds visible trace, revision, and
comparison evidence to their run, revision, and asset identities rather than attaching stale
results to a refreshed head.

A successful hidden qualification seals that case generation and locks editing while its evidence
remains readable. The local workbench stores the returned promotion ticket internally and does not
expose it in browser context or a site-tool result. It is not a page-level approval token. Ordinary
Reset replaces the service/store generation with a fresh editable case and clears the draft,
traces, latest task result, and qualification lock.

A hidden-suite failure also seals the one-shot case generation. The browser shows
`Qualification failed — reset required` instead of silently returning the editor to an actionable
state after the qualification budget is consumed; evidence stays readable until Reset.

## Fixed case and budgets

`compound-arm-01` contains three hinge joints and three position actuators. The public patch
allowlist is `joint.axis`, `joint.damping`, `joint.armature`, and `joint.frictionloss`.

| Resource | Initial budget |
| --- | ---: |
| Task and experiment runs | 10 |
| Agent-defined experiments | 5 |
| Child revisions | 2 |
| Hidden qualification attempts | 1 |

A successful promotion ticket binds the complete one-to-two entry evidence chain and final asset
hash. The workbench retains it only to represent the sealed qualification state until Reset.

## Capability contract

The WebMCP surface composes the same domain operations for a live page: design context and
inspection, public task execution, generic experiments, bounded trace queries, a shared draft,
evidence-backed revision creation, and qualification. It remains exactly eight tools. The context
returns the current head identity and canonical diff instead of exposing the full internal
revision/event history, feedback, approval state, or promotion-ticket digest.

The generic Streamable HTTP MCP surface retains six tools:

- `open_case` exposes requirements, topology, current head, budgets, history, and permitted changes.
- `inspect_asset` exposes authored and compiled public values for one exact revision.
- `run_task` evaluates the fixed public scenario and returns a parent `BehaviorDiff` for children.
- `run_experiment` records a falsifiable hypothesis, bounded controls, and selected observables.
- `create_revision` accepts only exact current-base evidence and one allowlisted attribute patch.
- `verify_revision` rechecks the public result and consumes the committed hidden suite once.

Errors distinguish invalid inputs, stale evidence, public requirement failures, non-finite design
outcomes, upstream simulation failures, stored-evidence corruption, and exhausted budgets. Raw
upstream details and hidden qualification conditions do not cross either public boundary.

## Evidence binding

Every experiment records its revision, asset hash, condition hash, execution fingerprint, trace
hash, hypothesis ID, and ledger events. Revision creation revalidates that:

1. the cited base is the current head and its hash matches;
2. the cited run completed finitely on that same base;
3. the hypothesis ID owns that experiment;
4. the changed target and attribute were preregistered by the hypothesis or competitor;
5. the patch changes exactly one permitted authored value;
6. the patched model is accepted by the pinned simulator before commit.

`run_task` stores the complete evaluation trace privately and returns public metrics. For a child,
it reruns or retrieves the matching parent condition and returns a machine-validated
`BehaviorDiff`. The visible evidence view presents that comparison alongside the exact canonical
revision change, while experiment traces remain bound to their source run and revision. Old results
therefore cannot silently become evidence for a new revision or appear as current UI evidence.

## Verification

```bash
uv sync --frozen
uv run pytest -q -m "not cgl"
uv run ruff check .
uv run ruff format --check .
git diff --check
cd web
npm ci
npm test
npm run build
npm audit --audit-level=high
cd ..
```

On a CGL-capable Mac, also run `uv run pytest -q -m cgl` and the unfiltered suite. Then serve the
built `web/dist` artifact through the local Python workbench on `127.0.0.1:8713`; the Vite
development proxy is not the release-path proof.

From the Codex desktop browser, give Codex only the design goal and protected boundaries. Do not
supply a diagnosis, tool sequence, or patch value. Observe the full communication path in the live
page: eight registered tools and `r000`; an agent-chosen experiment and queried trace; a visible
uncommitted draft; the evidence-backed child revision and canonical change; its same-condition
parent `BehaviorDiff`; and qualification showing `Qualified — editing locked`. Confirm the page has
no Accept, Reject, feedback-history, activity-history, or promotion-ticket UI. Exercise Reset from
that qualified state and confirm the same visitor receives a fresh editable `r000` with no draft,
trace, latest task result, or qualification lock.

## Deferred work

Public hosting, durable accounts, multiple robot families, arbitrary CAD/URDF import, FEA, real
hardware actions, and asset export/materialization remain outside this milestone. The temporary
workbench evidence chain is not materialized as asset files or Git-backed asset history.
