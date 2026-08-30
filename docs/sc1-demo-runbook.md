# Asset Autopsy SC1 — 3-minute demo runbook

Status: rehearsal script. Do not label the run “passed” unless the sanitized real-model evidence
artifact for the checked-out commit exists and its gate result is PASS. A scripted event fixture,
unit test, or manually assembled screen sequence is not substitute evidence.

The [accepted SC1 contract](decisions/sc1-approval-request-endpoint.md) ends at the real
`tool.approval_required` event for `publish_revision`. The recording never clicks approval or
claims publication materialization. Post-approval materialization and promotion persistence are
not implemented; a direct approved server call fails closed with `PUBLICATION_DEFERRED` and no
storage or filesystem mutation.

## Before recording

- Checkout the exact final SC1 commit and complete the README verification commands.
- Confirm the normal TrueForge 0.1.4 runtime has the saved OpenAI provider,
  `openai/gpt-5-6-sol`, and the existing `hackathon-starter` agent.
- Start TrueForge with `npm run trueforge`, then run
  `uv run python scripts/run_sc1_e2e.py` in a second terminal.
- Use the isolated fresh case created by that run. A repeated request may return a terminal result
  that was already committed before response loss. If the attempt is interrupted before a terminal
  commit, stop and start another evidence run with a fresh case.
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
> metrics, and hidden verifier; only physics runs in a private pinned stdio MuJoCo child.

Point out that `publish_revision` is the only destructive tool and the only tool requiring
approval. There is no product CLI, separate agent package, or TrueForge fork.

### 0:25–0:50 — One prompt and failing baseline

Show the single submitted prompt:

> Autopsy compound-arm-01. Do not change its controller or tests. Qualify and publish the repaired asset.

Show `open_case`, the root `run_task`, and its `result: fail`. Briefly show that `inspect_asset`
contains authored and compiled public values but no fault labels or hidden values.

Say:

> The prompt does not name a defect or a repair path. The fixed public task establishes the
> failure; the model chooses what to inspect, which competing explanations to test, and what to do
> next within the public budgets.

### 0:50–1:25 — Model-chosen experiment and Sandbox analysis

Show one representative `run_experiment` chosen by the model. Highlight its claim, competing
explanation, prediction, falsifier, initial condition, controls, and observables.

For the input boundary, show that:

- `joint_a`, `joint_b`, and `joint_c` are each named exactly once;
- every control segment names `motor_a`, `motor_b`, and `motor_c` exactly once;
- positions and controls stay inside the `open_case` ranges of `-1.2` to `1.2`;
- the model chose 1–16 segments totaling 256–100,000 simulation steps and 1–8 observables.

Then show the evidence chain:

1. `Content too large. Result saved to: …` for the completed experiment response.
2. A successful Sandbox `exec` that references that offloaded response.
3. The later `create_revision` citing the same completed run and hypothesis.
4. The returned canonical diff containing one changed joint attribute.

Say:

> The experiment always returns a self-describing 256-row sampled trace, but that does not
> prescribe the experiment length or the analysis program. The model chooses the experiment and
> Sandbox analysis method, then uses the measured result to justify one immutable revision.

### 1:25–2:05 — Evidence-backed repair strategy and public pass

Follow the actual turn instead of presenting a fixed two-run recipe. If the model runs additional
experiments, show only the evidence that informed a revision. If it creates a second revision,
show the same offload → Sandbox analysis → cited run/hypothesis → one-attribute diff chain again.
The implemented budget allows one or two child revisions and up to five experiments; the model
decides how much of that budget to use.

Finish this section on the final `run_task`:

```text
result: pass
behavior_diff.verdict: public_pass
behavior_diff.changed: true
```

Say:

> Every revision is bound to completed current-base evidence. The final fixed public condition
> passes, and the same-condition parent comparison records a real behavioral improvement.

### 2:05–2:30 — Hidden qualification without leakage

Show one `verify_revision` call after the public pass, followed by only the aggregate result:

```text
public: 1/1
hidden: 3/3
promotion ticket: present
```

Do not open raw qualification storage or display per-scenario values.

Say:

> Hidden verification is a one-shot case-level gate. The agent receives only public and hidden
> aggregates plus a ticket bound to the qualified revision; hidden targets and traces never enter
> the tool result. A committed terminal result is idempotent; interrupted nonterminal work fails
> closed and is not recovered during SC1.

### 2:30–3:00 — Human approval request is the endpoint

Show the single `publish_revision` request and the matching `tool.approval_required` event. Show
that there is no tool response after it. Finish on the sanitized evidence counters:

```text
facade publish invocations: 0
domain publish invocations: 0
```

Also show the sanitized cited run and hypothesis hashes, observed tool sequence, Sandbox execution
count, approval event, and Git commit SHA.

Say:

> TrueForge stops before the destructive server call. No domain publication call ran, and
> post-approval materialization is not implemented. This approval request is the SC1 and hackathon
> endpoint; the submission never approves it and makes no publication-materialization claim.

End the recording without approving, publishing, merging a pull request, or opening a submission
form.

## Evidence checklist

The final frame or linked sanitized evidence must prove, for one fresh real-model turn:

- the first public task failed before any revision;
- the model chose hypotheses, experiment conditions, observables, Sandbox analysis, patches, and
  next actions within the advertised ranges, budgets, and patch policy;
- every revision cited a distinct completed current-base experiment whose response was offloaded
  and successfully analyzed in Sandbox before the revision;
- each cited run and hypothesis reconciled with the ledger, stored trace, and one-attribute
  canonical diff;
- the final public `BehaviorDiff` was changed and `public_pass`;
- qualification was public `1/1` and hidden `3/3`, aggregate only;
- the qualified publish request matched its approval-required event and had no tool response;
- facade and domain publish invocations were both zero;
- the evidence records the exact Git SHA and contains no secret or private-boundary leakage.

## If the live run does not pass

Stop and show the reproducible blocker. Keep the final pull request draft. Record the checked-out
commit, failed gate, sanitized error, and exact rerun command. Do not edit events, reuse a failed or
interrupted qualification case, invoke publication directly, or replace the real provider run with
the scripted E2E event fixture.
