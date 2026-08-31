# Decision: evaluation ends at the TrueForge approval request

Status: Accepted
Date: 2026-08-31
Decision issue: [#31](https://github.com/my-name-is-yu/TrueForgeHackathon/issues/31)

## Decision

The accepted autonomy-evaluation contract ends when TrueForge emits
`tool.approval_required` for the destructive `publish_revision` request. The submission does not
click approval, invoke the domain publisher, or claim a publication response, receipt, bundle, or
public artifact.

Keep `publish_revision` declared destructive and approval-gated so the submission demonstrates the
real approval pause.

## Consequence

Post-approval materialization and promotion persistence have been removed. An approved direct
server call now fails closed with `PUBLICATION_DEFERRED` before any storage or filesystem access.
The approval pause proves the human boundary without claiming that publication has occurred.
