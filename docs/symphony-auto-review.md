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
| `Rework` | Luna | Apply only findings accepted by Sol to the same PR. |
| `Merge Ready` | Human | All automated gates passed; human may decide whether to merge. |
| `Blocked` | Human | Timeout, conflict, uncertainty, failed gate, or exhausted safety limit. |
| `Done` | Human | Human-owned post-merge terminal state. |

`Auto Review` and `Rework` remain active Symphony states so a process restart can resume from the
Linear Workpad. `Merge Ready` and `Blocked` are terminal for the automation. `In Review` remains a
terminal state only for backward compatibility with issues created under the previous policy.

## Durable Workpad

Every dispatched issue has exactly one Linear comment containing
`<!-- symphony-workpad:v1 -->`. It records acceptance criteria, current phase and next action, PR
identity, exact head SHA, checks, known follow-up/dependency context, frozen contracts,
review-source evidence, Sol decisions, counters, and follow-up
candidates. The helper creates or updates that comment without printing the API key:

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
For each pushed head it waits for both sources (10-minute timeout per source), then a 60-second
quiet period and one 30-second delayed recheck. Evidence for an older SHA is never counted as
completion for the current head.

Luna writes a review packet and invokes:

```sh
scripts/symphony_sol_review /tmp/review-packet.md /tmp/decision.json
```

The packet starts with a fixed seven-line trusted header containing version, head, counters, and
the independently observed Codex/Qodo completion statuses. Review text begins only after an explicit
header terminator. The wrapper parses control metadata only from those fixed lines and rejects any
Sol output whose source statuses differ from the trusted header.

The wrapper strips common Linear/GitHub credentials, refuses likely secrets in the packet, and runs
an ephemeral, read-only GPT-5.6 Sol/xhigh adjudication with only the packet as its task input. It
requires schema-valid JSON. A second deterministic validator enforces the safety invariants:
timeouts, conflicting or
human findings block; accepted findings require rework; completed reviews with no accepted finding
may proceed to the merge-ready gate. Sol is instructed to judge only the packet and cannot modify
the checkout.
It also refuses packets above 4,000 lines or 1 MiB instead of silently truncating review evidence.

For each finding, Sol must state whether it is required by the current acceptance criteria, whether
it aligns with known later issues/dependencies/frozen contracts, and whether implementing it now
would create unnecessary abstraction, freeze future choices, or duplicate later work. High-YAGNI
or explicitly later-owned work cannot enter rework: it is rejected or becomes a deduplicated
Backlog candidate. A requirement needed for the current issue cannot be deferred this way.

There are at most nine automatic rework rounds and ten distinct reviewed heads for the same PR.
This is a review-loop head cap, not a Qodo quota. A new push changes the head and forces both
reviews and all gates to be evaluated again. Accepted findings that remain at the cap move the
issue to `Blocked`; the cap never authorizes merge.

## Merge-ready and Backlog safety

`Merge Ready` requires a fresh connector read proving that the recorded head is still current, both
review sources and late comments were processed, accepted findings are zero, required verification
passed, the tree is clean, and base synchronization is satisfied. The workflow never merges.

Out-of-scope improvements are created in `Backlog`, never `Todo`. The helper derives a stable
source/title fingerprint and returns an existing candidate instead of creating a duplicate:

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
