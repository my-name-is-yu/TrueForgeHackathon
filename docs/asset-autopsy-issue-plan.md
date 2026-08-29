# Asset Autopsy — Luna-sized issue execution plan

Status: Linear synchronized; implementation not dispatched.
Human controls: `YU-19` (plan synchronization) and `YU-29` (final evidence, links, and submission).
Product contract: `docs/asset-autopsy-mvp-design.md`.

## 1. Why this plan exists

The MVP scope and behavior are unchanged. This document changes the execution boundary used by Symphony; the companion design receives only the review-proven export-name and destination-parent-fsync errata recorded by YU-19. The previous 12 implementation issues placed several independent unknowns into AA-00, AA-06, AA-09, and AA-10. Those four became non-dispatch umbrella parents. Review then separated AA-05's shared exact-byte commitment primitive from causal-service behavior, and split AA-12's three independently falsifiable release-harness seams into evidence-provenance, Git-binding, and manifest-CLI children.

The resulting plan has:

- 21 executable leaves;
- six non-dispatch umbrellas with 14 native subissues;
- one completed bootstrap issue, AA-B00;
- two relation-free, non-dispatch human control issues;
- an acyclic 33-edge blocker graph;
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
| AA-05 | YU-34 | AA-05A, AA-05B | One exact-byte five-field commitment primitive is shared by qualification, runtime provisioning, and final evidence without reimplementation; the causal service only reads the stored pre-provisioned identity. |
| AA-06 | YU-13 | AA-06A, AA-06B | One-shot hidden qualification and digest-bound crash-safe publication are implemented as separate ownership boundaries. |
| AA-09 | YU-16 | AA-09A, AA-09B | Private runtime provisioning/direct-stack integration is proved before the real TrueForge autonomous approval run. |
| AA-10 | YU-17 | AA-10A, AA-10B, AA-10C | Security/leakage, crash recovery, and determinism/degradation are independently falsified before final evidence. |
| AA-12 | YU-30 | AA-12A, AA-12B, AA-12C | Evidence provenance and Git/final-link state are verified independently before the canonical manifest CLI integrates them without reimplementation. |

An umbrella owns no files, branch, commit, or PR. It remains Backlog and relation-free. Mark it Done only after every child PR is merged and a human verifies its rollup condition.

`YU-19` owns this planning and bounded contract-errata PR. `YU-29` owns the post-hardening evidence decision, final README/demo-link PR, video upload, and submission. Both remain relation-free and never dispatch through Symphony.

## 4. Executable leaves

| Leaf | Linear | Wave | Direct blockers |
|---|---|---|---|
| AA-00A | YU-20 | Wave 0A — serial upstream kill gate | AA-B00 |
| AA-00B | YU-21 | Wave 0B — serial TrueForge kill gate | AA-00A |
| AA-02 | YU-9 | Wave 1A — parallel lower layer | AA-00B |
| AA-03 | YU-10 | Wave 1A — parallel lower layer | AA-00B |
| AA-04 | YU-11 | Wave 1A — parallel lower layer | AA-00B |
| AA-01 | YU-8 | Wave 1B — runner-dependent calibration | AA-02 |
| AA-05A | YU-35 | Wave 1C — exact commitment primitive | AA-01 |
| AA-05B | YU-12 | Wave 2A — parallel domain layer | AA-01, AA-03, AA-04 |
| AA-06A | YU-22 | Wave 2A — parallel qualification domain | AA-03, AA-04, AA-05A |
| AA-07 | YU-14 | Wave 2A — parallel facade layer | AA-01, AA-03, AA-04 |
| AA-08 | YU-15 | Wave 2A — parallel agent-configuration layer | AA-01, AA-03, AA-04 |
| AA-06B | YU-23 | Wave 2B — serial publication boundary | AA-06A |
| AA-09A | YU-24 | Wave 3A — serial direct-stack integration | AA-05B, AA-06B, AA-07, AA-08 |
| AA-09B | YU-25 | Wave 3B — serial real-agent E2E | AA-09A |
| AA-10A | YU-26 | Wave 4A — parallel security gate | AA-09B |
| AA-10B | YU-27 | Wave 4A — parallel recovery gate | AA-09B |
| AA-10C | YU-28 | Wave 4A — parallel determinism gate | AA-09B |
| AA-12A | YU-31 | Wave 4A — parallel evidence-provenance harness | AA-09B |
| AA-12B | YU-32 | Wave 4A — parallel Git/final-link harness | AA-09B |
| AA-12C | YU-33 | Wave 4B — serial manifest/CLI integration | AA-12A, AA-12B |
| AA-11 | YU-18 | Wave 4C — release preparation; human evidence remains later | AA-12C |

AA-B00 (`YU-5`) is already Done and is the sole predecessor of AA-00A. It is not counted among the 21 implementation leaves.

## 5. Wave activation

| Wave | Ready leaves | Maximum effective width | Gate |
|---|---|---:|---|
| 0A | AA-00A | 1 | AA-B00 merged |
| 0B | AA-00B | 1 | AA-00A merged and all four upstream gates passed |
| 1A | AA-02, AA-03, AA-04 | 3 | AA-00B merged and all five TrueForge gates passed |
| 1B | AA-01 | 1 | AA-02 merged; AA-00B already transitively satisfied |
| 1C | AA-05A | 1 | AA-01 merged; AA-02 already transitively satisfied |
| 2A | AA-05B, AA-06A, AA-07, AA-08 | 4 | AA-01, AA-03, and AA-04 merged make AA-05B, AA-07, and AA-08 ready; AA-05A merged additionally makes AA-06A ready; host-capacity gate before activating a fourth leaf |
| 2B | AA-06B | 1 | AA-06A merged |
| 3A | AA-09A | 1 | AA-05B, AA-06B, AA-07, and AA-08 all merged |
| 3B | AA-09B | 1 | AA-09A merged |
| 4A | AA-10A, AA-10B, AA-10C, AA-12A, AA-12B | 4 | AA-09B merged; five leaves are ready but no more than four are activated |
| 4B | AA-12C | 1 | AA-12A and AA-12B merged; may use a freed slot while a hardening leaf finishes |
| 4C | AA-11 | 1 | AA-12C merged; AA-11 also requires a human-fixed project license |

