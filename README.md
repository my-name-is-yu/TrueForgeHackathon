# Asset Autopsy

Asset Autopsy is a local robot-design workbench for the bounded MuJoCo case
`compound-arm-01`. A person edits the Three.js view while Codex operates the same live session
through WebMCP. The browser contains no second chat or agent loop.

```text
Codex conversation <-> WebMCP tools in the live page
                         |
Human <-> Three.js workbench + one-change shared draft
                         |
                 AssetAutopsyService
                    |-- immutable revisions and evidence ledger
                    |-- one-attribute patcher and hidden verifier
                    v
             pinned stdio MuJoCo MCP -> MuJoCo 3.5.0
```

See [the implemented design](docs/asset-autopsy-mvp-design.md) for the responsibility, evidence,
revision, and qualification contracts.

## Local design workbench

```bash
cd web
npm ci
npm run build
cd ..
uv run uvicorn asset_autopsy.workbench:create_workbench_app --factory --host 127.0.0.1 --port 8713
```

Open `http://127.0.0.1:8713` in the Codex desktop browser. The visual editor remains usable when
WebMCP is unavailable and shows a compatibility banner in that case. During frontend development,
run `npm run dev` in `web/` alongside the Python server and open `http://127.0.0.1:5173`.

The registered WebMCP tools are `get_design_context`, `inspect_design`, `run_task`,
`run_experiment`, `query_trace`, `set_draft_patch`, `create_revision_from_draft`,
and `verify_revision`.

The shared evidence view makes the latest experiment trace, the current revision's canonical
change, and the parent-to-child `BehaviorDiff` visible to the person while Codex works. A successful
qualification locks editing and leaves the evidence available for inspection. **Reset session**
starts a fresh service/store generation at `r000`; feedback, source history, review, revert, and any
final decision remain in the Codex conversation and Git instead of being duplicated in the page.
Workbench revision IDs are temporary evidence identities, not Git history.

Because qualification is one-shot, a hidden-suite failure also locks editing and is shown as
`Qualification failed — reset required`; existing evidence remains readable until Reset.

## Streamable HTTP MCP tools

| Tool | Capability |
| --- | --- |
| `open_case` | Read requirements, topology, current head, budgets, history, and patch policy. |
| `inspect_asset` | Inspect authored and compiled public values for an exact revision. |
| `run_task` | Measure a revision and compare a child with its parent under the same condition. |
| `run_experiment` | Test a preregistered causal hypothesis using selected controls and observables. |
| `create_revision` | Create one immutable current-head child bound to experiment evidence. |
| `verify_revision` | Recheck the public pass and consume the one-shot hidden qualification. |

## Local verification

Requirements are Python 3.12, `uv`, Node.js 22, and MuJoCo CGL support for the render gate.

```bash
uv sync --frozen
uv run pytest -q -m "not cgl"
uv run ruff check .
uv run ruff format --check .
git diff --check
cd web
npm ci
npm test
npm run build
npm audit --audit-level=high
cd ..
```

On a CGL-capable Mac, also run:

```bash
uv run pytest -q -m cgl
uv run pytest -q
```

After these checks, serve the built artifact with the Python workbench command above
and run a goal-only end-to-end trial from Codex against `http://127.0.0.1:8713`. Give Codex the
design goal and protected boundaries, but not a diagnosis, tool order, or patch value. Confirm in
the live page that:

1. all eight site tools are available and the initial revision is `r000`;
2. Codex chooses an experiment and trace query, and the resulting trace becomes visible;
3. the shared draft changes the selected joint's live preview before commit, then the revision and
   canonical change become visible;
4. the public child run shows a parent-to-child `BehaviorDiff` before qualification;
5. qualification displays `Qualified — editing locked` without exposing approval controls or a
   promotion-ticket digest; and
6. Reset returns the same visitor to a fresh editable `r000` with no draft, trace, task result, or
   qualification lock.

## Current limits

This milestone intentionally supports one fixed simulated arm and local browser sessions. It does
not include deployment, durable accounts, arbitrary CAD or URDF import, multiple robots, FEA, real
hardware, asset-file materialization, Git-backed asset revisions, or export.
