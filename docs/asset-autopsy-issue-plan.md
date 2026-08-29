# Asset Autopsy — Luna-sized issue execution plan

Status: Linear synchronized; implementation not dispatched.
Human controls: `YU-19` (plan synchronization) and `YU-29` (final evidence, links, and submission).
Product contract: `docs/asset-autopsy-mvp-design.md`.

## 1. Why this plan exists

The product contract is unchanged. This document changes only the execution boundary used by Symphony. The previous 12 implementation issues placed several independent unknowns into AA-00, AA-06, AA-09, and AA-10. Those four are now non-dispatch umbrella parents; nine native Linear subissues carry the executable work.

The resulting plan has:

- 17 executable leaves;
- four non-dispatch umbrellas;
- one completed bootstrap issue, AA-B00;
- two relation-free, non-dispatch human control issues;
- an acyclic 28-edge blocker graph;
- effective wave width no greater than `max_concurrent_agents: 4`;
- GPT-5.6 Luna with `xhigh` reasoning and at most 12 turns per run.

Each executable leaf has one primary uncertainty, exclusive owned surfaces, protected surfaces, binary acceptance, and explicit verification. The target is the smallest complete PR that closes that leaf's understand-work-verify loop.

## 2. Dispatch safety

Current state is intentionally inert:

- every unfinished AA issue is Backlog;
- no AA issue has the `symphony` label;
- the active Symphony dispatch count is zero;
- this plan does not start, restart, or reconfigure the Mac mini service.

The `symphony` label is the dispatch switch. Linear blocker relations document and expose readiness, but do not by themselves prevent dispatch. Activate an executable leaf only with this sequence:

1. Verify every listed prerequisite PR is merged into `main`; In Review or a green unmerged PR is insufficient.
2. Verify the issue body, owned surfaces, acceptance, and dependency links still match this plan.
3. Move only that ready leaf from Backlog to Todo.
4. Add the `symphony` label last.
5. If the agent reports a blocker, remove `symphony` before changing scope or dependencies.

Never add `symphony` to an umbrella parent, `YU-19`, or `YU-29`.

## 3. Non-dispatch umbrellas

| Umbrella | Linear | Children | Human rollup condition |
|---|---|---|---|
| AA-00 | YU-7 | AA-00A, AA-00B | The risky upstream/CGL seam and the TrueForge/sandbox/approval seam are proved independently before any product implementation starts. |
| AA-06 | YU-13 | AA-06A, AA-06B | One-shot hidden qualification and digest-bound crash-safe publication are implemented as separate ownership boundaries. |
| AA-09 | YU-16 | AA-09A, AA-09B | Private runtime provisioning/direct-stack integration is proved before the real TrueForge autonomous approval run. |
| AA-10 | YU-17 | AA-10A, AA-10B, AA-10C | Security/leakage, crash recovery, and determinism/degradation are independently falsified before final evidence. |

An umbrella owns no files, branch, commit, or PR. It remains Backlog and relation-free. Mark it Done only after every child PR is merged and a human verifies its rollup condition.

`YU-19` owns this one-file planning PR. `YU-29` owns the post-hardening evidence decision, final README/demo-link PR, video upload, and submission. Both remain relation-free and never dispatch through Symphony.

## 4. Executable leaves

| Leaf | Linear | Wave | Direct blockers |
|---|---|---|---|
| AA-00A | YU-20 | Wave 0A — serial upstream kill gate | AA-B00 |
| AA-00B | YU-21 | Wave 0B — serial TrueForge kill gate | AA-00A |
| AA-02 | YU-9 | Wave 1A — parallel lower layer | AA-00B |
| AA-03 | YU-10 | Wave 1A — parallel lower layer | AA-00B |
| AA-04 | YU-11 | Wave 1A — parallel lower layer | AA-00B |
| AA-01 | YU-8 | Wave 1B — runner-dependent calibration | AA-02 |
| AA-05 | YU-12 | Wave 2A — parallel domain layer | AA-01, AA-03, AA-04 |
| AA-06A | YU-22 | Wave 2A — parallel qualification domain | AA-01, AA-03, AA-04 |
| AA-07 | YU-14 | Wave 2A — parallel facade layer | AA-01, AA-03, AA-04 |
| AA-08 | YU-15 | Wave 2A — parallel agent-configuration layer | AA-01, AA-03, AA-04 |
| AA-06B | YU-23 | Wave 2B — serial publication boundary | AA-06A |
| AA-09A | YU-24 | Wave 3A — serial direct-stack integration | AA-05, AA-06B, AA-07, AA-08 |
| AA-09B | YU-25 | Wave 3B — serial real-agent E2E | AA-09A |
| AA-10A | YU-26 | Wave 4 — parallel security gate | AA-09B |
| AA-10B | YU-27 | Wave 4 — parallel recovery gate | AA-09B |
| AA-10C | YU-28 | Wave 4 — parallel determinism gate | AA-09B |
| AA-11 | YU-18 | Wave 4 — parallel release preparation; human evidence remains later | AA-09B |

AA-B00 (`YU-5`) is already Done and is the sole predecessor of AA-00A. It is not counted among the 17 implementation leaves.

## 5. Wave activation