AA-06B may become ready while the other Wave 2A leaves are still in review, but AA-09A waits for AA-05B, AA-06B, AA-07, and AA-08. In Wave 4A, activate AA-12A and AA-12B plus at most two hardening leaves first; activate the remaining hardening leaf when a slot frees. `YU-29` starts only after AA-10A, AA-10B, AA-10C, AA-12A, AA-12B, AA-12C, and AA-11 are merged and all six umbrella rollups are verified. It reuses evidence only when the AA-12C CLI binds the AA-12A provenance result and AA-12B Git result; any invalidation requires a fresh post-hardening run and regenerated manifest.

### Host-capacity gate for width four

`max_concurrent_agents: 4` is a scheduling ceiling, not permission to activate four leaves blindly. Before Wave 2A or Wave 4A, the human dispatcher records a fresh capacity checkpoint in Linear:

- total RAM and current macOS memory-pressure state;
- whether swapouts continuously rise during the three-agent ramp;
- free disk versus four measured shallow-clone plus frozen-environment footprints, with at least 20 percent headroom;
- zero unexpected `codex app-server` exits or repeated Symphony retries during the ramp;
- no dependency-install lock stall;
- no external or manually launched live CGL, real TrueForge, private qualification/publication, or full-E2E workload while the four-wide wave is active;
- enough human/Qodo review capacity to merge the current wave before activating the next one.

If RAM is unverified or at most 8 GB, effective concurrency is capped at two. On a verified host above 8 GB, begin with at most three; activate the fourth leaf only after the first three have completed clone/environment startup and every checkpoint remains green. Warning or critical memory pressure, rising swapouts, insufficient disk, app-server exits, retry loops, or install-lock stalls forbid the fourth activation and require removing `symphony` from any queued leaf. Even on a larger host, four remains the hackathon ceiling.

The live-workload restriction does not prohibit synthetic unit, contract, or failure-injection tests inside a leaf. In Wave 2A, AA-06A uses synthetic non-demo qualification fixtures and no leaf launches the real private runtime. In Wave 4A, AA-10C is the sole renderer/runtime lane; AA-10A, AA-10B, AA-12A, and AA-12B must not launch CGL or a real TrueForge/private qualification/publication/full-E2E run. If evidence makes a second lane require one of those live resources, that lane reports a blocker and defers the command to a serial follow-on leaf instead of overlapping it.

### Test-tier isolation during Wave 4

The unqualified command `uv run pytest -q` is a serial release gate, not a Wave 4 leaf command. Until the repository has an independently proved marker/addopts contract that excludes CGL, the private runtime, real TrueForge, publication, and full E2E by default, a four-wide lane must run only its named path:

- AA-10A runs `tests/security` only;
- AA-10B runs `tests/recovery` only;
- AA-10C runs `tests/determinism` and is the sole lane allowed to exercise its owned renderer/runtime failure checks;
- AA-12A runs `tests/release/test_evidence_binding.py` with synthetic fixtures only;
- AA-12B runs `tests/release/test_git_binding.py` with synthetic Git fixtures only;
- AA-12C runs `tests/release/test_evidence_manifest_cli.py` with synthetic integration fixtures only;
- AA-11 runs `tests/release/test_release_artifacts.py` with synthetic/public fixtures only.

Only relation-free `YU-29`, after every Wave 4 and release-preparation PR is merged and no other live lane is active, runs the full default suite against the recorded final-code SHA. A focused green result is evidence for its leaf, not a claim that the repository-wide regression gate passed.

## 6. Exact blocker graph

The following 33 `blocks` relations are the complete baseline executable AA graph. The source is the blocker; the target is the blocked issue. Human control issues and non-dispatch umbrellas are intentionally relation-free.

- `AA-B00` blocks `AA-00A`
- `AA-00A` blocks `AA-00B`
- `AA-00B` blocks `AA-02`
- `AA-00B` blocks `AA-03`
- `AA-00B` blocks `AA-04`
- `AA-02` blocks `AA-01`
- `AA-01` blocks `AA-05A`
- `AA-01` blocks `AA-05B`
- `AA-03` blocks `AA-05B`
- `AA-04` blocks `AA-05B`
- `AA-05A` blocks `AA-06A`
- `AA-03` blocks `AA-06A`
- `AA-04` blocks `AA-06A`
- `AA-01` blocks `AA-07`
- `AA-03` blocks `AA-07`
- `AA-04` blocks `AA-07`
- `AA-01` blocks `AA-08`
- `AA-03` blocks `AA-08`
- `AA-04` blocks `AA-08`
- `AA-06A` blocks `AA-06B`
- `AA-05B` blocks `AA-09A`
- `AA-06B` blocks `AA-09A`
- `AA-07` blocks `AA-09A`
- `AA-08` blocks `AA-09A`
- `AA-09A` blocks `AA-09B`
- `AA-09B` blocks `AA-10A`
- `AA-09B` blocks `AA-10B`
- `AA-09B` blocks `AA-10C`
- `AA-09B` blocks `AA-12A`
- `AA-09B` blocks `AA-12B`
- `AA-12A` blocks `AA-12C`
- `AA-12B` blocks `AA-12C`
- `AA-12C` blocks `AA-11`

The graph is acyclic. AA-07 and AA-08 deliberately have three direct blockers each; their earlier prose-only wave gate is now machine-visible.

## 7. Ownership and integration rules

