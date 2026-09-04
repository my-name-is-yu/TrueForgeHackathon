# Character Robot Studio product-agent instructions

These instructions describe the local, shared-browser Character Robot Studio
loop.  The agent operates the top-level WebMCP tools registered by the page at
`/studio`; the page is the shared visual surface and must not contain a second
chat, agent loop, or renderer.

## Goal-only operation

Start from the user's natural-language character goal and explicit size or
environment constraints.  Do not require the user to provide tool names, tool
order, node IDs, raw CAD, mesh, MJCF, executable code, or numeric repair
values.  Read the current Studio context before making a change and use only
the advertised constraints, profiles, scenarios, and exact draft or revision
identities returned by that context.

The current standalone surface exposes these eight V1 tools:

1. `get_studio_context` — read the shared head, draft, profiles, evidence
   policy, and project generation.
2. `set_design_draft` — create or replace a complete typed character-robot
   draft.
3. `revise_design_draft` — apply a bounded semantic edit to the exact draft
   hash while preserving unrelated parts.
4. `inspect_design` — inspect the exact draft or immutable revision and its
   compiler-provided geometry.
5. `preview_scenario` — preview an advertised behavior for the exact target.
6. `validate_design` — run the bounded digital checks and report evidence,
   warnings, measurements, and repair suggestions.
7. `create_revision_from_draft` — commit an exact draft as an immutable digital
   revision when the user wants to finalize that design state.
8. `prepare_build_pack` — prepare an evidence-gated artifact manifest for an
   immutable revision without downloading or writing hardware.

After each mutation, use the returned identity and readiness information.  If
the server reports a stale project, generation, draft, or target, fetch a
fresh context and reapply the intended semantic change to the current target.
Never force a stale write, guess a replacement hash, or silently fall back to
an older preview.

Use the shared page to inspect visible parts and verify that the displayed
preview matches the exact target.  A selection is ephemeral inspection state;
it is not a new design revision and must not be treated as one.

## Evidence and claim boundary

The Studio can establish typed design data, compiler geometry identity,
bounded digital checks, scenario previews, revision history, and digest-bound
artifacts.  These are digital design claims only.  `digital_checks_passed` or
an `experimental_ready` Build Pack does not mean that the robot is safe,
manufacturing-qualified, physically verified, or ready for replication.

Keep unresolved catalog facts, fit, power, thermal, motion, and emergency-stop
work visible as limitations.  Do not invent missing manufacturer data or
promote estimates to physical evidence.

## Human-only actions

The agent may prepare and explain digital artifacts, but a human must decide
and perform every external physical action:

- download or transport files from the local Studio;
- purchase or substitute components;
- fabricate, print, assemble, or modify parts;
- install or flash firmware and configuration;
- connect power, energize, or power on a robot; and
- accept physical safety, fit, performance, or build completion.

The agent must not represent a prepared Pack as a completed physical build and
must not trigger any of those actions through WebMCP.

If WebMCP is unavailable, leave the visual editor usable as a manual surface
and report that site tools are unavailable.  Do not emulate WebMCP by adding a
chat surface inside the page.
