# TrueForgeHackathon

Project workspace for the [Agent Harness Hackathon](https://www.wemakedevs.org/hackathons/trueforge), running August 24–30, 2026.

## Local harness

Requirements:

- Node.js 22.14 or later
- A model-provider API key

Install and start the pinned TrueForge version:

```bash
npm install
npm run trueforge
```

Open <http://localhost:8790>. In **Settings**, configure a model provider and connect only the MCP services and accounts you are authorized to use. Standalone mode stores its SQLite state outside this repository and uses the local sandbox fallback when supported.

The current local setup has been smoke-tested with:

- TrueForge 0.1.4
- OpenAI model provider
- Exa MCP connector
- Local sandbox execution
- Saved `hackathon-starter` agent

Provider keys and personal data must not be added to this repository or shown in the demo video.

## Symphony + Linear

[OpenAI Symphony](https://github.com/openai/symphony) monitors a dedicated Linear project
and dispatches isolated Codex workspaces for explicitly opted-in issues.

Configured Linear resources:

- Workspace/team: `Yu`
- Project: [TrueForgeHackathon](https://linear.app/yuarenotyu/project/trueforgehackathon-e396beca278f)
- Project slug: `trueforgehackathon-e396beca278f`
- Active states: `Todo`, `In Progress`, `Auto Review`, `Rework`
- Automated review/rework states: `Auto Review`, `Rework`
- Human merge-decision state: `Merge Ready`
- Safety stop state: `Blocked`
- Dispatch gate: move a ready issue from `Backlog` to `Todo`

Symphony runs continuously on the Mac mini. The workflow allows up to four concurrent
agents, ramps cold starts through one `Todo` issue at a time, uses isolated workspaces and
`yu/` branches, and automates review handling up to a final human merge decision. It never merges.
Ready work is
released in dependency-safe waves by moving an issue to `Todo` only after prerequisite PRs are
merged to `main`. Labels are informational and do not control dispatch.
Symphony-launched Codex agents are pinned by this repository to GPT-5.6 Luna with `xhigh`
reasoning. This does not change the Mac mini's host-wide Codex model or reasoning defaults.
They run without approval prompts or a Codex filesystem sandbox because the Mac mini is a
dedicated isolated execution host. This full-host access applies only to agents launched by this
workflow; the merge decision remains human-only.
The HTTP dashboard is disabled because the pinned preview build reports known vulnerabilities
in several web-server dependencies.

For a fresh host, create the ignored environment file and add a narrowly scoped Linear
personal API key:

```sh
cp .env.example .env
brew install mise
zsh scripts/symphony install
zsh scripts/symphony check
zsh scripts/symphony status
zsh scripts/symphony status --json
zsh scripts/symphony start
```

The read-only status command combines LaunchAgent health, Codex activity, workspaces, and Linear
issue state. Pull request, review, and check state are monitored separately through the Codex
GitHub connector so the Mac mini does not need GitHub CLI authentication for status collection.
The command reports one read-only snapshot; the recurring monitor determines stalled work only
after comparing each active workspace across three consecutive observations.

Review-remediation issues may continue an existing open pull request only when the Linear issue
description or durable Symphony Workpad includes both that pull request's URL or number and its
exact head branch. Symphony then fetches and checks out that branch with local Git and uses the
Codex GitHub connector to inspect and update the same pull request; incomplete or conflicting
references become blockers instead of creating a duplicate branch or pull request.

OpenAI Codex and Qodo continue to generate reviews externally. The repository-owned workflow gets
one completed review from each source across the PR, then uses Qodo alone on later heads with one
Codex fallback on timeout. GPT-5.6 Sol deduplicates and adjudicates current-head findings and sends
only `fix_now` findings back to GPT-5.6 Luna for at most nine same-PR rework rounds and ten distinct
reviewed heads. Linear holds one
durable Workpad comment so retries and model boundaries do not depend on a long-lived session.
The merge-ready gate and deduplicated, non-executing Backlog candidate creation are documented in
[`docs/symphony-auto-review.md`](docs/symphony-auto-review.md).

Local credentials, the pinned Symphony source/build, logs, generated workflow files, and
per-issue workspaces live under `.env` and `.symphony/`. The source build applies only
[`symphony/patches/add-castore.patch`](symphony/patches/add-castore.patch) to the pinned
official commit to supply the CA certificate bundle missing from the macOS release executable.

The Mac mini service definition is [`ops/com.trueforge.symphony.plist`](ops/com.trueforge.symphony.plist).
Inspect it with `launchctl print gui/$(id -u)/com.trueforge.symphony` and stop it with
`launchctl bootout gui/$(id -u)/com.trueforge.symphony`.

## Participation checklist

- [x] Complete the official hackathon registration form
- [x] Start TrueForge locally
- [x] Connect a model provider
- [x] Connect a real MCP tool
- [x] Verify sandbox execution
- [x] Create an initial reusable agent
- [x] Make this GitHub repository public
- [x] Install the Qodo GitHub app on this repository before development PRs
- [ ] Develop through pull requests and address Qodo findings before merge
- [ ] Demonstrate a human approval pause before an irreversible action
- [ ] Prepare a roughly three-minute demo video
- [ ] Finish the public README and short project write-up
- [ ] Submit by August 30, 2026 at 8:00 PM London time (August 31 at 4:00 AM JST)

## Local data and secrets

TrueForge standalone mode is intended for localhost development, not shared internet exposure. Its local state currently lives under `~/Library/Application Support/trueforge/`; it is deliberately not part of the repository.
