# TrueForgeHackathon agent rules

## Scope and source of truth

- Work from the current issue, pull request, repository source, and tests. Do not rely on an older task summary when live evidence is available.
- Keep each pull request limited to one executable issue. Put concrete, evidenced, out-of-scope improvements in Backlog instead of widening the current pull request.
- Use a `yu/` branch and target `main`. Continue an existing pull request only after verifying its open state, exact head branch, current head SHA, and `main` base.
- Preserve unrelated work and explicit user fences. Never force-push, merge, close a pull request, or mark an issue Done unless the user explicitly asks.

## Implementation and verification

- Reproduce or inspect current behavior before editing. Implement the smallest complete change required by the current acceptance criteria and known contracts.
- Prefer a small root-cause reshape over stacked local guards only when the current requirement needs it and it reduces duplicated checks or inconsistent outcomes. Do not add abstractions for hypothetical reuse.
- Run the most relevant focused tests, then the repository-wide suite when the available environment supports it. Record exact commands, results, and any omitted verification.
- A pull request is not ready merely because it is mergeable or GitHub reports no configured checks.

## Pull-request review

- Before acting on review feedback, reread the current pull-request head, reviews, inline comments, top-level comments, checks, acceptance criteria, dependencies, and frozen contracts.
- Require at least one completed OpenAI Codex review and one completed Qodo review during the pull request. After both sources have reviewed the pull request, use Qodo for later heads unless the user requests another reviewer or Qodo is unavailable.
- Attribute findings to the reviewed head. Do not treat an older-head finding as current without checking whether the current code already resolves it.
- Classify every distinct finding as:
  - `fix_now`: required by current acceptance criteria or an existing contract;
  - `backlog`: concrete and evidenced, but owned by later or out-of-scope work;
  - `reject`: duplicate, already fixed, unsupported, false positive, or conflicting with the current design.
- Apply all `fix_now` findings on the same pull-request branch, run relevant verification, push only when the current task authorizes the PR workflow, and obtain a review of the new head. Do not impose a retry quota; stop only for an observable blocker or user decision.
- Reply with evidence when rejecting a reviewer finding. Create or identify the Backlog issue before calling a `backlog` disposition complete.
- Only a human may make the final merge decision.

## No Comments final review

Run this final review only after the latest external review has no remaining `fix_now` findings and the branch is synchronized with `origin/main`.

1. Require a clean working tree. From the exact absolute workspace, record the complete target list from `git diff --name-status --no-ext-diff origin/main...HEAD` and the SHA-256 of `git diff --binary --full-index --no-ext-diff --no-textconv origin/main...HEAD`.
2. Give that exact scope and digest to one independent read-only reviewer. The reviewer must inspect only the supplied candidate, recompute the same digest before reporting, and make no edits.
3. Audit comments as possible unresolved design work. Remove comments that merely narrate obvious code, preserve dead approaches, or claim constraints without evidence. Prefer types, tests, runtime checks, or lint rules for enforceable constraints.
4. Preserve comments that explain non-obvious reasons, external constraints, security-sensitive ordering, invariants, compatibility requirements, or public API contracts that the code cannot express clearly on its own.
5. Verify every proposed deletion against current callers, tests, and relevant history. Do not accept scope escapes or delete a comment merely because a reviewer dislikes comments.
6. Recompute the candidate digest and verify the tree stayed clean after the read-only review. A mismatch invalidates the review; investigate instead of applying its findings.
7. Apply only verified in-scope findings. If any tracked candidate file changes, rerun relevant tests and return the new head through external review before repeating this final review.
8. Report deleted comments, retained comments with their reason, root-cause fixes, verification, and remaining work. Do not call the pull request ready until the latest head, reviews, checks, clean tree, base synchronization, and No Comments result have all been freshly verified.
