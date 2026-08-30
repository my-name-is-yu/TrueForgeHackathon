# Asset Autopsy — SC1 issue plan

Status: thin slice implemented; Issue #41 records the documentation gate and Issue #32 remains the
final quality gate

Product contract: [implemented SC1 design](asset-autopsy-mvp-design.md)

Approval decision: [SC1 ends at the TrueForge approval request](decisions/sc1-approval-request-endpoint.md)

## 1. Source of truth

Work starts from the live GitHub issue, current `main`, current tests, and the current decision
record. A historical issue body, pull-request review, or planning wave does not override merged
source.

Each pull request owns one executable issue. The implementation must stay within the accepted SC1
boundary, run focused verification, and preserve unrelated work. Only a human decides whether to
merge.

The old pre-implementation AA/YU wave graph is no longer an execution dependency. Its planned
modules, recovery lanes, release schemas, and file ownership tables described code that either
changed shape or was deliberately removed. Keeping that graph active would direct work toward
nonexistent surfaces instead of the implemented thin slice.

## 2. Frozen SC1 direction

The product is a TrueForge-facing Streamable HTTP MCP with seven bounded tools:

```text
open_case
inspect_asset
run_task
run_experiment
create_revision
verify_revision
publish_revision
```

The frozen behavior is:

- `run_task` owns the fixed public scenario and parent `BehaviorDiff`;
- `run_experiment` is a generic bounded experiment, not a fixed recipe or `run_probe` alias;
- the model chooses hypotheses, conditions, observables, Sandbox analysis, patch values, and next
  actions;
- every revision changes one allowed joint attribute and cites completed current-base evidence;
- qualification is a one-shot case-level gate; committed terminal results are idempotent, while
  interrupted nonterminal work has no automatic recovery path;
- the hackathon endpoint is the qualified `publish_revision` approval request;
- the submission never clicks approval, receives a publish response, or claims materialization.

The SC1 implementation does not depend on a product CLI, separate agent Skill, TrueForge fork,
arbitrary or future 3D formats, advanced qualification recovery, promotion reconciliation,
publisher module, future evidence-manifest format, or general release framework. Adding any of
those requires a new product decision and a separate issue; none is deferred work required to call
this thin slice complete.

## 3. Implemented baseline on `main`

The current baseline includes the decisions and cleanup recorded by Issue #41:

| Area | Implemented state |
| --- | --- |
| Model strategy | Lean goal and hard constraints; outcome/provenance evaluation accepts different valid strategies. |
| Experiment input | Every hinge and every position actuator exactly once; positions and controls bounded by `open_case` ranges. |
| Experiment shape | 1–16 segments, 256–100,000 total steps, 1–8 observables, deterministic 256-row sampled output. |
| Revisions | One or two child revisions within budget; one attribute per child; no prescribed exact count. |
| Qualification | One attempt per case; a committed terminal result may be returned again, while interrupted nonterminal work requires a fresh case. |
| Approval | `tool.approval_required` is the accepted endpoint; no server response in the submission run. |
| Publication | Materialization and promotion persistence are absent; a direct approved call fails with `PUBLICATION_DEFERRED`. |

The current implementation surfaces are `src/asset_autopsy/**`,
`fixtures/compound-arm-01/asset.mjcf`, `scripts/run_sc1_e2e.py`, and the tests under `tests/`.
Documentation examples must be executable against those surfaces rather than against planned file
names.

## 4. Completed documentation gate: GitHub #41

Issue #41 aligned the implemented contract across:

- `README.md`;
- `docs/sc1-demo-runbook.md`;
- `docs/asset-autopsy-mvp-design.md`;
- `docs/asset-autopsy-issue-plan.md`.

Recorded acceptance:

- [x] The canonical experiment example names `joint_a`, `joint_b`, and `joint_c` exactly once.
- [x] Every illustrated segment names `motor_a`, `motor_b`, and `motor_c` exactly once.
- [x] The docs state the advertised `[-1.2, 1.2]` joint/control ranges and pre-upstream validation.
- [x] The model is described as choosing its strategy within budgets, without a fixed two-run,
      256-step, or Python-analysis recipe.
- [x] Qualification prose distinguishes committed terminal idempotency from nonterminal
      interruption, which is not recovered and requires a fresh case.
- [x] The approval request is consistently the SC1 and hackathon endpoint.
- [x] No command or repository path depends on a removed or unimplemented surface.
- [x] Production code and architecture are unchanged.

Verification for this documentation gate:

```bash
uv run pytest tests/unit/test_schemas.py tests/unit/test_service.py \
  tests/integration/test_service_flow.py tests/e2e/test_sc1_event_evaluator.py -q
git diff --check
```

In addition, inspect every shell command and repository path written in the four documents against
`package.json`, `pyproject.toml`, and `rg --files`. Grep for removed publisher, advanced-recovery,
Skill, future-format, exact-two-run, fixed-256-step, and prescribed-Python dependencies before
review.

## 5. Remaining final quality gate: GitHub #32

Issue #32 remains open. It owns the final, separately reported quality evidence:

- a documented GitHub CI workflow covering install, repository tests, Ruff lint, Ruff format
  check, and diff hygiene;
- final formatting after the implementation lanes are integrated;
- a fresh real-model SC1 case from the exact final `main` candidate;
- evidence bound to that exact commit and command/environment boundary;
- separate test, lint, format, CI, and E2E results;
- a privacy check for hidden targets, traces, host paths, secrets, publication responses, and
  public artifacts.

Do not pre-claim this gate from documentation or local scripted fixtures. The real evidence run
starts the normal TrueForge runtime and developer driver in separate terminals:

```bash
npm run trueforge
```

```bash
uv run python scripts/run_sc1_e2e.py
```

For one fresh case, the sanitized artifact for the exact Git commit must prove:

1. a pre-revision public baseline failed;
2. every revision followed successful Sandbox analysis of its own completed, offloaded,
   current-base experiment;
3. every revision cited the matching run and hypothesis and returned one canonical diff;
4. the final public task passed with a changed `public_pass` `BehaviorDiff`;
5. qualification returned public `1/1` and hidden `3/3` without hidden details;
6. one qualified `publish_revision` request matched one `tool.approval_required` event;
7. no publish response followed and facade/domain publish invocation counts stayed zero;
8. the event and Sandbox boundaries contained no bearer, private path, fixture XML, hidden target,
   or hidden trace.

If CI, formatting, the provider, TrueForge, MuJoCo, or the model blocks #32, record that result in
its own evidence category and keep the final quality claim incomplete. Do not replace the real
turn with a scripted event fixture or reuse an interrupted qualification case.

## 6. Submission handoff

After Issue #32's exact-head quality and real-model gates pass:

1. review the sanitized evidence artifact against the checked-out commit;
2. rehearse the [three-minute runbook](sc1-demo-runbook.md) using the observed strategy rather than
   inventing a cleaner fixed sequence;
3. keep the final frame on the approval request without clicking it;
4. report any omitted verification or unavailable external service as an evidence limit;
5. leave merge and hackathon submission to the human.

No future product format, recovery protocol, export bundle, or additional agent interface blocks
this handoff.
