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
