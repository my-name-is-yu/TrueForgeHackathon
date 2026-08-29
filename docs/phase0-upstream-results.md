# Phase 0 upstream results

Overall result: `PASS` (4/4 checks)

The checks below are the complete Phase 0 upstream gate. A merge or AA-00B
handoff requires all four checks to be `PASS`.

| Check | Result | Sanitized evidence |
|---|---|---|
| Frozen dependency lock | `PASS` | `uv sync --frozen` completed successfully, with the exact MCP SDK, MuJoCo, and pinned upstream commit present in `pyproject.toml` and `uv.lock`. |
| Pinned stdio initialize and required schemas | `PASS` | The child started with `python -m mujoco_mcp --transport stdio`; MCP initialize completed and all seven required tool input schemas matched the committed contract. |
| CGL 160x120 primitive render | `PASS` | The child selected `MUJOCO_GL=cgl`, initialized rendering, loaded the primitive through `xml_string`, and returned one PNG image with dimensions 160 by 120. |
| Success-wrapped upstream error adapter | `PASS` | A malformed synthetic XML load returned MCP success with an error object; the adapter converted it to the fixed bounded `UPSTREAM_UNAVAILABLE` envelope without forwarding upstream details. |

## Verification record

- `uv sync --frozen`: PASS.
- `uv run pytest tests/phase0/upstream -q`: `7 passed`.
- `git diff --check`: PASS.
- Private-data scan over the owned implementation and evidence files: PASS; no credentials, nonce, hidden target, golden XML, host path, or raw private trace was committed.

All four exit checks pass against the committed pins. The result is ready for
the AA-00B dependency-closure handoff after this PR is merged into `main`.