| Wave | Ready leaves | Maximum effective width | Gate |
|---|---|---:|---|
| 0A | AA-00A | 1 | AA-B00 merged |
| 0B | AA-00B | 1 | AA-00A merged and all four upstream gates passed |
| 1A | AA-02, AA-03, AA-04 | 3 | AA-00B merged and all five TrueForge gates passed |
| 1B | AA-01 | 1 | AA-02 merged; AA-00B already transitively satisfied |
| 2A | AA-05, AA-06A, AA-07, AA-08 | 4 | AA-01, AA-02, AA-03, and AA-04 all merged |
| 2B | AA-06B | 1 | AA-06A merged |
| 3A | AA-09A | 1 | AA-05, AA-06B, AA-07, and AA-08 all merged |
| 3B | AA-09B | 1 | AA-09A merged |
| 4 | AA-10A, AA-10B, AA-10C, AA-11 | 4 | AA-09B merged; AA-11 also requires a human-fixed project license |

AA-06B may become ready while the other Wave 2A leaves are still in review, but AA-09A waits for all four integration inputs. `YU-29` starts only after AA-10A, AA-10B, AA-10C, and AA-11 are merged and the AA-10 rollup is verified. It reuses AA-09B evidence only when the final-code Git SHA and production-tree plus all commitment digests still match; any production change requires a fresh post-hardening run.

## 6. Exact blocker graph

The following 28 `blocks` relations are the complete baseline executable AA graph. The source is the blocker; the target is the blocked issue. Human control issues are intentionally relation-free.

- `AA-B00` blocks `AA-00A`
- `AA-00A` blocks `AA-00B`
- `AA-00B` blocks `AA-02`
- `AA-00B` blocks `AA-03`
- `AA-00B` blocks `AA-04`
- `AA-02` blocks `AA-01`
- `AA-01` blocks `AA-05`
- `AA-03` blocks `AA-05`
- `AA-04` blocks `AA-05`
- `AA-01` blocks `AA-06A`
- `AA-03` blocks `AA-06A`
- `AA-04` blocks `AA-06A`
- `AA-01` blocks `AA-07`
- `AA-03` blocks `AA-07`
- `AA-04` blocks `AA-07`
- `AA-01` blocks `AA-08`
- `AA-03` blocks `AA-08`
- `AA-04` blocks `AA-08`
- `AA-06A` blocks `AA-06B`
- `AA-05` blocks `AA-09A`
- `AA-06B` blocks `AA-09A`
- `AA-07` blocks `AA-09A`
- `AA-08` blocks `AA-09A`
- `AA-09A` blocks `AA-09B`
- `AA-09B` blocks `AA-10A`
- `AA-09B` blocks `AA-10B`
- `AA-09B` blocks `AA-10C`
- `AA-09B` blocks `AA-11`

The graph is acyclic. AA-07 and AA-08 deliberately have three direct blockers each; their earlier prose-only wave gate is now machine-visible.

## 7. Ownership and integration rules

- One issue owns one branch and one PR, always based on current `main`. Stacked PRs are not part of this workflow.
- Dependency ownership is a serial Phase 0 handoff: AA-00A creates the initial `pyproject.toml` and `uv.lock`; after AA-00A merges, AA-00B exclusively owns those two files while closing the real TrueForge seam. AA-00B must preserve AA-00A's direct upstream pins and rerun the upstream 4/4 suite under the final lockfile. The dependency files freeze only after that regression and AA-00B's 5/5 gate pass and merge. Every later dependency change is a blocker, not an opportunistic edit.
- `tests/conftest.py` belongs only to AA-00A. AA-00B keeps its helpers inside `tests/phase0/trueforge/**`.
- AA-00A and AA-00B have disjoint spike, test, and evidence directories.
- AA-09A owns direct-stack provisioning tests; AA-09B owns real TrueForge denial and approval runs.
- AA-10A, AA-10B, and AA-10C have disjoint production-fix allowlists so all three may run in parallel. A validated root cause outside a lane's allowlist creates a dedicated follow-on fix leaf with exact ownership and new blocker links; the discovering lane remains incomplete until that fix merges and its regression passes.
- An agent may commit, push, and open its PR. It must not merge, mark Done, weaken acceptance, change a protected surface, or invent a product fallback.
- A valid implementation blocker stays visible in Linear with exact evidence and without the `symphony` label.

## 8. Leaf contracts

### AA-00A — Prove pinned upstream MCP, CGL, and dependency lock (`YU-20`)

Wave: Wave 0A — serial upstream kill gate
Blocked by: AA-B00
Activation gate: all of AA-B00 are merged into `main`.

Primary uncertainty: Can the exact locked upstream MCP and CGL stack satisfy the required stdio, schema, rendering, and error contracts?

Owned surfaces:

