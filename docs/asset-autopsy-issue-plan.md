# Asset Autopsy issue-driven implementation plan

Bootstrap rule: create only AA-B00 before its acceptance criteria are complete; create downstream
issues only after the control-plane PR is merged and applied on the Mac mini

Date: 2026-08-29

Source of truth: docs/asset-autopsy-mvp-design.md

## 1. Decision

Use 13 Linear issues:

- one human-controlled bootstrap issue;
- one sequential Phase 0 kill gate;
- four parallel lower-layer issues;
- four parallel domain/integration issues;
- one complete E2E issue;
- two parallel finish issues.

This is intentionally fewer than a component-per-file plan. More issues would increase PR review,
Qodo, merge, and stale-base overhead without shortening the critical path.

Symphony is configured for an upper bound of four agents:

~~~yaml
agent:
  max_concurrent_agents: 4
  max_concurrent_agents_by_state:
    Todo: 1
    "In Progress": 4
  max_turns: 12
~~~

Todo: 1 staggers clone, dependency install, and app-server startup. Agents move their issue to
In Progress before editing, so Symphony ramps toward four instead of cold-starting four clones at
once.

Four is a scheduling ceiling, not a promise to keep four agents busy. Normal effective concurrency
is three. CGL, real TrueForge, qualification, publication, and full E2E work run one at a time.

## 2. Control-plane prerequisite

Symphony clones SOURCE_REPO_URL from remote main for every issue. No implementation issue may be
created or dispatched until the control-plane PR has put these artifacts on origin/main and the
Mac mini has applied the rendered workflow:

- docs/asset-autopsy-mvp-design.md;
- docs/asset-autopsy-issue-plan.md;
- the four-agent Symphony workflow;
- the README concurrency policy.

The bootstrap issue is tracked in Linear but does not receive the symphony label. It is completed
from the canonical checkout through normal review.

## 3. Dependency graph

~~~text
AA-B00  Land design and four-agent control plane          [human-controlled]
  |
  v
AA-00   Prove Phase 0 integration gates                  [effective concurrency 1]
  |
  +----------+-------------+-------------+
  v          v             v             v
AA-01      AA-02         AA-03         AA-04             [wave width 4]
fixture    engine        evidence      schema/patcher
metrics    runner        ledger
  |          |             |             |
  +----------+-------------+-------------+
             |
  +----------+-------------+-------------+
  v          v             v             v
AA-05      AA-06         AA-07         AA-08             [wave width 4]
causal     qualify/      MCP facade    TrueForge agent
service    publish
  |          |             |             |
  +----------+-------------+-------------+
             |
             v
AA-09   Complete one-prompt E2E                           [effective concurrency 1]
  |
  +------------------------+
  v                        v
AA-10                    AA-11                           [wave width 2]
critical hardening       release/demo/notices
  |                        |
  +------------------------+
             |
             v
Human final run, approval, video, and submission
~~~

Linear blocker links are documentation only. Symphony treats In Review as terminal and does not
wait for a prerequisite PR to merge. The real dispatch gate is:

1. prerequisite PR is merged into main;
2. next issue is moved to Todo;
3. only then is the symphony label added.

## 4. Shared issue contract

Every implementation issue uses these rules:

- one issue equals one branch and one PR;
- branch prefix is yu/;
- base is main;
- prerequisite means merged into main, not merely In Review;
- owned paths are exclusive within a wave;
- tests for the owned behavior are part of the same issue;
- agents do not merge or mark Done;
- design disagreements become a Linear blocker, not an improvised contract change;
- dependency or lockfile changes belong to AA-00; later agents must report a blocker instead;
- shared files pyproject.toml, uv.lock, schemas.py, service.py, server.py, tests/conftest.py,
  README.md, and license files have exactly one owner at a time;
- no secret, nonce, hidden target, seed, golden XML, host path, or private trace is placed in
  Linear, Git, PR output, CI artifacts, or videos.

