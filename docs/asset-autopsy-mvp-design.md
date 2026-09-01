# Asset Autopsy Codex and WebMCP milestone

Status: implemented design

Date: 2026-09-01

Primary case: `compound-arm-01`

## Product claim

Asset Autopsy tests whether a human and Codex can design a robot through a shared visual and
machine-operable environment without forcing a scripted workflow. Codex receives a goal and
protected boundaries. It chooses what to inspect, which competing explanation to test, how to
excite the system, which signals to analyze, what single attribute to change, and when to verify.

The harness enforces identity, causality, bounded mutation, comparison, qualification, and human
acceptance. It does not enforce a tool order or supply a diagnosis.

```text
goal in Codex
    <-> WebMCP observations and requirements
    <-> agent-defined experiments and trace queries
    <-> shared one-change draft
    <-> immutable evidence-backed revisions and BehaviorDiff
    <-> one-shot hidden qualification
    <-> human-only Accept
```

This is a narrow proof for one existing MuJoCo asset, not a general robot-design platform.

## Responsibility boundaries

The top-level Three.js workbench registers nine composable WebMCP capabilities and owns temporary
visitor-isolated sessions, visible design state, a single shared uncommitted patch, subjective
revision-bound feedback, and the final human-only Accept action. Codex remains in the external
conversation; the browser does not embed another agent or chat loop.

`AssetAutopsyService` owns the fixture, budgets, evidence lineage, patch policy, public evaluation,
and hidden qualification. A private commit-pinned MuJoCo MCP child owns physics execution. The
generic six-tool Streamable HTTP MCP facade owns transport authentication, strict public schemas,
tool annotations, and sanitized error mapping, but it does not run a model or expose acceptance.

Drafting never mutates the evidence ledger. `create_revision_from_draft` requires experiment
evidence from the exact draft base, and `query_trace` gives Codex bounded trace operations while
the complete trace remains available to the visible UI. A successful hidden qualification locks
editing. Accept validates the exact promotion ticket through the service and records only this
temporary browser session's accepted state; it is not registered as WebMCP or Streamable HTTP MCP.

## Fixed case and budgets

`compound-arm-01` contains three hinge joints and three position actuators. The public patch
allowlist is `joint.axis`, `joint.damping`, `joint.armature`, and `joint.frictionloss`.

| Resource | Initial budget |
| --- | ---: |
| Task and experiment runs | 10 |
| Agent-defined experiments | 5 |
| Child revisions | 2 |
| Hidden qualification attempts | 1 |

A successful promotion ticket binds the complete one-to-two entry change history and final asset
hash. The ticket remains part of qualification because the human Accept boundary validates it.

## Capability contract

The WebMCP surface composes the same domain operations for a live page: design context and
inspection, public task execution, generic experiments, bounded trace queries, a shared draft,
evidence-backed revision creation, qualification, and revision-bound human feedback.

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
`BehaviorDiff`. Old results therefore cannot silently become evidence for a new revision.

## Verification

```bash
uv run pytest -q -m "not cgl"
uv run ruff check .
uv run ruff format --check .
git diff --check
cd web && npm test && npm run build && npm audit --audit-level=high
```

On a CGL-capable Mac, also run `uv run pytest -q -m cgl` and the unfiltered suite. After the
deterministic gates, exercise the live page from Codex with a goal-only request and confirm the
human can observe, edit, give feedback, and exclusively perform final acceptance.

## Deferred work

Public hosting, durable accounts, multiple robot families, arbitrary CAD/URDF import, FEA, real
hardware actions, and post-accept export/materialization remain outside this milestone.