- One issue owns one branch and one PR, always based on current `main`. Stacked PRs are not part of this workflow.
- Dependency ownership is a serial Phase 0 handoff: AA-00A creates the initial `pyproject.toml` and `uv.lock`; after AA-00A merges, AA-00B exclusively owns those two files while closing the real TrueForge seam. AA-00B must preserve AA-00A's direct upstream pins and rerun the upstream 4/4 suite under the final lockfile. The dependency files freeze only after that regression and AA-00B's 5/5 gate pass and merge. Every later dependency change is a blocker, not an opportunistic edit.
- `tests/conftest.py` belongs only to AA-00A. AA-00B keeps its helpers inside `tests/phase0/trueforge/**`.
- AA-00A and AA-00B have disjoint spike, test, and evidence directories.
- AA-05A alone owns exact-byte commitment hashing and its single keyword-only full-five API. AA-06A, AA-09A, and AA-12A import that module and its conformance vector; none may copy hashing, field assembly, decoding, normalization, or reserialization.
- AA-05 is a relation-free umbrella. AA-05B owns causal service behavior only: it reads the stored pre-provisioned case identity, never imports the commitment module, never computes or updates a commitment, and never receives hidden-manifest or nonce bytes.
- AA-09A owns direct-stack provisioning tests; AA-09B owns real TrueForge denial and approval runs.
- AA-09B owns the versioned, sanitized E2E evidence-index contract and emits the selected run/export identities that AA-12A consumes. Raw private paths and caller-supplied digest fields are never part of that handoff.
- AA-12A owns only evidence-source resolution, exact byte/equality checks, and the typed `VerifiedEvidenceBinding`. It does not own Git-tree or manifest/CLI logic.
- AA-12B owns only named-Git tree canonicalization, presentation normalization, prepared/final-link state verification, and the typed `VerifiedGitBinding`. It never reads the private runtime or evidence graph.
- AA-12C imports the two verified bindings and alone owns the evidence-manifest schema, canonical assembly, atomic writer, exact CLIs, stdout/exit contract, and integration fixtures. It must not reimplement AA-12A or AA-12B algorithms.
- AA-11 consumes the AA-12C interface and owns only release prose, notices, runbook, and demo-report presentation.
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
- [ ] One keyword-only `create_preprovisioned_case` transaction atomically inserts the case and root revision with `root_revision_id == head_revision_id`, null qualification/promotion state, and exactly the five named lowercase-64-hex fields `source_asset_sha256`, `controller_sha256`, `public_contract_sha256`, `runner_sha256`, and `holdout_commitment_sha256`
- [ ] Case creation rejects a missing, extra, malformed, or existing identity; it never upserts, hashes artifact bytes, renames a commitment field, or exposes an API that later updates any of the five stored commitments
- [ ] Every non-root child revision persists exactly one hypothesis event and one probe run citation; the pre-provisioned root revision has both citation fields null
- [ ] Event-chain mutation is detected
- [ ] Linear head, qualification attempt, and promotion state can be restored
- [ ] RUNNING and RECOVERING qualification states persist exact attempt identity
- [ ] No generic artifact registry, garbage collector, or distributed idempotency layer is added
- [ ] The public transaction API supports pre-provisioned-case creation, revision-plus-event atomic commit, exact qualification reserve/recover/terminal identity, and promotion receipt/reconciliation lookup without downstream storage edits

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

### AA-05A — Freeze the five exact case commitments (`YU-35`)

Wave: Wave 1C — exact commitment primitive
Blocked by: AA-01
Activation gate: AA-01 is merged into `main`; AA-02 is already transitively satisfied.

Primary uncertainty: Can every consumer derive one identical five-field case identity from exact artifact bytes without paths, caller-supplied digests, reserialization, or private-data leakage?

Owned surfaces:

