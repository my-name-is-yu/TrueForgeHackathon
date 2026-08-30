# Symphony automatic review operations

This repository owns the operating policy layered on the pinned upstream OpenAI Symphony
executable. It does not fork or modify Symphony. The policy automates work up to a merge-ready
decision; a human remains the only actor allowed to merge.

## State machine

The Linear team must contain these exact state names before this workflow is rendered:

| State | Owner | Meaning |
| --- | --- | --- |
| `Backlog` | Human | Candidate only; Symphony must not dispatch it. |
| `Todo` | Human | Sole activation gate for new work. |
| `In Progress` | Luna | Initial implementation on one PR branch. |
| `Auto Review` | Luna + Sol | Wait for Codex and Qodo, then adjudicate the current head. |
| `Rework` | Luna | Apply only `fix_now` findings from Sol to the same PR. |
| `Merge Ready` | Human | All automated gates passed; human may decide whether to merge. |
| `Blocked` | Human | Observable execution failure that cannot safely continue automatically. |
| `Done` | Human | Human-owned post-merge terminal state. |

`Auto Review` and `Rework` remain active Symphony states so a process restart can resume from the
Linear Workpad. `Merge Ready` and `Blocked` are terminal for the automation. `In Review` remains a
terminal state only for backward compatibility with issues created under the previous policy.

## Durable Workpad

Every dispatched issue has exactly one Linear comment containing
`<!-- symphony-workpad:v1 -->`. It records only durable orchestration facts: current phase and next
action, PR identity, last processed head, checks, review-request markers, counters, decision
summaries, blockers, and created follow-up candidates. Acceptance criteria, follow-up descriptions,
and frozen contracts remain authoritative in their Linear source sections and are re-read rather
than copied into the Workpad. Symphony agents create or update it through the authenticated
`linear_graphql` dynamic tool. Symphony intentionally keeps `LINEAR_API_KEY` out of the agent
process; the repository helper below is only for human-operated diagnostics from an environment
that already has that credential:

```sh
python3 scripts/symphony_linear.py workpad get YU-123 > /tmp/YU-123-workpad.md
python3 scripts/symphony_linear.py workpad upsert YU-123 --file /tmp/YU-123-workpad.md
python3 scripts/symphony_linear.py state YU-123 'Auto Review'
```

Multiple marked comments are treated as corruption and block rather than guessing. Bodies are
checked for common token formats before publication. Git and the live PR remain the source of truth
for code; the Workpad is the durable orchestration ledger.

## Two-review adjudication

OpenAI's GitHub connector and Qodo continue to generate reviews. The workflow only consumes them.
It derives reviewer coverage from GitHub: each PR must receive at least one completed Codex review
and one completed Qodo review across its history. Until that coverage is complete it requests only
the missing source or sources. Afterward it requests Qodo alone for each new head and falls back once
to Codex only when Qodo times out. Every reviewed head still receives at least one exact-head review,
followed by a 60-second quiet period and one 30-second delayed recheck. Older-head evidence counts
only toward PR-wide coverage, never as the current-head review.

Luna writes a review packet and invokes:

```sh
scripts/symphony_sol_review /tmp/review-packet.md /tmp/decision.json
```

The packet starts with a fixed six-line trusted header containing version, head, counters, and the
current-head reviewer (`codex`, `qodo`, or `both`). Review text begins only after an explicit header
terminator. The wrapper binds the invocation to those fixed control facts. Sol does not repeat them
in its output, and the workflow re-reads the GitHub head before applying the decision. PR-wide
reviewer coverage is checked directly from GitHub and is not copied into the packet or Workpad.

The wrapper strips common Linear/GitHub credentials, refuses likely secrets in the packet, and runs
an ephemeral, read-only GPT-5.6 Sol/xhigh adjudication with only the packet as its task input. It
requires schema-valid JSON and does not second-guess Sol with a semantic validator. Sol returns only
packet finding IDs, `fix_now`/`backlog`/`reject` dispositions, rationales, and disposition-specific
instructions or titles. It is instructed to judge only the packet and cannot modify the checkout.
It also refuses packets above 4,000 lines or 1 MiB instead of silently truncating review evidence.

For each finding, Sol reasons from current acceptance criteria, known later
issues/dependencies/frozen contracts, and YAGNI risk, but emits only its final disposition and
rationale. A required current change is `fix_now`; an evidenced later-owned improvement is
`backlog`; unsupported, resolved, or design-conflicting advice is `reject`. Sol has no human or
conflict escape disposition.

`Rework round` and `Reviewed heads` are informational counters, not quotas. A new push changes the
head and forces one exact-head review and all gates to be evaluated again. A finding is never waived
or sent to `Blocked` merely because the PR has used many review heads.

## Merge-ready and Backlog safety

After external review reaches zero `fix_now` findings and base synchronization is current, the final
candidate must pass `$no-comments`. Controller and reviewer use the same absolute workspace, explicit
changed-file list, and full SHA-256 of the binary full-index diff. The controller verifies the digest
before and after review. A mismatch invalidates the result and is retried once; a second mismatch is
an execution failure. Any No Comments fix that changes a tracked candidate file is pushed and
returned to exact-head external review before finalization runs again.

`Merge Ready` requires a fresh connector read proving that the recorded head is still current, PR
history contains both reviewer sources, at least one current-head review and its late comments were
processed, `fix_now` findings are zero, required verification passed, the tree is clean, finalization
passed, and base synchronization is satisfied. An empty checks/status list is allowed unless the
repository identifies required checks. The workflow never merges.

Terminal `Blocked` is forbidden while intentional work exists only as uncommitted changes. Such an
issue remains in `Rework` with an explicit recovery action so Symphony does not discard its workspace.

Out-of-scope improvements are created in `Backlog`, never `Todo`. Agents query and mutate them
through `linear_graphql`. For human-operated diagnostics, the helper derives a stable source/title
fingerprint and returns an existing candidate instead of creating a duplicate:

```sh
python3 scripts/symphony_linear.py backlog YU-123 \
  --title 'Narrow follow-up title' --body-file /tmp/follow-up.md
```

The workflow caps candidates at three per source issue, rejects reviewer wording alone as evidence,
and never executes a candidate automatically.

## Safe rendering and rollout

`zsh scripts/symphony render` writes and validates a temporary workflow, then atomically replaces
the complete runtime workflow before publishing its SHA-256 sidecar. This prevents Symphony's
dynamic watcher from observing partial workflow content; it is not a two-file transaction. A
concurrent status read may briefly report a mismatch, and interruption between the two renames
leaves a persistent mismatch. Treat either case as an unverified rollout: do not activate the
canary until a completed render and `status --json` report `runtime_verified: true`.

Do not render or restart merely because this branch exists. After the implementation PR is tested,
reviewed, and human-merged, use this rollout sequence on the Mac mini:

1. Confirm no issue is active in `In Progress`, `Auto Review`, or `Rework`; preserve any workspace
   that is not clean.
2. Confirm the required Linear states exist with the exact names above.
3. Update the repository checkout by the team's normal non-destructive, fast-forward-only process.
4. Run `zsh scripts/symphony check`; this validates the template and reports its prospective hash
   without changing the runtime workflow.
5. Run `zsh scripts/symphony render`, inspect `status --json`, and observe the existing process
   reload. Restart only if the status/log evidence
   proves reload did not occur and no agent is active.
6. Activate one disposable `Todo` issue and prove Workpad creation, both review-source attribution,
   Sol output validation, and a non-merging terminal transition before restoring normal concurrency.

The current Mac mini service and active workspaces are intentionally unchanged by repository tests.
