# Decision: SC1 ends at the TrueForge approval request

Status: Accepted
Date: 2026-08-31
Decision issue: [#31](https://github.com/my-name-is-yu/TrueForgeHackathon/issues/31)

## Decision

The accepted SC1 and hackathon product contract ends when TrueForge emits
`tool.approval_required` for the destructive `publish_revision` request. The submission does not
click approval, invoke the domain publisher, or claim a publication response, receipt, bundle, or
public artifact.

Keep `publish_revision` declared destructive and approval-gated so the submission demonstrates the
real approval pause.

## Consequence

The repository currently contains post-approval materialization and promotion-persistence code.
That surface is outside the accepted contract and will be removed in a separate implementation
pull request. Broad legacy-document alignment remains tracked by
[#41](https://github.com/my-name-is-yu/TrueForgeHackathon/issues/41).