If an agent is blocked and remains In Progress, remove its symphony label so it does not consume a
slot while a human decision is pending.

## 5. Issues

### AA-B00 — Land the implementation contract and four-agent Symphony control plane

Execution: human-controlled; do not add the symphony label.

Owned paths:

- docs/asset-autopsy-mvp-design.md
- docs/asset-autopsy-issue-plan.md
- symphony/WORKFLOW.md.template
- README.md

Acceptance:

- Design and issue plan are present in a fresh origin/main clone.
- max_concurrent_agents is 4.
- Todo cap is 1 and In Progress cap is 4.
- README explains wave-gated labels and human merge.
- git diff --check passes.
- Mac mini renders the new runtime workflow after merge.
- The service observes the new limit through hot reload or a verified restart.

Out of scope:

- Asset Autopsy implementation.
- Creating or activating downstream issues before all AA-B00 acceptance criteria are complete.

### AA-00 — Prove the TrueForge–facade–MuJoCo Phase 0 seams

Dependencies: AA-B00 merged.

Dispatch: this is the only issue with the symphony label.

Owned paths:

- pyproject.toml
- uv.lock
- src/asset_autopsy/__init__.py
- spikes/phase0/**
- tests/phase0/**
- tests/conftest.py
- docs/phase0-results.md

Acceptance:

- Frozen Python environment installs from the lockfile.
- TrueForge calls a localhost Streamable HTTP facade.
- The facade initializes the pinned MuJoCo MCP over stdio and lists required tools.
- CGL renders a 160 by 120 primitive model.
- A success-wrapped upstream error is detected and sanitized.
- A 256-row result reaches TrueForge Large Tool Response and is analyzed from sandbox Python.
- Sandbox access to checkout, private runtime, and outbound network is measured and recorded.
- Bearer and Origin checks work from the saved TrueForge connection.
- Only publish_revision causes a real approval pause.
- Any failed gate leaves the issue In Progress and prevents AA-01 through AA-04 dispatch.

Out of scope:

- Production domain behavior.
- Hidden qualification.
- General infrastructure abstractions.

### AA-01 — Build and calibrate compound-arm-01 metrics and BehaviorDiff

Dependencies: AA-00 merged.

Owned paths:

- fixtures/compound-arm-01/**
- src/asset_autopsy/metrics.py
- tests/unit/test_metrics.py
- tests/fixture/**

Acceptance:

- Primitive-only three-DOF arm exists.
- Clean asset passes public and hidden calibration.
- r000 fails reach and hold.
- First correction passes reach but fails hold.
- Final correction passes public and hidden 3/3.
- Intended pass/fail values have at least 20 percent threshold margin.
- p95 error, RMS speed, settling, condition hash, and first divergence have boundary tests.
- A repeated numeric run stays within the design tolerance.
- Clean XML, hidden manifest, nonce, and exact repair values are absent from agent-visible output.

Protected:

- pyproject.toml and uv.lock.
- Engine client and storage modules.
- TrueForge configuration.

### AA-02 — Implement the pinned MuJoCo MCP client and deterministic runner

Dependencies: AA-00 merged.

Owned paths:

- src/asset_autopsy/mujoco_client.py
- src/asset_autopsy/runner.py
- tests/upstream_contract/**

Acceptance:

- Child environment removes unrelated secrets and sets CGL before import.
- Pinned upstream commit and required schemas are verified at startup.
- Only xml_string is used; no agent-controlled path or URL reaches upstream.
- Runs use fresh load, reset, set-state, and constant segments.
- Returned step count must match the requested count.
- Wrapped errors, tracebacks, and host paths become bounded typed errors.
- Failed slots are poisoned and never reused.
- Render failure has one numeric-only fallback.
- modify_model, reload_from_xml, diagnose_instability, and compare_trajectories are unused.

Protected:

- Metrics policy and fixture values.
- Public MCP schemas.
- Qualification logic.

### AA-03 — Implement the evidence store and hash-chained ledger

Dependencies: AA-00 merged.

Owned paths:

- src/asset_autopsy/storage.py
- tests/unit/test_storage.py
- tests/unit/storage_helpers/**

Acceptance:

- Exactly the four design tables are used.
- Large XML, traces, and images remain outside SQLite.
- Objects use temporary write, fsync, hash verification, and atomic rename.
- Revision plus ledger event commits atomically.
- Every revision persists exactly one hypothesis event and one probe run citation.
- Event-chain mutation is detected.
- Linear head, qualification attempt, and promotion state can be restored.
- RUNNING and RECOVERING qualification states persist exact attempt identity.
- No generic artifact registry, garbage collector, or distributed idempotency layer is added.

Protected:

- Simulation and metric semantics.
- XML patch policy.
- HTTP/MCP server.

### AA-04 — Implement strict schemas and immutable one-attribute MJCF patching

Dependencies: AA-00 merged.

Owned paths:

- src/asset_autopsy/schemas.py
- src/asset_autopsy/patcher.py
- tests/unit/test_schemas.py
- tests/unit/test_patcher.py

Acceptance:

- Exactly seven tool input/output models are frozen.
- Unknown fields and non-finite inputs are rejected.
- Patch is one object, never an array.
- create_revision accepts exactly one basis_probe_run_id, never an array.
- Only joint axis, damping, armature, and frictionloss are editable.
- Base hash and expected-old-value guards are enforced.
- Axis normalization and family-level safety ranges are enforced.
- Fixture provisioning rejects unsafe external XML features.
- Whole-document comparison catches undeclared edits.
- Schema contains no fault label, golden value, target, seed, path, or slot name.

Protected:

- No MuJoCo execution.
- No database or service orchestration.
- No dependency changes.

### AA-05 — Implement the public causal loop service

Dependencies: AA-01, AA-02, AA-03, and AA-04 merged.

Owned paths:

- src/asset_autopsy/service.py
- tests/facade/test_causal_loop.py

Acceptance:

- open_case, inspect_asset, run_task, run_probe, and create_revision are implemented.
- Probe without a baseline is rejected.
- Hypothesis, alternative, prediction, and falsifier are committed before engine execution.
- Output contains observations and predicate matches, not a root-cause diagnosis.
- Cited probe completed on the same base revision.
- Patch target appears in the cited hypothesis or alternative.
- Only the current linear head may be patched.
- One revision changes one attribute.
- Child run_task returns same-condition BehaviorDiff against its parent.
- Budget accounting matches the design.

Protected:

- verify_revision and publish_revision.
- MCP transport and TrueForge AgentSpec.
- Schemas are consumed, not edited.

### AA-06 — Implement one-shot qualification and atomic publication

Dependencies: AA-01, AA-02, AA-03, and AA-04 merged.

Owned paths:

- src/asset_autopsy/qualification.py
- src/asset_autopsy/publisher.py
- tests/facade/test_qualification.py
- tests/facade/test_publication.py

Acceptance:

- Only a public-pass head can begin qualification.
- Attempt ID, revision, suite commitment, and scenario hashes persist before hidden execution.
- Agent sees only 3/3 aggregate and failed clause IDs.
- Infrastructure interruption replays only the exact RECOVERING attempt.
- A completed failure prevents a new case qualification.
- Qualified core hash has no ledger self-reference.
- Ticket binds the exact revision, core, diff, results, and export name.
- Forged ticket is rejected.
- Export directory is atomically renamed and startup reconciliation is tested.
- Exported ledger ends at QUALIFICATION_PASSED; PROMOTED stores final manifest hash.

Protected:

- Public causal service.
- MCP annotations and approval UI.
- Hidden details never enter public logs or exceptions.

### AA-07 — Expose exactly seven sanitized Asset Autopsy MCP tools

Dependencies: AA-00 and AA-04 merged. Activate in the second four-issue wave.

Owned paths:

- src/asset_autopsy/server.py
- src/asset_autopsy/errors.py
- tests/facade/test_tool_surface.py
- tests/facade/test_http_boundary.py

Acceptance:

- list_tools returns exactly seven tools.
- Generic MuJoCo tools cannot be reached.
- Annotations match the frozen design.
- Only publish_revision is destructive.
- Bearer, Origin, and localhost boundary are enforced.
- Paths, slot names, raw XML, traceback, and private payloads are never returned.
- Normal contract failure remains a domain result.
- Contract/precondition failures use MCP isError with a bounded fixed envelope.
- Service and verifier are injected through fakes; AA-05 and AA-06 files are not edited.

### AA-08 — Package the TrueForge Asset Autopsy agent

Dependencies: AA-00 and AA-04 merged. Activate in the second four-issue wave.

Owned paths:

- skills/asset-autopsy/**
- configs/trueforge/**
- tests/agent_spec/**

Acceptance:

- Only the seven facade tools are enabled.
- parallel_tool_calls is false and iteration limit is 30.
- Sandbox and Large Tool Response are enabled.
- Only publish_revision requires approval.
- The instruction requires hypothesis, alternative, prediction, and falsifier.
- The agent analyzes trace evidence instead of trusting a diagnosis label.
- Controller/test changes and repeated denied approval requests are forbidden.
- The prompt contains no fixture-specific correct value.

Protected:

- Core Python implementation.
- Hidden runtime and qualification state.

### AA-09 — Wire provisioning and the complete one-prompt E2E

Dependencies: AA-05, AA-06, AA-07, and AA-08 merged.

Dispatch: run alone because it owns shared runtime, CGL, TrueForge, and approval.

Owned paths:

- src/asset_autopsy/bootstrap.py
- scripts/serve
- scripts/provision-demo
- scripts/reset-demo
- scripts/verify-ledger
- tests/e2e/**

Acceptance:

- Fresh case completes r000 to r001 to r002.
- Two hypothesis/probe/patch cycles occur with zero human re-prompts.
- Public and hidden qualification pass.
- Source, controller, contract, runner, and holdout commitments remain unchanged.
- Before approval and after denial, no export exists.
- Approval publishes only the qualified revision.
- Evidence chain and manifest hashes verify.
- Agent cannot observe generic MuJoCo MCP, checkout, or private runtime.
- Approval is not mocked or replaced by a script.
- If compound depth 2 fails, the issue blocks instead of silently changing the product claim.

### AA-10 — Close critical recovery, leakage, and determinism gates

Dependencies: AA-09 merged.

Owned paths:

- tests/security/**
- tests/recovery/**
- tests/determinism/**
- narrowly required fixes in existing core modules

Acceptance:

- Arbitrary path/XML, two-attribute patch, and forged ticket are rejected.
- Failure injection proves hypothesis-before-probe ordering.
- Qualification child death performs exact RECOVERING replay.
- Crash around export rename reconciles one valid bundle.
- Numeric loop survives rendering failure.
- No root-cause, private value, path, or secret leaks.
- Fresh numeric replay remains within tolerance.
- Valid critical Qodo findings owned by this issue are fixed.
- No general 3D, auth platform, or distributed-system scope is introduced.

### AA-11 — Produce release documentation, notices, and demo runbook

Dependencies: AA-09 merged. It may run beside AA-10 but does not claim final evidence.

Owned paths:

- README.md
- LICENSE
- THIRD_PARTY_NOTICES.md
- docs/demo/**
- docs/release-checklist.md
- scripts/generate-notices
- scripts/build-demo-report

Acceptance:

- Dependency inventory is generated from the final lockfile.
- Direct MIT and Apache obligations are preserved.
- Nova code and assets are absent.
- README calls the result a contract-bounded case study, not a general 3D doctor.
- Report generation accepts verified E2E artifacts and excludes private data.
- Three-minute edited runbook preserves real event order and normal-speed approval.
- Full uncut-run link has an explicit placeholder.
- Final evidence recording remains a human gate after AA-10 and AA-11 merge.

## 6. Wave dispatch protocol

### Bootstrap

1. Create only AA-B00 in Backlog without the symphony label.
2. Complete and merge AA-B00.
3. On the Mac mini, update the checkout and run zsh scripts/symphony render.
4. Confirm .symphony/runtime/WORKFLOW.md contains the four-agent limits.
5. Confirm hot reload in logs; restart only if the running process did not reload.
6. Create AA-00 through AA-11 in Backlog without the symphony label.

### Wave 0

- Move AA-00 to Todo and add symphony.
- Do not label any other issue.

### Wave 1

After AA-00 is merged:

- move AA-01 through AA-04 to Todo;
- add symphony to those four;
- watch the ramp at effective three before allowing all four to run heavy tests.

### Wave 2

After AA-01 through AA-04 are all merged:

- move AA-05 through AA-08 to Todo;
- add symphony to those four;
- only AA-05 and AA-06 may run simulator-heavy integration; their real CGL checks must not overlap.

### Wave 3

After AA-05 through AA-08 are all merged:

- move AA-09 to Todo and add symphony;
- run it alone.

### Wave 4

After AA-09 is merged:

- move AA-10 and AA-11 to Todo;
- add symphony to both;
- merge AA-10 before producing the final human-recorded evidence.

## 7. Linear issue body template

~~~markdown
## Outcome

<One externally observable result.>

## Source of truth

- docs/asset-autopsy-mvp-design.md sections <x, y>
- docs/asset-autopsy-issue-plan.md section <issue>
- If implementation evidence conflicts with the frozen contract, stop and report a blocker.

## Preconditions

- Blocked by: <issue aliases>
- Start only after every prerequisite PR is merged into main.

## Owned surfaces

- <paths this issue may change>

## Protected surfaces

- <paths/contracts this issue may inspect but not change>

## Integration contract

Inputs:

- <types, hashes, state>

Outputs:

- <types, public values, effects>

Failure semantics:

- domain outcome: <expected failure>
- typed error: <invalid call>
- recovery: <allowed retry>

Invariants:

- <must remain true>

## Acceptance criteria

- [ ] <binary production result>
- [ ] <boundary/failure behavior>
- [ ] <protected surface remains unchanged>
- [ ] <agent-visible output contains no prohibited data>

## Required verification

- uv sync --frozen
- uv run pytest <owned tests> -q
- git diff --check

Record in Linear:

- commit SHA;
- commands and results;
- skipped or unavailable verification;
- private-data scan result;
- PR URL.

## Private data

- Do not write secrets, nonce, hidden target, seed, golden XML, host path, or raw private trace
  to Linear, Git, PR output, Actions artifacts, or videos.
- Demo private runtime must be outside both Git checkout and TrueForge sandbox.

## Out of scope

- <explicit exclusions>
- unrelated cleanup;
- speculative abstractions.

## Blocker policy

If completion requires changing a protected surface, dependency, or public contract, keep the issue
In Progress, remove the symphony label, and report the exact blocker. Do not broaden scope.

## PR handoff

- Branch: yu/<linear-id>-<short-name>
- Base: main
- One issue, one PR
- Agent may commit, push, and open the PR
- Agent must not merge or mark Done
~~~

## 8. Operational checks before using all four slots

The Mac mini was unreachable by SSH during planning, so its current capacity has not been freshly
verified. Begin with three effective agents and increase to four only after checking:

- memory pressure is not warning or critical;
- swapouts do not continuously rise;
- sufficient disk remains for four shallow clones and Python environments;
- no codex app-server exits or repeated Symphony retries;
- dependency installation is not lock-stalled;
- CGL and real TrueForge tests are not overlapping;
- reviewer/Qodo throughput can merge the current wave before the next one.

If the Mac mini has only 8 GB RAM, cap effective concurrency at 2. With 16 GB, ramp from 3 to 4.
Even on a larger machine, keep the hackathon ceiling at 4 because shared runtime and review
contention dominate beyond that point.