- src/asset_autopsy/commitments.py
- tests/unit/test_commitments.py
- tests/unit/commitment_fixtures/**

Protected surfaces:

- src/asset_autopsy/service.py
- src/asset_autopsy/qualification.py
- src/asset_autopsy/publisher.py
- src/asset_autopsy/evidence_binding.py
- src/asset_autopsy/storage.py and src/asset_autopsy/schemas.py
- fixtures/compound-arm-01/**
- pyproject.toml and uv.lock

Exact API:

- The sole public callable is `compute_case_commitments_from_bytes(*, source_asset_bytes: bytes, controller_bytes: bytes, public_contract_bytes: bytes, runner_bytes: bytes, canonical_hidden_manifest_bytes: bytes, nonce_bytes: bytes) -> CaseCommitments`.
- `CaseCommitments` is frozen and contains exactly `source_asset_sha256`, `controller_sha256`, `public_contract_sha256`, `runner_sha256`, and `holdout_commitment_sha256`.
- The six same-typed byte inputs are keyword-only. There is no public partial helper or caller-side field assembly; any internal exact-byte SHA-256 helper remains private.

Acceptance:

- [ ] The four public digests are SHA-256 of their exact nonempty artifact bytes; the helper never decodes, parses, normalizes, or reserializes them
- [ ] Holdout digest is exactly SHA-256 of `canonical_hidden_manifest_bytes || nonce_bytes`; nonce is exactly 32 raw CSPRNG bytes and hidden-manifest bytes are nonempty exact canonical bytes supplied by their producer
- [ ] Inputs are bytes only: no path, file object, runtime object, or caller-supplied digest
- [ ] Success returns only frozen typed lowercase 64-hex digests; failure is a bounded typed reason containing no input byte, path, hidden value, or nonce
- [ ] Same named bytes return the same values across fresh processes, all six inputs are keyword-only, and a missing, extra, non-bytes, or empty input fails closed
- [ ] Each public artifact's one-byte tamper changes only its own digest; one-byte hidden-manifest or nonce tamper changes the holdout digest; empty material and nonce lengths other than 32 fail
- [ ] One synthetic conformance vector is exported for AA-06A, AA-09A, and AA-12A contract tests without importing service, qualification, runtime, or evidence modules
- [ ] Protected surfaces remain unchanged and focused tests use no filesystem, DB, MuJoCo, network, or private runtime

Required verification:

- uv sync --frozen
- uv run pytest tests/unit/test_commitments.py -q
- git diff --check

Out of scope:

- Producing controller, contract, runner, or hidden-manifest canonical bytes
- Case persistence or causal-service behavior
- Qualification, qualified-core digest, runtime provisioning, or evidence verification
- Generic signing, attestation, or key management

### AA-05B — Implement the public causal loop service (`YU-12`)

Wave: Wave 2A — parallel domain layer
Blocked by: AA-01, AA-03, AA-04
Activation gate: all of AA-01, AA-02, AA-03, and AA-04 are merged into `main`.

Primary uncertainty: Can causal ordering and budget accounting remain exact across domain success, failure, and partial-engine outcomes?

Owned surfaces:

- src/asset_autopsy/service.py
- tests/facade/test_causal_loop.py

Protected surfaces:

- verify_revision and publish_revision
- MCP transport and TrueForge AgentSpec
- schemas.py is consumed, not edited
- commitments.py and its conformance fixtures are neither imported nor edited
- pyproject.toml and uv.lock

Acceptance:

- [ ] open_case, inspect_asset, run_task, run_probe, and create_revision are implemented
- [ ] open_case performs a fresh read of an already pre-provisioned case; a missing case returns typed `CASE_NOT_FOUND`
- [ ] open_case does not create or mutate a case row, commitment, revision, run, budget, ledger event, or object-store artifact; repeated calls leave that complete logical storage state unchanged
- [ ] For its five commitment fields, open_case copies only the stored values and never imports commitments.py, computes or renames a digest, or reads hidden-manifest or nonce bytes; the response still contains every contract, budget, revision, topology, patch-policy, and sanitized event-tail field required by the frozen design
- [ ] A pre-provisioned case missing any required commitment fails with a bounded typed precondition error rather than inventing or repairing identity
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
- Commitment hashing and byte identity owned by AA-05A
- MCP/HTTP wiring
- TrueForge agent configuration

### AA-06A — Implement one-shot qualification and exact recovery (`YU-22`)

Wave: Wave 2A — parallel qualification domain
Blocked by: AA-03, AA-04, AA-05A
Activation gate: all of AA-03, AA-04, and AA-05A are merged into `main`; AA-01 and AA-02 are already transitively satisfied.

Primary uncertainty: Can one-shot hidden qualification recover infrastructure interruption without creating a second logical attempt or leaking partial output?

Owned surfaces:

- src/asset_autopsy/qualification.py
- tests/facade/test_qualification.py

Protected surfaces:

- Publication and export files owned by AA-06B
- Public causal service and MCP approval UI
- commitments.py and its conformance fixtures are consumed, not edited
- Hidden details never enter public logs or exceptions
- pyproject.toml and uv.lock

Acceptance:

- [ ] Only a public-pass linear head can begin qualification
- [ ] A focused cross-contract test first proves AA-05A `compute_case_commitments_from_bytes` against its synthetic conformance vector; immediately before reserving a real attempt, the same single API recomputes the exact five fields, those values equal the stored case, and the reserved qualification identity plus later qualification events copy them exactly without hashing, field assembly, decoding, normalization, or reserialization
- [ ] Attempt ID, revision, suite commitment, and scenario hashes persist before hidden execution
- [ ] Public output contains only aggregate status and failed clause IDs; partial and private child output is never published
- [ ] Infrastructure interruption replays only the exact RECOVERING attempt
- [ ] A completed terminal failure prevents another qualification for the case
- [ ] A passing attempt prepares a qualified core containing exactly `repaired.xml`, `patch-manifest.json`, and `qualification.json`; no ledger, manifest, promotion receipt, or event bytes enter `qualified_core_sha256`
- [ ] AA-06A exposes one pure digest helper consumed, not reimplemented, by AA-06B: hash each exact file's bytes, sort entries by UTF-8 filename, encode `{"files":[{"path":...,"sha256":...,"size":...}]}` with UTF-8 `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, then SHA-256 those canonical index bytes
- [ ] `qualification.json` contains neither `qualified_core_sha256` nor ticket, ledger, manifest, or PROMOTED data, so the three-file digest cannot refer to itself
- [ ] `QUALIFICATION_PASSED` stores that already-computed `qualified_core_sha256`, and the complete promotion ticket binds the same digest for AA-06B
- [ ] Cross-contract tests run AA-06A output through the helper AA-06B will consume and fail on member, byte, filename, framing, or order drift, including any dependency on the ledger event that records the digest

Required verification:

- uv sync --frozen
- uv run pytest tests/facade/test_qualification.py -q
- git diff --check

Out of scope:

- Atomic export publication
- Commitment hashing and byte identity owned by AA-05A
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

- AA-05B service.py and AA-06 qualification/publication files
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
- [ ] Service and verifier are injected through fakes; AA-05B and AA-06 files are not edited
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
- [ ] Export uses temp write, file and temporary-directory fsync, content verification, atomic directory rename, and destination-parent-directory fsync in that order
- [ ] Only after verifying the three-file core, publication generates `evidence-ledger.jsonl`; it ends at `QUALIFICATION_PASSED` and is excluded from `qualified_core_sha256`
- [ ] `manifest.json` contains `qualified_core_sha256` plus exact byte hashes for only the three core files and `evidence-ledger.jsonl`; it explicitly excludes `manifest.json` itself and uses the same canonical JSON encoding rule
- [ ] `manifest_sha256` is SHA-256 of the exact canonical `manifest.json` bytes; no `PROMOTED` event exists before rename and destination-parent fsync both succeed, and only then is that digest committed in `PROMOTED`
- [ ] Tests fail if a ledger, manifest, promotion receipt, or event is added to the qualified-core member set, or if any of the three core files changes after ticket creation
- [ ] Failure injection proves: crash after rename but before parent fsync cannot leave `PROMOTED`; a surviving valid bundle is parent-fsynced then reconciled, a missing bundle remains unpromoted and retryable; crash after parent fsync but before event reconciles to one `PROMOTED`; replay after the event never rewrites the bundle
- [ ] A durable valid final bundle reconciles to exactly one `PROMOTED` event with its manifest hash; if no final bundle survived, reconciliation creates no `PROMOTED` event and leaves publication retryable

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
Blocked by: AA-05B, AA-06B, AA-07, AA-08
Activation gate: all of AA-05B, AA-06B, AA-07, AA-08 are merged into `main`.

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
- commitments.py and its conformance fixtures are consumed, not edited
- Core modules owned by earlier issues except an explicitly reported blocker

Acceptance:

- [ ] A fresh private demo runtime is provisioned outside both checkout and agent sandbox, and all runtime commands require the same absolute `ASSET_AUTOPSY_RUNTIME_ROOT`
- [ ] The canonical root layout is exactly `.asset-autopsy/{ledger.sqlite,objects/sha256/**,exports/**,builds/**}`, `private/**`, and `evidence/{runs/**,reports/**}`; provision, serve, reset, ledger verification, and sanitized report output share one resolver that rejects unset/relative roots, symlink roots or escapes, traversal, and any root inside the Git checkout
- [ ] Provisioning materializes the exact public artifacts, canonical hidden-manifest bytes, and 32-byte nonce, then obtains all five runtime commitments only from AA-05A `compute_case_commitments_from_bytes`; scripts contain no copied hashing, field assembly, decoding, normalization, or reserialization
- [ ] Provisioning passes the exact five named fields from that frozen `CaseCommitments` result, without renaming or recomputation, to AA-03 `create_preprovisioned_case`; that single transaction creates the case/root revision once, a read-back equals the typed input, and no service-start or later runtime path mutates a commitment
- [ ] serve, reset-demo, and verify-ledger commands are reproducible from a clean checkout and emit no absolute runtime path or private value
- [ ] Exact recordable entry is `scripts/serve --recordable --git-sha <full-hex>`; it materializes the named Git tree into an isolated runtime-root build snapshot, launches only that snapshot plus its frozen environment, clears `PYTHONPATH`/`PYTHONHOME`, disables user-site and plugin autoload, and rejects every loaded executable/config origin outside the snapshot or frozen environment
- [ ] After service initialization and before any recordable run, canonical private `runtime-build.json` records the execution SHA, snapshot tree, frozen lockfile, upstream commit/tool schema, TrueForge AgentSpec, Asset Autopsy config, interpreter, and loaded-module/config origin inventory; `component_build_sha256` is SHA-256 of those canonical bytes and is stored outside the record
- [ ] Contract tests prove every script resolves the same root/layout, rejects path/symlink escape, preserves the same runtime-build identity for an unchanged named commit, and rejects a dirty checkout, shadow untracked/ignored module, `sitecustomize`, plugin, config override, or executable search-path injection
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
- `ASSET_AUTOPSY_RUNTIME_ROOT` is provisioned through AA-09A's canonical resolver and points outside the checkout and agent sandbox

Primary uncertainty: Can the real agent close the full depth-two loop and real approval boundary from one prompt with zero re-prompts?

Owned surfaces:

- tests/e2e/trueforge/**
- schemas/e2e-evidence-index-v1.json
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
- [ ] A recordable run starts only through AA-09A's isolated named-Git-snapshot entry, captures `execution_code_git_sha` before launch and the initialized `component_build_sha256` before the first agent call, and refuses dirty, shadowed, injected, or changed runtime code as valid evidence
- [ ] Run A uses real denial and leaves zero exports
- [ ] After reset, fresh Run B completes r000 to r001 to r002 from one prompt
- [ ] Exactly two hypothesis/probe/one-attribute-patch cycles occur with zero human re-prompts
- [ ] Public gates and hidden qualification pass 3/3
- [ ] A real TrueForge approval pause occurs only for publish_revision
- [ ] The approval surface displays the complete human-readable ticket before the human acts
- [ ] Fresh Run B uses real approval and publishes only its exact qualified revision
- [ ] Evidence-chain and manifest hashes verify
- [ ] The run writes a canonical `asset-autopsy-e2e-index/v1` source-map record at `$ASSET_AUTOPSY_RUNTIME_ROOT/evidence/runs/<run-id>/e2e-index.json`; it contains only execution-code Git SHA, component-build SHA-256, case/run/revision/qualification identities, export name, public commitment digests, canonical relative artifact references, publication digests, and the sanitized session-trace digest
- [ ] The source-map record uses only canonical relative references beneath `ASSET_AUTOPSY_RUNTIME_ROOT`, rejects symlink escape and identifiers outside `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`, and never serializes an absolute path, hidden value, nonce, secret, raw trace, or caller-supplied digest
- [ ] The session trace contains machine-readable execution-code Git SHA, component-build SHA-256, case/run/qualification/revision identity, approved export name, and publish-result manifest digest so a later verifier can reject stale code or evidence mixed across a case, run, revision, qualification, or publication
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

Wave: Wave 4A — parallel security gate
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
- git diff --check

Out of scope:

- Qualification/publication crash recovery
- Determinism and rendering degradation
- Broad security refactoring

### AA-10B — Prove qualification and publication crash recovery (`YU-27`)

Wave: Wave 4A — parallel recovery gate
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
- git diff --check

Out of scope:

- Public-data leakage
- Numeric determinism and rendering degradation
- Generic job or distributed transaction infrastructure

### AA-10C — Prove determinism and numeric degradation recovery (`YU-28`)

Wave: Wave 4A — parallel determinism gate
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
- git diff --check

Out of scope:

- Fixture retuning
- Security and publication recovery
- General performance or simulator-pool redesign

### AA-12A — Verify evidence provenance and exact equality (`YU-31`)

Wave: Wave 4A — parallel evidence-provenance harness
Blocked by: AA-09B
Activation gate: all of AA-09B are merged into `main`; Wave 4A has five ready leaves but at most four active.

Primary uncertainty: Can the trusted source map alone close one evidence identity graph without trusting a caller-supplied path or digest and without leaking private data?

Owned surfaces:

- src/asset_autopsy/evidence_binding.py
- tests/release/test_evidence_binding.py
- tests/release/evidence_binding_fixtures/**

Protected surfaces:

- Git-tree and presentation logic owned by AA-12B
- AA-05A commitments module and conformance fixtures are consumed, not edited
- Final manifest schema, module, scripts, and CLI owned by AA-12C
- Production core/runtime, AA-09B schema and evidence, pyproject.toml, and uv.lock

Exact API and output contract:

- `verify_evidence_binding(runtime_root, expected_code_git_sha, run_id, export_name) -> VerifiedEvidenceBinding`
- `runtime_root` is an already canonicalized AA-09A root passed internally; no public path or digest input is created.
- IDs match `[A-Za-z0-9][A-Za-z0-9._-]{0,63}`. Resolution starts only at `evidence/runs/<run-id>/e2e-index.json` and follows canonical relative references beneath the root; traversal, symlink escape, a missing identity, or run/export disagreement fails closed.
- `VerifiedEvidenceBinding` has exactly three groups: `commitments`, `publication`, and `e2e`.
- `commitments` is exactly `source_asset_sha256`, `controller_sha256`, `public_contract_sha256`, `runner_sha256`, and `holdout_commitment_sha256`. `publication` is exactly case ID, export name, revision ID, qualification-attempt ID, qualified-core, export-manifest, and evidence-ledger SHA-256. `e2e` is exactly run ID, execution-code Git SHA, component-build SHA-256, and session-trace SHA-256.

Acceptance:

- [ ] Execution SHA equals the expected code SHA and the source-map, isolated snapshot, runtime-build, and trace values.
- [ ] Component-build digest equals the exact canonical `runtime-build.json` bytes and the source-map/trace values.
- [ ] Case ID equals the source-map, qualification, `QUALIFICATION_PASSED`, publish result, `PROMOTED`, export, and trace case.
- [ ] SHA-256 of exact sanitized session-trace bytes equals the source-map and typed-output trace digest.
- [ ] AA-06A's pure helper recomputes the exact three-file qualified-core digest, which equals the export `manifest.json` field `qualified_core_sha256`, `QUALIFICATION_PASSED`, and promotion-ticket values; it is never read from self-excluding `qualification.json`.
- [ ] SHA-256 and byte size of exact `qualification.json` bytes equal its export-manifest member entry, while its revision, qualification-attempt, and commitment identities equal the rest of the evidence graph.
- [ ] SHA-256 of exact `evidence-ledger.jsonl` bytes equals the export-manifest and typed-output ledger values.
- [ ] SHA-256 of exact canonical export `manifest.json` bytes equals the approved `publish_revision` result, `PROMOTED` receipt, source-map, trace, and typed-output export digest.
- [ ] Revision, qualification attempt, each commitment, E2E run ID, approved revision, and export agree across every named node.
- [ ] AA-05A `compute_case_commitments_from_bytes` is called exactly once over the resolved exact public artifacts, canonical hidden-manifest bytes, and 32-byte nonce; its exact five-field result equals source-map, qualification/event, export-evidence, and typed-output values. Evidence binding contains no copied hashing, field assembly, decoding, normalization, or reserialization.
- [ ] The private source, hidden manifest, and nonce are read only inside the trusted runtime; nonce, hidden values, absolute paths, secrets, and raw trace never enter output, errors, fixtures, Git, or Linear.
- [ ] Mixed execution/build/case/run/revision/attempt/export/publication fixtures fail even when each file is internally coherent; dirty/stale/shadow-module fixtures and one-byte tampering of trace, every core file, ledger, export manifest, source, controller, contract, runner, hidden manifest, or nonce also fail while unrelated metadata remains coherent.
- [ ] Success returns the exact deterministic typed output; failure returns a typed sanitized reason without traceback or host path.
- [ ] AA-12B and AA-12C protected surfaces remain unchanged, and this leaf launches no CGL, TrueForge, real private qualification/publication, or full E2E workload.

Required verification:

- uv sync --frozen
- uv run pytest tests/release/test_evidence_binding.py -q
- git diff --check

Out of scope:

- Git tree, presentation normalization, and prepared/final-link state
- Final manifest schema, canonical assembly, file write, CLI, and process exit codes
- Live final evidence or production fixes

### AA-12B — Verify Git tree and marker-only final-link transition (`YU-32`)

Wave: Wave 4A — parallel Git/final-link harness
Blocked by: AA-09B
Activation gate: all of AA-09B are merged into `main`; Wave 4A has five ready leaves but at most four active.

Primary uncertainty: Can named Git commits reproduce the production tree and reject every final-link change except the four marker-bounded HTTPS values?

Owned surfaces:

- src/asset_autopsy/git_binding.py
- tests/release/test_git_binding.py
- tests/release/git_binding_fixtures/**

Protected surfaces:

- Private source-map and evidence equality owned by AA-12A
- Final manifest schema, module, scripts, and CLI owned by AA-12C
- README, runbook, final evidence manifest, production core, pyproject.toml, and uv.lock

Exact API and output contract:

- `verify_git_binding(repo_root, final_code_git_sha, phase, head_sha=None) -> VerifiedGitBinding`
- The repository root is resolved internally with Git; no public path input is introduced.
- Output is exactly final-code SHA, production-tree SHA-256, presentation-template SHA-256, phase, optional verified head SHA, and—only in final-link phase—the exact opaque manifest Git-blob bytes plus blob OID.
- Inventory every tracked entry at final-code SHA except exactly `README.md`, `docs/demo/runbook.md`, and `docs/demo/final-evidence-manifest.json`; reject a manifest already present at that commit and every unsupported Git entry type.
- Sort by UTF-8 path and hash canonical JSON `{"files":[...]}` encoded with `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, whose entries contain path, Git mode, exact byte size, and exact-byte SHA-256.

Acceptance:

- [ ] `presentation_template_sha256` binds exact README/runbook bytes after normalizing only each file's unique uncut/video marker value to one fixed sentinel.
- [ ] Prepared values are exactly `ASSET_AUTOPSY_UNCUT_URL_PENDING` and `ASSET_AUTOPSY_VIDEO_URL_PENDING`; final values are one trimmed HTTPS line with a nonempty ASCII host, no userinfo, and no whitespace/control bytes.
- [ ] Prepared phase requires current `HEAD == final_code_git_sha`, no index/tracked-worktree change, exact placeholders, and only the fixed manifest as a permitted untracked addition.
- [ ] Final-link phase requires clean current `HEAD == head_sha`, final-code SHA as ancestor/base, named Git blobs rather than mutable worktree bytes, and an exact three-path diff containing README, runbook, and one regular opaque blob at `docs/demo/final-evidence-manifest.json`; AA-12B validates only that blob's path, mode, OID, and returned exact bytes, never its schema or canonicality.
- [ ] Exactly four placeholders become valid URLs; every other README/runbook byte and mode is equal after marker normalization.
- [ ] Canonical tree digest is deterministic across enumeration order, and prepared/final-link outputs are phase-distinct and binary.
- [ ] Missing, duplicate, or nested markers; partial replacement; Markdown/HTML/multiline/non-HTTPS content; extra paths; dirty state; non-ancestor head; byte/mode/OID tamper; self-reference; and unsupported Git entries fail.
- [ ] This leaf never reads the private runtime/source map, never edits AA-12A/AA-12C surfaces, and uses only synthetic Git fixtures.

Required verification:

- uv sync --frozen
- uv run pytest tests/release/test_git_binding.py -q
- git diff --check

Out of scope:

- Evidence provenance, private commitments, or publication equality
- Final manifest schema/assembly, canonical manifest bytes, atomic write, CLI, stdout, or exit codes

### AA-12C — Assemble the canonical evidence manifest and CLI (`YU-33`)

Wave: Wave 4B — serial manifest/CLI integration
Blocked by: AA-12A, AA-12B
Activation gate: all of AA-12A and AA-12B are merged into `main`.

Primary uncertainty: Can two opaque verified bindings be integrated into one non-self-referential canonical manifest and exact CLI without reimplementing either verifier?

Owned surfaces:

- schemas/evidence-manifest-v1.json
- src/asset_autopsy/evidence_manifest.py
- scripts/build-evidence-manifest
- scripts/verify-evidence-manifest
- tests/release/test_evidence_manifest_cli.py
- tests/release/evidence_manifest_integration_fixtures/**

Protected surfaces:

- AA-12A evidence-binding module, tests, and fixtures
- AA-12B Git-binding module, tests, and fixtures
- README, runbook, final real manifest, production core/runtime, pyproject.toml, and uv.lock

Integration contract:

- Import AA-12A `VerifiedEvidenceBinding` and AA-12B `VerifiedGitBinding`; never duplicate their source resolution, equality, Git-object read, tree, marker, or phase logic.
- The top level is exactly `schema_version`, `final_code_git_sha`, `production_tree_sha256`, `presentation_template_sha256`, `commitments`, `publication`, and `e2e`; `schema_version` is `asset-autopsy-evidence/v1`.
- The `commitments`, `publication`, and `e2e` groups are copied from AA-12A without omission, renaming, or recomputation; `e2e` remains exactly run ID, execution-code Git SHA, component-build SHA-256, and session-trace SHA-256. Git/tree/presentation fields are copied from AA-12B, and `e2e.execution_code_git_sha` must equal AA-12B final-code SHA before assembly.
- The manifest is UTF-8 canonical JSON via `json.dumps(..., sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, and its SHA-256 never appears inside it.
- Builder is exactly `scripts/build-evidence-manifest --git-sha <full-hex> --run-id <safe-id> --export-name <safe-id>`.
- Prepared verifier is exactly `scripts/verify-evidence-manifest --phase prepared --git-sha <full-hex> --run-id <safe-id> --export-name <safe-id>`; final-link verifier adds `--phase final-link --head-sha <full-hex>`.
- All invocations resolve the checkout with Git and the private runtime through AA-09A's canonical resolver, accept no path/digest argument, and use only `docs/demo/final-evidence-manifest.json`.
- Builder requires AA-12B prepared state before and after writing, writes canonical bytes by temp file → fsync → atomic rename, and leaves only the fixed manifest addition. In final-link phase, C validates only the exact manifest bytes/OID returned by AA-12B from the named Git object; C never performs a second Git or mutable-worktree read.
- Every success prints only `manifest_sha256=<64 lowercase hex>`. Exit codes are 0 success, 2 usage/schema/noncanonical, 3 missing/untrusted source/path, 4 digest/identity/equality/phase mismatch, and 5 private-data sentinel; failure prints one sanitized reason code without a stack trace or host path.

Acceptance:

- [ ] Schema rejects every missing, extra, or noncanonical field.
- [ ] Builder and both verifier phases produce the same canonical bytes and manifest SHA for the same imported verified inputs; component-build SHA-256 is present in `e2e` in every phase.
- [ ] Execution-SHA disagreement, forged or mixed A/B binding, stale evidence, self-reference, noncanonical manifest, partial final link, or private sentinel fails through the imported verifiers.
- [ ] Integration tests prove AA-12A and AA-12B functions are invoked, the final manifest is parsed only from AA-12B's immutable returned blob, and neither verifier nor Git-object read is copied.
- [ ] Atomic-write crash fixtures never expose a valid partial manifest.
- [ ] Protected surfaces remain unchanged, and this leaf uses synthetic integration fixtures without CGL, TrueForge, real private runtime, qualification, publication, or full E2E.

Required verification:

- uv sync --frozen
- uv run pytest tests/release/test_evidence_manifest_cli.py -q
- git diff --check

Out of scope:

- Evidence source-map or byte-level equality implementation
- Git tree, presentation, URL, or phase-state implementation
- Release prose, recording, upload, approval, final-link PR, or submission

### AA-11 — Produce release documentation, notices, and demo runbook (`YU-18`)

Wave: Wave 4C — release preparation after manifest/CLI integration; human evidence remains later
Blocked by: AA-12C
Activation gate: all of AA-12C are merged into `main`; AA-12A, AA-12B, and AA-09B are already transitively satisfied.

Human gate before dispatch:

- A human-selected repository SPDX license is recorded before dispatch

Primary uncertainty: Can final lockfile licenses and verified public-only evidence produce accurate release artifacts without making unproved claims?

Owned surfaces:

- README.md
- LICENSE
- THIRD_PARTY_NOTICES.md
- docs/demo/runbook.md
- docs/demo/report-template.md
- docs/release-checklist.md
- scripts/generate-notices
- scripts/build-demo-report
- tests/release/test_release_artifacts.py

Protected surfaces:

- Production core and tests outside narrow documentation plumbing
- Private manifest, hidden values, raw traces, secrets, and host paths
- AA-12A evidence-binding module/tests/fixtures, AA-12B Git-binding module/tests/fixtures, and AA-12C manifest schema/module/scripts/tests
- docs/demo/evidence/** and docs/demo/final-evidence-manifest.json
- Any other docs/demo path not named in Owned surfaces
- Final human-recorded evidence and submission action

Acceptance:

- [ ] A human has fixed the repository license choice before dispatch; the agent does not infer MIT from a recommendation
- [ ] Dependency inventory is generated from the final lockfile
- [ ] Direct MIT and Apache obligations are preserved
- [ ] Nova code and assets are absent
- [ ] README calls the result a contract-bounded case study, not a general 3D doctor
- [ ] Report generation accepts only an AA-12C manifest that passes `scripts/verify-evidence-manifest`, rejects raw caller-supplied digests, and excludes private data
- [ ] Three-minute edited runbook preserves real event order and normal-speed approval
- [ ] `README.md` and `docs/demo/runbook.md` each contain exactly the two marker pairs `<!-- asset-autopsy-link:uncut:start -->` / `<!-- asset-autopsy-link:uncut:end -->` and `<!-- asset-autopsy-link:video:start -->` / `<!-- asset-autopsy-link:video:end -->`; their initial values are explicit placeholders and no other file contains a final-link placeholder
- [ ] The release checklist names YU-29 as the sole final-evidence owner and lists merged AA-10A, AA-10B, AA-10C, AA-12A, AA-12B, AA-12C, and AA-11 PRs plus all six verified umbrella rollups as its preconditions
- [ ] Demo-report generation consumes only AA-12C's verified manifest interface, which binds AA-12A evidence and AA-12B Git results; it never reimplements source-map/equality/tree/presentation logic, and any invalidation requires a fresh post-hardening E2E manifest
- [ ] Synthetic report tests write only to a temporary directory; the real final report is never a tracked release artifact and YU-29 writes it only to `$ASSET_AUTOPSY_RUNTIME_ROOT/evidence/reports/final-report.html`

Required verification:

- uv sync --frozen
- uv run pytest tests/release/test_release_artifacts.py -q
- Run the documented notice generator and review generated direct/transitive licenses
- Run scripts/verify-evidence-manifest against the AA-12C synthetic/public integration fixture
- Run the documented demo-report generator against verified synthetic/public evidence
- git diff --check

Out of scope:

- Final screen recording, upload, and submission
- Core production fixes
- Claims beyond the verified compound-arm-01 case study
- Evidence/source equality owned by AA-12A, Git/presentation verification owned by AA-12B, or manifest schema/generator/verifier owned by AA-12C

## 9. Human gates

The following choices and effects remain human-owned:

- choose the repository SPDX license before AA-11 dispatch; MIT is a recommendation in the product design, not a selected license;
- review and merge every PR;
- grant or deny the real TrueForge publication approval in AA-09B;
- verify all six umbrella rollups;
- use the AA-12C generator and verifier in `YU-29` to bind or re-record post-hardening evidence, replace only the four marker-bounded README/runbook link values through a human-controlled final-link PR, edit and upload the videos, and submit the entry.

## 10. Final human control contract

### YU-29 — Record final evidence, publish links, and submit

## Outcome

After every implementation and release-preparation PR is merged, a human selects or records evidence matching the current commitments, publishes the real demo links, lands the final-link update, and submits the hackathon entry.

## Preconditions

- AA-10A, AA-10B, AA-10C, AA-12A, AA-12B, AA-12C, and AA-11 PRs are merged into main.
- All six non-dispatch umbrella rollups are human-verified.
- The repository SPDX license is fixed.
- No other live CGL, TrueForge, private-runtime, qualification, publication, or full-E2E lane is active.
- The human has selected one safe run ID and export name from the private `asset-autopsy-e2e-index/v1` source map; no file path or digest is hand-entered.
- Before any final-link PR, `scripts/build-evidence-manifest --git-sha <full-hex> --run-id <safe-id> --export-name <safe-id>` has generated `docs/demo/final-evidence-manifest.json`, and the matching verifier with `--phase prepared` has reported the same separate manifest SHA-256.
- Prepared verification proves the selected run executed the exact clean final-code Git SHA and component build; it recomputes the final-code tree, presentation template, all five artifact commitments, publication equality graph, and E2E identity graph. Any mismatch requires a fresh post-hardening run and regenerated manifest.

## Human-owned work

- Grant or deny any real TrueForge publication approval.
- Select only the final-code SHA, safe run ID, and export name; generate and verify the final evidence manifest with the exact AA-12C CLI and never hand-author its schema, paths, tree inventory, or digests.
- Record or verify the final uncut run.
- Edit and upload the three-minute video.
- Replace only the four marker-bounded URL values in `README.md` and `docs/demo/runbook.md`, and add the canonical generated `docs/demo/final-evidence-manifest.json`, in one human-controlled final-link PR.
- Generate the real sanitized demo report only at `$ASSET_AUTOPSY_RUNTIME_ROOT/evidence/reports/final-report.html`; never add it to the final-link PR.
- Verify the edited event order against the uncut evidence and ledger.
- Submit the final entry and record the submission confirmation.

## Dispatch safety

- Keep this issue Backlog until all preconditions pass.
- Never add the symphony label.
- Do not assign this issue to Symphony; browser approval, recording, upload, merge, and submission remain human actions.

## Acceptance

- [ ] Before the final-link PR, the exact AA-12C generator plus `--phase prepared` verifier emit the same manifest SHA for the recorded final-code Git SHA, selected run ID, and export name; execution-code SHA equals final-code SHA and component-build identity matches.
- [ ] Prepared verification closes case/execution/source-map/equality graphs and matches the production tree, normalized presentation template, commitments, publication, and E2E identity; any invalidation has triggered a fresh post-hardening E2E and regenerated manifest.
- [ ] The final-link PR changes exactly `README.md`, `docs/demo/runbook.md`, and `docs/demo/final-evidence-manifest.json`; README/runbook changes are only the four marker-bounded placeholder-to-URL values, and the manifest equals canonical generator output byte-for-byte.
- [ ] Each replacement is one trimmed HTTPS URL with a nonempty ASCII host, no userinfo, and no whitespace/control bytes; partial replacement, arbitrary Markdown/HTML, and non-URL marker content fail.
- [ ] The full regression suite passes serially on the recorded final-code SHA before the final-link PR.
- [ ] Real approval outcome and exact published revision are visible in the uncut evidence.
- [ ] README contains the real uncut-run and three-minute video links with no placeholder.
- [ ] The final-link PR is merged and its diff contains no private data.
- [ ] `--phase final-link` passes on both the clean PR head and clean merged `main`; both runs re-emit the prepared manifest SHA, and the final merge SHA is recorded.
- [ ] Edited video order matches the verified ledger and shows approval at normal speed.
- [ ] Submission confirmation and public URLs are recorded without secrets or private runtime data.

## Verification

- Record `git rev-parse HEAD` for the final-code main commit and select one safe run ID/export name from the fixed private source map.
- Run uv sync --frozen and uv run pytest -q serially on that commit, then require a clean index/worktree before manifest generation.
- With the provisioned `ASSET_AUTOPSY_RUNTIME_ROOT`, run `scripts/build-evidence-manifest --git-sha <full-hex> --run-id <safe-id> --export-name <safe-id>` and record the sole reported manifest SHA-256.
- Run `scripts/verify-evidence-manifest --phase prepared --git-sha <full-hex> --run-id <safe-id> --export-name <safe-id>` while HEAD equals the final-code SHA and the generated manifest is the only worktree addition; require stdout to equal the builder SHA.
- Run zsh scripts/verify-ledger for the selected final evidence.
- After committing the exact three-path final-link change, run `scripts/verify-evidence-manifest --phase final-link --head-sha <pr-head-full-hex> --git-sha <full-hex> --run-id <safe-id> --export-name <safe-id>` from a clean checkout; require stdout to equal the prepared SHA.
- After merge, check out clean `main`, record its full merge SHA, rerun with `--phase final-link --head-sha <merge-full-hex> --git-sha <full-hex> --run-id <safe-id> --export-name <safe-id>`, and require the same SHA and exact three-path diff from final-code SHA.
- Run the demo-report generator to the fixed untracked/private-runtime report path and verify its source/production commitments match the final-code SHA.
- Scan the report, links, PR diff, and videos for private data.
- Verify the final-link PR path set is exactly `README.md`, `docs/demo/runbook.md`, and `docs/demo/final-evidence-manifest.json`; reject every non-marker README/runbook byte change, and require verifier stdout to equal the manifest SHA recorded before the PR.
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
