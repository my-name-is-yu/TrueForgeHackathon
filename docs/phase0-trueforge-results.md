# Phase 0 TrueForge results

Overall result: `PASS` (5/5 gates)

The probe used the pinned TrueForge `0.1.4` standalone runtime, a saved localhost
Streamable HTTP connection, a synthetic non-production MCP facade, and a local
CGL render from the pinned upstream server. The runtime state and sandbox were
created outside the Git checkout. No credentials, host paths, or private fixture
values were retained.

| Gate | Result | Sanitized measured evidence |
| --- | --- | --- |
| Saved HTTP authentication and Origin boundary | `PASS` | The saved connection authenticated with its static bearer and allowed Origin. Wrong bearer was rejected with observed status 401, and wrong Origin was rejected with observed status 403. Streamable HTTP returned the seven planned tool schemas. |
| Large Tool Response | `PASS` | `inspect_asset` returned exactly 256 synthetic rows. TrueForge offloaded the large response, exposed a bounded file reference, and the sandbox Python probe read and analyzed all 256 rows. |
| Sandbox isolation and network measurement | `PASS` | Sandbox Python reported the exact checkout and private-runtime boundary sentinels inaccessible, and an observed outbound network attempt blocked. Non-target-revealing per-session sentinel copies were used; host checks validated regular-file metadata and exact contents, while sandbox metadata and content access were denied. Bounded scans of the facade response, model requests, and retained event stream found no protected path or sentinel encoding. Only boolean measurements were retained. |
| AgentSpec and approval boundary | `PASS` | The resolved spec was serial, disabled parallel tool calls, selected exactly seven planned tools, enabled sandbox/LTR, and required approval for `publish_revision`. Only `inspect_asset` and `publish_revision` were implemented and exercised; the other selectors were schema-only and fail closed. The publish request paused at `tool.approval_required` and publish call count was zero. |
| CGL image transport | `PASS` | A pinned-upstream CGL render produced one `image/png` content block at 160 by 120. TrueForge's MCP transport received the block without a host path, and the model request contained no image payload. |

## Reproduction

```text
npm ci
uv sync --frozen
uv run pytest tests/phase0/upstream -q
uv run pytest tests/phase0/trueforge -q
uv run python -m spikes.phase0.trueforge.probe
git diff --check
```

The fresh verification produced 7 upstream tests passed, 28 TrueForge tests
passed, and two standalone live probe runs with `PASS` for all five gates.
Each successful run left zero per-run runtime directories. A bounded synthetic
startup failure was also exercised and left zero per-run runtime directories.

The live probe keeps failure output bounded to `BLOCKED_HARD_GATE`; it does not
print raw TrueForge traces or sandbox-sensitive values. A final lockfile is
accepted only with the upstream 4/4 regression and this TrueForge 5/5 result.