- pyproject.toml
- uv.lock
- src/asset_autopsy/__init__.py
- spikes/phase0/upstream/**
- tests/phase0/upstream/**
- tests/conftest.py
- docs/phase0-upstream-results.md

Protected surfaces:

- Any post-merge dependency closure belongs to AA-00B during Wave 0B
- TrueForge connection, sandbox, Large Tool Response, and approval probes owned by AA-00B
- Production domain behavior
- Hidden qualification and production fixture values

Acceptance:

- [ ] uv sync --frozen succeeds from the committed lockfile
- [ ] Pinned stdio server initializes and required tool schemas match the frozen contract
- [ ] CGL renders the 160 by 120 primitive scene
- [ ] A success-wrapped upstream error is detected and returned as a bounded sanitized typed error
- [ ] docs/phase0-upstream-results.md records PASS or BLOCKED_HARD_GATE for exactly these four checks
- [ ] Only 4/4 PASS may merge, complete the issue, or unblock AA-00B; BLOCKED_HARD_GATE keeps the issue incomplete with symphony removed

Required verification:

- uv sync --frozen
- uv run pytest tests/phase0/upstream -q
- git diff --check

Out of scope:

- TrueForge HTTP and approval behavior
- Large Tool Response and sandbox probes
- Production causal-service behavior

### AA-00B — Prove TrueForge HTTP, LTR, sandbox, and approval boundary (`YU-21`)

Wave: Wave 0B — serial TrueForge kill gate
Blocked by: AA-00A
Activation gate: all of AA-00A are merged into `main`.

Primary uncertainty: Can the real TrueForge runtime close the authenticated HTTP, LTR, sandbox, and approval seams without a production facade?

Owned surfaces:

- pyproject.toml
- uv.lock
- spikes/phase0/trueforge/**
- tests/phase0/trueforge/**
- docs/phase0-trueforge-results.md

Protected surfaces:

- tests/conftest.py and the upstream MCP/CGL seam owned by AA-00A
- Production seven-tool facade and causal behavior
- Hidden qualification and production fixture values

Acceptance:

- [ ] Any package or pin required by the live TrueForge probes is committed here, AA-00A's direct upstream pins remain exact and unchanged, and `uv sync --frozen` succeeds from the final Phase 0 lockfile
- [ ] The AA-00A upstream suite still passes 4/4 under that final lockfile
- [ ] Saved localhost Streamable HTTP connection enforces bearer and allowed Origin behavior
- [ ] Exactly 256 result rows reach TrueForge Large Tool Response and are analyzed from sandbox Python
- [ ] Sandbox access to the checkout, private runtime, and outbound network is measured and recorded
- [ ] The resolved AgentSpec is serial, exposes the exact seven planned tool schemas, and makes only the dummy publish probe destructive and approval-gated
- [ ] A 160 by 120 CGL image content block passes through the facade into TrueForge without exposing a host path or flooding the model context
- [ ] docs/phase0-trueforge-results.md records PASS or BLOCKED_HARD_GATE for the four TrueForge gates plus the image transport gate
- [ ] Only the upstream 4/4 regression plus TrueForge 5/5 PASS may merge, complete the issue, or unblock AA-02 through AA-04; BLOCKED_HARD_GATE keeps the issue incomplete with symphony removed

Required verification:

- uv sync --frozen
- uv run pytest tests/phase0/upstream -q
- uv run pytest tests/phase0/trueforge -q
- Run each live TrueForge Phase 0 probe and record sanitized evidence
- git diff --check

Out of scope:

- Production causal-service behavior
- Generic authentication infrastructure
- Replacing live checks with fakes

### AA-02 — Implement the pinned MuJoCo MCP client and deterministic runner (`YU-9`)

Wave: Wave 1A — parallel lower layer
Blocked by: AA-00B
Activation gate: all of AA-00B are merged into `main`.

Primary uncertainty: Can the pinned upstream child lifecycle normalize successful, failed, timed-out, and protocol-corrupt calls deterministically?

Owned surfaces:

- src/asset_autopsy/mujoco_client.py
- src/asset_autopsy/runner.py
- tests/upstream_contract/**

Protected surfaces:

- Metrics policy and fixture values
- Public MCP schemas
- Qualification logic
- pyproject.toml and uv.lock

Acceptance:

- [ ] Child environment removes unrelated secrets and sets CGL before import
- [ ] Pinned upstream commit and required schemas are verified at startup
- [ ] Only xml_string is used; no agent-controlled path or URL reaches upstream
- [ ] Runs use fresh load, reset, set-state, and constant segments
- [ ] Returned step count matches the requested count
- [ ] Wrapped errors, tracebacks, and host paths become bounded typed errors
- [ ] Failed slots are poisoned and never reused
- [ ] Render failure has one numeric-only fallback
- [ ] modify_model, reload_from_xml, diagnose_instability, and compare_trajectories are unused
- [ ] Explicit timeout handling terminates and poisons the child, and context-managed shutdown closes stdio and CGL resources cleanly

Required verification:

- uv sync --frozen
- uv run pytest tests/upstream_contract -q
- git diff --check

Out of scope:

- Metric threshold policy
- Public facade transport
- Qualification and publication

### AA-03 — Implement the evidence store and hash-chained ledger (`YU-10`)

Wave: Wave 1A — parallel lower layer
Blocked by: AA-00B
Activation gate: all of AA-00B are merged into `main`.

Primary uncertainty: Can the four-table store expose the exact atomic transaction and recovery API required by parallel downstream owners?

Owned surfaces:

- src/asset_autopsy/storage.py
- tests/unit/test_storage.py
- tests/unit/storage_helpers/**

Protected surfaces:

- Simulation and metric semantics
- XML patch policy
- HTTP/MCP server
- pyproject.toml and uv.lock

Acceptance:

- [ ] Exactly the four design tables are used
- [ ] Large XML, traces, and images remain outside SQLite
- [ ] Objects use temporary write, fsync, hash verification, and atomic rename
- [ ] Revision plus ledger event commits atomically
- [ ] Every revision persists exactly one hypothesis event and one probe run citation
- [ ] Event-chain mutation is detected
- [ ] Linear head, qualification attempt, and promotion state can be restored
- [ ] RUNNING and RECOVERING qualification states persist exact attempt identity
- [ ] No generic artifact registry, garbage collector, or distributed idempotency layer is added
- [ ] The public transaction API supports revision-plus-event atomic commit, exact qualification reserve/recover/terminal identity, and promotion receipt/reconciliation lookup without downstream storage edits

Required verification:

- uv sync --frozen
- uv run pytest tests/unit/test_storage.py -q
- git diff --check

Out of scope:

- Simulation execution
- Schema and patch semantics
- Distributed storage or exactly-once infrastructure

### AA-04 — Implement strict schemas and immutable one-attribute MJCF patching (`YU-11`)

Wave: Wave 1A — parallel lower layer
Blocked by: AA-00B
Activation gate: all of AA-00B are merged into `main`.

Primary uncertainty: Can untrusted MJCF changes be reduced to one canonical, validated attribute diff without mutating the base?

Owned surfaces:

- src/asset_autopsy/schemas.py
- src/asset_autopsy/patcher.py
- tests/unit/test_schemas.py
- tests/unit/test_patcher.py

Protected surfaces:

- MuJoCo execution
- Database and service orchestration
- Dependencies and lockfile

Acceptance:

- [ ] Exactly seven tool input/output models are frozen
- [ ] Unknown fields and non-finite inputs are rejected
- [ ] Patch is one object, never an array
- [ ] create_revision accepts exactly one basis_probe_run_id, never an array
- [ ] Only joint axis, damping, armature, and frictionloss are editable
- [ ] Base hash and expected-old-value guards are enforced
- [ ] Axis normalization and family-level safety ranges are enforced
- [ ] Fixture provisioning rejects unsafe external XML features
- [ ] Whole-document comparison catches undeclared edits
- [ ] Schema contains no fault label, golden value, target, seed, path, or slot name

Required verification:

- uv sync --frozen
- uv run pytest tests/unit/test_schemas.py tests/unit/test_patcher.py -q
- git diff --check

Out of scope:

- Simulator calls
- Service state machine
- Dependency changes

### AA-01 — Build and calibrate compound-arm-01 metrics and BehaviorDiff (`YU-8`)

Wave: Wave 1B — runner-dependent calibration
Blocked by: AA-02
Activation gate: all of AA-00B, AA-02 are merged into `main`.

Primary uncertainty: Can one fixed compound-arm fixture express two ordered faults with deterministic, margin-safe public and hidden metrics?

Owned surfaces:

- fixtures/compound-arm-01/**
- src/asset_autopsy/metrics.py
- tests/unit/test_metrics.py
- tests/fixture/**

Protected surfaces:

- pyproject.toml and uv.lock
- Engine client and storage modules
- TrueForge configuration

Acceptance:

- [ ] Primitive-only three-DOF arm exists
- [ ] Clean asset passes public and hidden calibration
- [ ] r000 fails reach and hold
- [ ] First correction passes reach but fails hold
- [ ] Final correction passes public and hidden 3/3
- [ ] Intended pass/fail values have at least 20 percent threshold margin
- [ ] p95 error, RMS speed, settling, condition hash, and first divergence have boundary tests
- [ ] The same-condition metric maximum absolute delta is no more than 1e-8
- [ ] The machine-readable calibration report and public fixture serialization contain no clean XML, hidden manifest, nonce, or exact hidden repair value
- [ ] One fixed primitive-only fixture and fixed r000, r001, and r002 revisions produce a machine-readable threshold-margin report; no controller search or additional fixture is introduced

Required verification:

- uv sync --frozen
- uv run pytest tests/unit/test_metrics.py tests/fixture -q
- git diff --check

Out of scope:

- General MJCF support
- Engine-process lifecycle
- TrueForge integration

### AA-05 — Implement the public causal loop service (`YU-12`)

Wave: Wave 2A — parallel domain layer
Blocked by: AA-01, AA-03, AA-04
Activation gate: all of AA-01, AA-02, AA-03, AA-04 are merged into `main`.

Primary uncertainty: Can causal ordering and budget accounting remain exact across domain success, failure, and partial-engine outcomes?

Owned surfaces:

- src/asset_autopsy/service.py
- tests/facade/test_causal_loop.py

Protected surfaces:

- verify_revision and publish_revision
- MCP transport and TrueForge AgentSpec
- schemas.py is consumed, not edited
- pyproject.toml and uv.lock

Acceptance:

- [ ] open_case, inspect_asset, run_task, run_probe, and create_revision are implemented
- [ ] Probe without a baseline is rejected
- [ ] Hypothesis, alternative, prediction, and falsifier are committed before engine execution
- [ ] Output contains observations and predicate matches, not a root-cause diagnosis
- [ ] Cited probe completed on the same base revision
- [ ] Patch target appears in the cited hypothesis or alternative
- [ ] Only the current linear head may be patched
- [ ] One revision changes one attribute
- [ ] Child run_task returns same-condition BehaviorDiff against its parent
- [ ] Budget accounting matches the design
- [ ] Table-driven budget tests enforce invalid request equals 0, pre-physics upstream failure equals 0, completed task or probe including domain failure equals 1, partial run records failed event plus 1, policy-valid compile rejection equals one patch attempt, and identical revision equals 0

Required verification:

- uv sync --frozen
- uv run pytest tests/facade/test_causal_loop.py -q
- git diff --check

Out of scope:

- Qualification and publication
- MCP/HTTP wiring
- TrueForge agent configuration

### AA-06A — Implement one-shot qualification and exact recovery (`YU-22`)

Wave: Wave 2A — parallel qualification domain
Blocked by: AA-01, AA-03, AA-04
Activation gate: all of AA-01, AA-02, AA-03, AA-04 are merged into `main`.

Primary uncertainty: Can one-shot hidden qualification recover infrastructure interruption without creating a second logical attempt or leaking partial output?

Owned surfaces:

- src/asset_autopsy/qualification.py
- tests/facade/test_qualification.py

Protected surfaces:

- Publication and export files owned by AA-06B
- Public causal service and MCP approval UI
- Hidden details never enter public logs or exceptions
- pyproject.toml and uv.lock

Acceptance:

- [ ] Only a public-pass linear head can begin qualification
- [ ] Current head, expected asset hash, and source, controller, contract, runner, and holdout commitments are revalidated before reserving the attempt
- [ ] Attempt ID, revision, suite commitment, and scenario hashes persist before hidden execution
- [ ] Public output contains only aggregate status and failed clause IDs; partial and private child output is never published
- [ ] Infrastructure interruption replays only the exact RECOVERING attempt
- [ ] A completed terminal failure prevents another qualification for the case
- [ ] A passing attempt prepares a qualified core containing exactly `repaired.xml`, `patch-manifest.json`, and `qualification.json`; no ledger, manifest, promotion receipt, or event bytes enter `qualified_core_sha256`
- [ ] `QUALIFICATION_PASSED` stores that already-computed `qualified_core_sha256`, and the complete promotion ticket binds the same digest for AA-06B
- [ ] Tests fail if the qualified-core member set changes or if computing its digest depends on the ledger event that records it

Required verification:

- uv sync --frozen
- uv run pytest tests/facade/test_qualification.py -q
- git diff --check

Out of scope:

- Atomic export publication
- Approval UI policy
- Generic distributed job infrastructure

### AA-07 — Expose exactly seven sanitized Asset Autopsy MCP tools (`YU-14`)

Wave: Wave 2A — parallel facade layer
Blocked by: AA-01, AA-03, AA-04
Activation gate: all of AA-01, AA-02, AA-03, AA-04 are merged into `main`.

Primary uncertainty: Can seven domain results map unambiguously to MCP success, bounded permanent error, and retryable upstream error envelopes?

Owned surfaces:

- src/asset_autopsy/server.py
- src/asset_autopsy/errors.py
- tests/facade/test_tool_surface.py
- tests/facade/test_http_boundary.py

Protected surfaces:

- AA-05 service.py and AA-06 qualification/publication files
- Frozen schemas are consumed, not edited
- TrueForge AgentSpec
- pyproject.toml and uv.lock

Acceptance:

- [ ] list_tools returns exactly seven tools
- [ ] Generic MuJoCo tools cannot be reached
- [ ] Annotations match the frozen design
- [ ] Only publish_revision is destructive
- [ ] Bearer, Origin, and localhost boundary are enforced
- [ ] Paths, slot names, raw XML, traceback, and private payloads are never returned
- [ ] Normal contract failure remains a domain result
- [ ] Contract/precondition failures use MCP isError with a bounded fixed envelope
- [ ] Service and verifier are injected through fakes; AA-05 and AA-06 files are not edited
- [ ] The fixed error table maps normal contract unmet to a success domain result; schema, hash, baseline, head, policy, ticket, and authorization violations to bounded MCP isError; and upstream unavailability to retryable MCP isError

Required verification:

- uv sync --frozen
- uv run pytest tests/facade/test_tool_surface.py tests/facade/test_http_boundary.py -q
- git diff --check

Out of scope:

- Domain-service implementation
- TrueForge instructions
- Generic authentication platform

### AA-08 — Package the TrueForge Asset Autopsy agent (`YU-15`)

Wave: Wave 2A — parallel agent-configuration layer
Blocked by: AA-01, AA-03, AA-04
Activation gate: all of AA-01, AA-02, AA-03, AA-04 are merged into `main`.

Primary uncertainty: Can one resolved TrueForge AgentSpec encode the causal-loop, LTR, serial-call, and approval policies without fixture answers?

Owned surfaces:

- skills/asset-autopsy/**
- configs/trueforge/**
- tests/agent_spec/**

Protected surfaces:

- Core Python implementation
- Hidden runtime and qualification state
- Public tool schemas
- pyproject.toml and uv.lock

Acceptance:

- [ ] Only the seven facade tools are enabled
- [ ] parallel_tool_calls is false and iteration limit is 30
- [ ] Sandbox and Large Tool Response are enabled
- [ ] Only publish_revision requires approval
- [ ] Instruction requires hypothesis, alternative, prediction, and falsifier
- [ ] Prompt and config require LTR Python analysis of trace evidence and forbid reliance on a diagnosis label; real behavior is proved only in AA-09B
- [ ] Controller/test changes and repeated denied approval requests are forbidden
- [ ] Prompt contains no fixture-specific correct value

Required verification:

- uv sync --frozen
- uv run pytest tests/agent_spec -q
- git diff --check

Out of scope:

- Core domain implementation
- Private holdout data
- Fixture-specific prompting

### AA-06B — Implement digest-bound atomic publication and reconciliation (`YU-23`)

Wave: Wave 2B — serial publication boundary
Blocked by: AA-06A
Activation gate: all of AA-06A are merged into `main`.

Primary uncertainty: Can ticket-bound publication converge to one valid bundle and receipt across the atomic-rename boundary?

Owned surfaces:

- src/asset_autopsy/publisher.py
- tests/facade/test_publication.py

Protected surfaces:

- Qualification attempt lifecycle owned by AA-06A
- Public causal service and MCP annotations
- Hidden details never enter bundles, logs, or exceptions
- pyproject.toml and uv.lock

Acceptance:

- [ ] Ticket equality binds exact revision, core, diff, results, and export name
- [ ] Forged, stale, or incomplete tickets are rejected before export
- [ ] Publication re-materializes exactly the three qualified-core files and rejects any mismatch with the `qualified_core_sha256` stored in both the ticket and `QUALIFICATION_PASSED`
- [ ] Export uses temp write, fsync, content verification, and atomic directory rename
- [ ] Only after verifying the three-file core, publication generates `ledger-through-qualification.jsonl`; it ends at `QUALIFICATION_PASSED` and is excluded from `qualified_core_sha256`
- [ ] `manifest.json` lists the qualified-core digest plus every exported file hash without feeding either the manifest or ledger back into the qualified-core digest
- [ ] Tests fail if a ledger, manifest, promotion receipt, or event is added to the qualified-core member set, or if any of the three core files changes after ticket creation
- [ ] Startup reconciliation leaves exactly one valid bundle and a PROMOTED event with its manifest hash

Required verification:

- uv sync --frozen
- uv run pytest tests/facade/test_publication.py -q
- git diff --check

Out of scope:

- Qualification execution
- Approval UI implementation
- Generic distributed publication infrastructure

### AA-09A — Provision the private runtime and prove direct-stack integration (`YU-24`)

Wave: Wave 3A — serial direct-stack integration
Blocked by: AA-05, AA-06B, AA-07, AA-08
Activation gate: all of AA-05, AA-06B, AA-07, AA-08 are merged into `main`.

Primary uncertainty: Can all real components and the private runtime be wired reproducibly before involving the autonomous agent?

Owned surfaces:

- src/asset_autopsy/bootstrap.py
- scripts/serve
- scripts/provision-demo
- scripts/reset-demo
- scripts/verify-ledger
- tests/e2e/direct_stack/**

Protected surfaces:

- Real TrueForge agent/approval E2E evidence owned by AA-09B
- Frozen public schemas, tool count, and commitment identities
- Core modules owned by earlier issues except an explicitly reported blocker

Acceptance:

- [ ] A fresh private demo runtime is provisioned outside both checkout and agent sandbox
- [ ] serve, reset-demo, and verify-ledger commands are reproducible from a clean checkout
- [ ] The real facade/service/upstream stack reaches public pass and hidden qualification without the TrueForge agent
- [ ] Source, controller, contract, runner, suite, scenario, and ledger hashes all verify
- [ ] No real approval or publication claim is made in this issue

Required verification:

- uv sync --frozen
- uv run pytest tests/e2e/direct_stack -q
- zsh scripts/provision-demo
- zsh scripts/reset-demo
- zsh scripts/verify-ledger
- git diff --check

Out of scope:

- Real TrueForge autonomous run
- Approval denial and approval publication evidence
- New product capabilities

### AA-09B — Complete the real TrueForge one-prompt approval E2E (`YU-25`)

Wave: Wave 3B — serial real-agent E2E
Blocked by: AA-09A
Activation gate: all of AA-09A are merged into `main`.

Human gate before dispatch:

- An authenticated TrueForge session is active
- A designated human is present and able to deny Run A and approve Run B during the agent turn

Primary uncertainty: Can the real agent close the full depth-two loop and real approval boundary from one prompt with zero re-prompts?

Owned surfaces:

- tests/e2e/trueforge/**
- docs/demo/evidence/trueforge/**
- Narrowly required integration or prompt fixes in skills/asset-autopsy/**
- Narrowly required integration or prompt fixes in configs/trueforge/**
- Narrowly required integration fixes in src/asset_autopsy/bootstrap.py
- Narrowly required integration fixes in src/asset_autopsy/server.py

Protected surfaces:

- Provisioning/direct-stack scripts owned by AA-09A
- Frozen public schemas, tool count, and all commitment identities
- Core modules owned by earlier issues; seam failures become blockers

Acceptance:

- [ ] Before each recorded run, sandbox unreadability and network behavior are audited without exposing private values
- [ ] Run A uses real denial and leaves zero exports
- [ ] After reset, fresh Run B completes r000 to r001 to r002 from one prompt
- [ ] Exactly two hypothesis/probe/one-attribute-patch cycles occur with zero human re-prompts
- [ ] Public gates and hidden qualification pass 3/3
- [ ] A real TrueForge approval pause occurs only for publish_revision
- [ ] The approval surface displays the complete human-readable ticket before the human acts
- [ ] Fresh Run B uses real approval and publishes only its exact qualified revision
- [ ] Evidence-chain and manifest hashes verify
- [ ] The agent never observes generic MuJoCo MCP, checkout, or private runtime

Required verification:

- uv sync --frozen
- uv run pytest tests/e2e/trueforge -q
- Run fresh real denial and approval cases and record only sanitized evidence locations
- zsh scripts/verify-ledger
- git diff --check

Out of scope:

- Provisioning implementation
- Mock approval or scripted diagnosis
- Generalizing beyond compound-arm-01

### AA-10A — Enforce contract and public-data leakage boundaries (`YU-26`)

Wave: Wave 4 — parallel security gate
Blocked by: AA-09B
Activation gate: all of AA-09B are merged into `main`.

Primary uncertainty: Does a complete public-surface sentinel corpus reveal any contract bypass or prohibited private-data leak?

Owned surfaces:

- tests/security/**
- Narrowly required fixes in src/asset_autopsy/schemas.py
- Narrowly required fixes in src/asset_autopsy/patcher.py
- Narrowly required fixes in src/asset_autopsy/server.py
- Narrowly required fixes in src/asset_autopsy/errors.py
- Narrowly required fixes in src/asset_autopsy/service.py

Protected surfaces:

- Recovery and determinism tests owned by AA-10B and AA-10C
- Fixture oracle and private runtime
- README, license, notices, and demo documentation

Acceptance:

- [ ] Arbitrary path or XML input is rejected
- [ ] A two-attribute patch is rejected
- [ ] Failure injection proves hypothesis-before-probe ordering
- [ ] All seven tool results, MCP isError envelopes, public event tails, LTR-visible payloads, and logs pass sentinel scans for golden data, holdout values, nonce, host path, secret, and traceback; intentional canonical diff values are explicitly scoped
- [ ] Any production fix is limited to the five explicitly allowed modules and a reproduced failing test
- [ ] Any validated out-of-allowlist root cause creates a dedicated follow-on fix leaf with exact ownership and blocker links; AA-10A remains incomplete until that fix merges and this regression passes

Required verification:

- uv sync --frozen
- uv run pytest tests/security -q
- uv run pytest -q
- git diff --check

Out of scope:

- Qualification/publication crash recovery
- Determinism and rendering degradation
- Broad security refactoring

### AA-10B — Prove qualification and publication crash recovery (`YU-27`)

Wave: Wave 4 — parallel recovery gate
Blocked by: AA-09B
Activation gate: all of AA-09B are merged into `main`.

Primary uncertainty: Do all qualification and publication crash windows converge to the same attempt or exactly one valid bundle and receipt?

Owned surfaces:

- tests/recovery/**
- Narrowly required fixes in src/asset_autopsy/storage.py
- Narrowly required fixes in src/asset_autopsy/qualification.py
- Narrowly required fixes in src/asset_autopsy/publisher.py

Protected surfaces:

- Security/leakage and determinism tests owned by AA-10A and AA-10C
- Frozen public tool contracts
- Fixture oracle, README, license, notices, and demo documentation

Acceptance:

- [ ] Qualification child death and lost terminal response replay only the exact RECOVERING qualification attempt
- [ ] A changed attempt, revision, suite, or scenario identity is rejected
- [ ] A forged or stale promotion ticket is rejected during recovery
- [ ] Crash before rename, after rename before PROMOTED, after PROMOTED, and invalid-temp cases each reconcile to exactly one valid bundle plus one receipt or to no promotion as required
- [ ] Any production fix is limited to the three explicitly allowed modules and a reproduced failing test
- [ ] Any validated out-of-allowlist root cause creates a dedicated follow-on fix leaf with exact ownership and blocker links; AA-10B remains incomplete until that fix merges and this regression passes

Required verification:

- uv sync --frozen
- uv run pytest tests/recovery -q
- uv run pytest -q
- git diff --check

Out of scope:

- Public-data leakage
- Numeric determinism and rendering degradation
- Generic job or distributed transaction infrastructure

### AA-10C — Prove determinism and numeric degradation recovery (`YU-28`)

Wave: Wave 4 — parallel determinism gate
Blocked by: AA-09B
Activation gate: all of AA-09B are merged into `main`.

Primary uncertainty: Do same-condition numeric runs remain deterministic while renderer and stdio process failures degrade safely?

Owned surfaces:

- tests/determinism/**
- Narrowly required fixes in src/asset_autopsy/mujoco_client.py
- Narrowly required fixes in src/asset_autopsy/runner.py
- Narrowly required fixes in src/asset_autopsy/metrics.py

Protected surfaces:

- Security/leakage and recovery tests owned by AA-10A and AA-10B
- Fixture thresholds and hidden qualification oracle
- README, license, notices, and demo documentation

Acceptance:

- [ ] One NO_RENDER restart after rendering failure preserves the numeric probe and repair loop
- [ ] A stdio death makes the current call retryable, discards the poisoned slot, and lets a later separate call use a new slot
- [ ] With identical XML, controller, scenario, seed, timestep, initial state, upstream commit, and architecture, two fresh-process metric sets differ by no more than 1e-8
- [ ] Qualification and public metrics remain independent of rendered pixels
- [ ] Any production fix is limited to the three explicitly allowed modules and a reproduced failing test
- [ ] Any validated out-of-allowlist root cause creates a dedicated follow-on fix leaf with exact ownership and blocker links; AA-10C remains incomplete until that fix merges and this regression passes

Required verification:

- uv sync --frozen
- uv run pytest tests/determinism -q
- uv run pytest -q
- git diff --check

Out of scope:

- Fixture retuning
- Security and publication recovery
- General performance or simulator-pool redesign

### AA-11 — Produce release documentation, notices, and demo runbook (`YU-18`)

Wave: Wave 4 — parallel release preparation; human evidence remains later
Blocked by: AA-09B
Activation gate: all of AA-09B are merged into `main`.

Human gate before dispatch:

- A human-selected repository SPDX license is recorded before dispatch

Primary uncertainty: Can final lockfile licenses and verified public-only evidence produce accurate release artifacts without making unproved claims?

Owned surfaces:

- README.md
- LICENSE
- THIRD_PARTY_NOTICES.md
- docs/demo/**
- docs/release-checklist.md
- scripts/generate-notices
- scripts/build-demo-report

Protected surfaces:

- Production core and tests outside narrow documentation plumbing
- Private manifest, hidden values, raw traces, secrets, and host paths
- Final human-recorded evidence and submission action

Acceptance:

- [ ] A human has fixed the repository license choice before dispatch; the agent does not infer MIT from a recommendation
- [ ] Dependency inventory is generated from the final lockfile
- [ ] Direct MIT and Apache obligations are preserved
- [ ] Nova code and assets are absent
- [ ] README calls the result a contract-bounded case study, not a general 3D doctor
- [ ] Report generation accepts verified E2E artifacts and excludes private data
- [ ] Three-minute edited runbook preserves real event order and normal-speed approval
- [ ] Full uncut-run link has an explicit placeholder
- [ ] The release checklist names YU-29 as the sole final-evidence owner and lists merged AA-10A, AA-10B, AA-10C, and AA-11 PRs plus a verified AA-10 rollup as its preconditions
- [ ] Demo-report generation accepts only evidence bound to the final-code Git SHA and current production-tree, source, controller, contract, runner, and holdout digests; any production change since AA-09B requires a fresh post-hardening E2E

Required verification:

- uv sync --frozen
- uv run pytest -q
- Run the documented notice generator and review generated direct/transitive licenses
- Run the documented demo-report generator against verified synthetic/public evidence
- git diff --check

Out of scope:

- Final screen recording, upload, and submission
- Core production fixes
- Claims beyond the verified compound-arm-01 case study

## 9. Human gates

The following choices and effects remain human-owned:

- choose the repository SPDX license before AA-11 dispatch; MIT is a recommendation in the product design, not a selected license;
- review and merge every PR;
- grant or deny the real TrueForge publication approval in AA-09B;
- verify the four umbrella rollups;
- use `YU-29` to verify or record post-hardening evidence, replace README/demo placeholders through a human-controlled final-link PR, edit and upload the videos, and submit the entry.

## 10. Final human control contract

### YU-29 — Record final evidence, publish links, and submit

## Outcome

After every implementation and release-preparation PR is merged, a human selects or records evidence matching the current commitments, publishes the real demo links, lands the final-link update, and submits the hackathon entry.

## Preconditions

- AA-10A, AA-10B, AA-10C, and AA-11 PRs are merged into main.
- The AA-10 umbrella rollup is human-verified.
- The repository SPDX license is fixed.
- The final-code Git SHA and production-tree digest are recorded before any final-link-only PR.
- The selected evidence manifest matches that final-code SHA plus current production-tree, source, controller, contract, runner, holdout, and export digests; any production change since AA-09B requires a fresh post-hardening run.

## Human-owned work

- Grant or deny any real TrueForge publication approval.
- Record or verify the final uncut run.
- Edit and upload the three-minute video.
- Replace README and demo-document placeholders with the real verified links in one human-controlled final-link PR.
- Verify the edited event order against the uncut evidence and ledger.
- Submit the final entry and record the submission confirmation.

## Dispatch safety

- Keep this issue Backlog until all preconditions pass.
- Never add the symphony label.
- Do not assign this issue to Symphony; browser approval, recording, upload, merge, and submission remain human actions.

## Acceptance

- [ ] Final-code Git SHA and production-tree digest are recorded before the final-link PR.
- [ ] Selected evidence matches that SHA and every current production/commitment digest; any production change since AA-09B has triggered a fresh post-hardening E2E.
- [ ] The full regression suite passes on the recorded final-code SHA.
- [ ] Real approval outcome and exact published revision are visible in the uncut evidence.
- [ ] README contains the real uncut-run and three-minute video links with no placeholder.
- [ ] The final-link PR is merged and its diff contains no private data.
- [ ] Edited video order matches the verified ledger and shows approval at normal speed.
- [ ] Submission confirmation and public URLs are recorded without secrets or private runtime data.

## Verification

- Record git rev-parse HEAD for the final-code main commit and the production-tree digest from the verified manifest.
- Run uv sync --frozen and uv run pytest -q on that commit.
- Run zsh scripts/verify-ledger for the selected final evidence.
- Run the demo-report generator and verify its source/production commitments match the final-code SHA.
- Scan the report, links, PR diff, and videos for private data.
- Run git diff --check on the final-link PR.

## 11. Private-data boundary

Never place a secret, nonce, hidden target, seed, golden XML, host path, raw private trace, or private runtime value in Linear, Git, PR output, CI artifacts, logs, LTR-visible payloads, or videos. The demo private runtime remains outside both the checkout and the TrueForge sandbox. Unit and CI tests use synthetic non-demo private fixtures.

## 12. Definition of done

An executable leaf is ready for human review only when:

- every acceptance checkbox has direct evidence;
- required focused and regression verification passes, or an unavailable check is explicitly recorded;
- protected surfaces remain unchanged;
- agent-visible output passes the private-data scan;
- the PR targets `main` and contains only that leaf's coherent change;
- Linear records the commit SHA, commands and results, skipped checks, scan result, and PR URL.

Further splitting is appropriate only when implementation evidence reveals a second independent uncertainty, a cross-owner fix, or a leaf that cannot finish its understand-work-verify loop within the 12-turn budget. Do not split one coherent calibration or real-agent E2E proof merely to reduce line count.
