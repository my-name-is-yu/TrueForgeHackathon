# Asset Autopsy SC1 — 3-minute demo runbook

Status: rehearsal script. Do not label the run “passed” unless the sanitized real-model evidence
artifact for the checked-out commit exists and its gate result is PASS. A scripted event fixture,
unit test, or manually assembled screen sequence is not substitute evidence.

## Before recording

- Checkout the exact final SC1 commit and complete the README verification commands.
- Confirm the normal TrueForge 0.1.4 runtime has the saved OpenAI provider,
  `openai/gpt-5-4-mini`, and the existing `hackathon-starter` agent.
- Start TrueForge with `npm run trueforge`, then run
  `uv run python scripts/run_sc1_e2e.py` in a second terminal.
- Use the fresh case created by that run. Never reuse a case after an interrupted hidden
  qualification.
- Prepare three views: the architecture/tool manifest, the TrueForge turn/events, and the
  sanitized evidence result. Enlarge the relevant lines; do not scroll through raw traces.
- Hide API keys, the MCP bearer, account details, private paths, fixture XML, hidden target values,
  and hidden traces.
- Do not click Approve. The final frame must still show the matching approval requirement and zero
  publication activity.

## Timed walkthrough

### 0:00–0:25 — What is running

Show the README architecture and the resolved MCP tool list.

Say:

> TrueForge is the general harness. Asset Autopsy adds seven bounded 3D repair tools over a
> loopback Streamable HTTP MCP. Its Python domain service owns the fixture, evidence, patching,
> metrics, hidden verifier, and publisher; only physics runs in a private pinned stdio MuJoCo
> child. There is no product CLI, Skill, or TrueForge fork.

Point out that `publish_revision` is the only destructive tool and the only tool requiring
approval.

### 0:25–0:50 — One prompt and failing baseline

Show the single submitted prompt:

> Autopsy compound-arm-01. Do not change its controller or tests. Qualify and publish the repaired asset.

Show `open_case`, the root `run_task`, and its `result: fail`. Briefly show that `inspect_asset`
contains authored/compiled values but no fault labels or hidden values.

Say:

> The prompt does not name either defect. The first fixed public run establishes the failure;
> the agent must now choose evidence that separates causes.

### 0:50–1:30 — First causal experiment and revision

Show the first `run_experiment` arguments. Highlight the causal claim, suspected element,
competing explanation, prediction, and falsifier selected by the model.

Then show the causal evidence chain:

1. `Content too large. Result saved to: …` for the experiment response.
2. A successful Sandbox `exec` after the offload and before the revision.
3. `create_revision` citing the completed experiment run and hypothesis and returning one matching
   canonical diff.
4. The child `run_task`, whose parent `BehaviorDiff` is improved but still failing.

Say:

> TrueForge moves the large trace out of the model context. The model chooses how to analyze it in
> Sandbox and uses that measured result to justify one immutable,
> single-attribute revision.

### 1:30–2:10 — Competing cause and public pass

Show the second model-chosen `run_experiment`. Again highlight its competing explanation,
prediction, and falsifier. Show the second Large Tool Response marker and the successful Sandbox
analysis that precedes the cited revision.

Show the second `create_revision` bound to that analyzed run. Confirm that its target attribute is
different from the first revision and its canonical diff has one entry. Then show the final
`run_task`:

```text
result: pass
behavior_diff.verdict: public_pass
behavior_diff.changed: true
```

Say:

> The second experiment supports a different cause, so the agent makes a second one-attribute
> child. The fixed public condition now passes, and the same-condition parent comparison records
> a real behavioral improvement.

### 2:10–2:35 — Hidden qualification without leakage

Show one `verify_revision` call after the public pass, followed by only the aggregate result:

```text
public: 1/1
hidden: 3/3
promotion ticket: present
```

Do not open raw qualification storage or display per-scenario values.

Say:

> Hidden verification runs once and fails closed. The agent receives only public and hidden
> aggregates plus a ticket bound to the exact two-change revision; hidden targets and traces
> never enter the tool result.

### 2:35–3:00 — Human approval is the stop, not the publish

Show the single `publish_revision` request and the matching `tool.approval_required` event. Show
that there is no tool response after it. Finish on the sanitized evidence counters:

```text
publish MCP invocations: 0
publication receipts: 0
published bundles: 0
public artifacts: 0
```

Also show the sanitized cited run and hypothesis hashes, observed tool sequence, Sandbox execution
count, approval event, and Git commit SHA.

Say:

> TrueForge stops before the destructive server call. No publication code ran and no public
> artifact exists. This approval boundary is the submission endpoint; publication remains a
> human handoff.

End the recording without approving, publishing, merging a pull request, or opening a submission
form.

## Evidence checklist

The final frame or linked sanitized evidence must prove, for one fresh real-model turn:

- first public task failed;
- the agent chose competing explanations, experiments, observables, analysis programs, and patches
  within the public budgets and patch policy;
- every revision cited a completed current-base experiment whose trace was offloaded and followed
  by successful Sandbox analysis before the revision;
- the cited run and hypothesis hashes reconciled with the ledger, stored trace, and immutable
  single-attribute revision outcome;
- final public `BehaviorDiff` was changed and `public_pass`;
- qualification was public `1/1` and hidden `3/3`, aggregate only;
- the qualified publish request matched its approval-required event and had no tool response;
- facade/domain publish invocations, receipts, bundles, and public artifacts were all zero;
- the evidence records the exact Git SHA and contains no secret or private-boundary leakage.

## If the live run does not pass

Stop and show the reproducible blocker. Keep the final pull request draft. Record the checked-out
commit, failed gate, sanitized error, and exact rerun command. Do not edit events, reuse a failed
hidden-qualification case, invoke publication directly, or replace the real provider run with the
scripted E2E event fixture.
