# Character Robot Studio V2 — Whole-Robot WebMCP Completion Plan

## Objective

Turn Character Robot Studio into a GPT-6 Astra-ready WebMCP that can produce one internally
consistent character-robot design from a goal-only request. The design must cover appearance,
component selection, internal mechanics, electronics, control/runtime, manufacturing data,
verification, revision history, and a digest-bound Build Pack.

The reference request is:

> 高さ180mm以内で、屋内の机上を低速で動き、人に挨拶できるペンギン型小型ロボットの設計を完成させてください

V2 is complete when this request can reach `design_complete` without hidden tool names, node IDs,
numeric repair hints, or manual Spec edits.

## Fixed decisions

- The reference controller is M5Stack CoreS3 when its safety-critical power assumptions are
  isolated. GoPlus2 may be used only where its own manufacturer evidence supports that use; it is
  not assumed to be a motor/servo power stage.
- Motors, servos, wheels, battery, protection, connectors, and fasteners are selected from the
  provenance-aware catalog. Official manufacturer-document gaps for CoreS3 and GoPlus2 remain
  unknown/ineligible for affected uses until resolved; the plan never invents those values.
- The catalog is datasheet-backed, not physically qualified. Unknown physical values must remain
  unknown and may not be promoted from estimates.
- Completion for this plan is the complete digital design and Build Pack. Building a reference
  robot and physical qualification are later goals.
- The product path is the shared Codex/ChatGPT browser plus WebMCP. A Responses API runner exists
  only to make GPT-6 Astra (`gpt-6-astra`) evaluation reproducible; it does not add a second chat
  surface to the page.
- V2 replaces the current V1 contract. V1 projects are retained on disk but are not automatically
  migrated; V2 returns `UNSUPPORTED_SCHEMA_VERSION` for them.
- Output is printer-neutral. STEP, per-part STL, and 3MF are the manufacturing source artifacts;
  no printer-specific G-code or printer qualification is required.
- Pi, arbitrary controllers, custom PCBs, custom battery packs, cloud deployment, accounts/auth,
  purchasing, fabrication, flashing, energizing, and physical acceptance are out of scope.

## Execution policy

- Every implementation task and implementation subtask must run with model
  `gpt-5.6-luna` and reasoning effort `max`.
- Create one executable GitHub issue per pull request. Use `yu/` branches and the dependency graph
  below.
- Qodo review and Codex automatic review are advisory only. They must not block merge or force an
  otherwise unnecessary wait. Findings are still inspected and classified as `fix_now`,
  `backlog`, or `reject` when available.
- The controlling agent is explicitly authorized to merge PRs in dependency order when the PR's
  acceptance criteria, focused verification, required integration gates, base synchronization,
  and clean-tree checks pass. A human review decision is not an additional merge gate for this
  plan.
- Do not merge around failed tests, unresolved in-scope correctness or safety findings, stale base
  state, a dirty candidate tree, or missing real-artifact verification.
- Do not force-push unless a later explicit instruction authorizes it. Preserve unrelated work.
- External purchases, downloads, fabrication, flashing, power-on actions, and physical evidence
  submission remain human-only even though code merges are agent-authorized.

## Completion semantics

The product must report design completeness and evidence confidence separately:

- `design_complete`: all required design domains and interoperable artifacts are present and pass
  cross-domain digital checks.
- `datasheet_checked`: component facts used by the design are traceable to manufacturer documents.
- `physical_verification_pending`: fit, current, thermal, motion, and safety have not been measured
  on an assembled robot.
- `within_qualified_profile` and `exact_build_verified` remain unavailable until a later physical
  qualification goal supplies trusted evidence.

The product may not call a design physically safe, manufacturing-qualified, replication-ready, or
exact-build verified during this plan.

## Canonical V2 project model

Replace the monolithic `CharacterRobotSpec` with one revision-bound `RobotSystemSpec` containing:

- `Requirements`: immutable original request, intended environment, dimensions, speed, voltage,
  required behavior, safety constraints, assumptions, and unresolved questions.
- `VisualDesign`: motif, outer morphology, colors, face/display definition, materials, and surface
  features.
- `ComponentSelection`: exact manufacturer, MPN, variant, quantity, catalog digest, evidence basis,
  and approved alternatives.
- `MechanicalAssembly`: printable parts, shell splits, cavities, mounts, bosses, inserts, fasteners,
  joints, shafts, bearings, transmission, datum frames, and service openings.
- `SpatialLayout`: component instances, transforms, keepouts, swept volumes, cable paths, mass,
  center of gravity, inertia, and support footprint.
- `ElectricalDesign`: typed pins and nets, voltage domains, pin mapping, connectors, wire, fuse,
  switch, E-stop path, and power tree.
- `RuntimeBinding`: source/build identity, binary digest, I/O mapping, calibration requirements,
  motion limits, watchdog, communication-loss behavior, and fault state machine.
- `ManufacturingPlan`: material constraints, minimum wall, fit tolerances, part orientation,
  support guidance, labeling, assembly order, and inspection checkpoints.
- `VerificationPlan`: requirement-to-check trace, calculations, simulation inputs, future physical
  procedures, acceptance thresholds, and evidence references.
- `ArtifactManifest`: every source and derived artifact with media type, byte size, and SHA-256.

Maintain separate durable objects for mutable drafts, immutable revisions, append-only evidence,
content-addressed artifacts, GPT-6 Astra evaluation trials, and ephemeral UI inspection state.

Every entity has a stable ID. Every write cites a server-issued `active_target_token`. A change
returns changed entities, invalidated domains, invalidated artifacts/evidence, blockers, and next
actions. For example, replacing a motor invalidates its mount, axle/wheel compatibility, driver
current margin, battery budget, harness, runtime mapping, dynamics checks, and Build Pack.

## WebMCP V2 surface

Do not preserve an exact tool-count invariant. Expose only the actions relevant to the current
phase when the host supports tool search; otherwise register the full compact set.

1. `get_project_state`
   - Read exact target, readiness matrix, blockers, unknowns, and available actions.
   - Default to a compact summary. Fetch one domain or paginated history explicitly.
2. `define_robot_goal`
   - Create immutable requirements from the user's request.
   - The model may add safe assumptions but may not relax a user must-have.
3. `query_component_catalog`
   - Search eligible catalog entries by dimensions, voltage, current, torque, speed, mass,
     mounting, connector, and capability.
4. `create_system_draft`
   - Create the initial architecture using CoreS3/GoPlus2 and eligible catalog IDs.
5. `revise_mechanical_design`
   - Apply typed changes to outer morphology, component layout, mechanisms, mounts, cavities,
     fasteners, openings, and cable routes.
6. `revise_electrical_design`
   - Apply typed changes to nets, pins, power, wiring, protection, and harness definitions.
7. `configure_runtime_behavior`
   - Bind I/O and configure safe boot, idle, greet, stop, watchdog, and fault behavior.
8. `inspect_design`
   - Inspect exterior, interior, section, exploded, clearance, wiring, and scenario views.
   - Return a digest-bound image artifact usable by the shared UI and GPT-6 Astra evaluation runner.
9. `run_design_checks`
   - Run selected geometry, fit, kinematics, engineering-budget, electrical, firmware-contract,
     manufacturing, and simulation suites.
10. `get_operation`
    - Read status, partial results, retryability, and cancellation state for long work.
11. `create_revision`
    - Commit an exact draft/head pair as an immutable revision.
12. `prepare_build_pack`
    - Operate on an immutable revision only. Return structured blockers instead of a provisional
      Pack when any required domain is incomplete.

Read tools must not mutate project generation. Telemetry belongs in the evaluation ledger.
Errors use a shared `{code, path, expected, actual, retryable, current_target, next_actions}` shape.

## Engineering requirements

### Component catalog

Keep CoreS3 as the isolated reference controller where its documented power boundary is sufficient.
Treat GoPlus2 as an evidence-gated optional board use, never as an assumed motor/servo power stage.
For each remaining category, admit one default and at most two alternatives: motor/gearbox,
wheel/hub/axle, pan/tilt servo and horn, caster/skid, protected battery and charger,
fuse/switch/E-stop, fasteners/inserts/spacers, and connectors/wire.

An entry is eligible only when the provenance-aware #89 contract has manufacturer documentation
for the exact MPN/variant, envelope, mount or shaft geometry, connector information, operating
voltage, applicable current limits, torque/speed where relevant, and mass. The current official
CoreS3/GoPlus2 documents do not fill every safety-critical module/power-stage field; those gaps
remain explicit unknowns and make the affected use ineligible. Price and availability are
timestamped advisory data, not immutable design evidence. Issue #102 may select a datasheet-
complete alternative or isolate CoreS3, but it may not promote an undocumented GoPlus2 power path.

### Mechanical design

- Generate hollow, split enclosures rather than solid planning shells.
- Generate component cavities, mounting bosses, heat-set insert holes, screw holes, locating
  features, wheel/motor interfaces, caster/skid support, servo brackets, hard stops, cable routes,
  strain relief, connector openings, ventilation, and sensor/display mounts.
- Replace compiler-sized wheel/neck proxies with selected-component geometry and interfaces.
- Keep expressive outer skin separate from the mechanical chassis/envelope.
- Support per-entity material/color and generic surface patches rather than motif-specific code.
- Validate containment, pairwise interference, ground clearance, support polygon, service access,
  connector access, cable bend/clearance, and complete swept volumes.

### Engineering solvers

- Size drive motors from mass, speed, acceleration, rolling resistance, incline/step assumptions,
  wheel radius, and gearing.
- Size servos from head mass/CoG, range, duty, and mechanical stops.
- Verify RPM, torque, continuous/stall current, driver and supply margin, shaft/hub compatibility,
  and thermal duty from catalog values.
- Derive assembly mass and CoG from placed components plus printed-part material estimates.
- Calculate idle, typical, peak, and stall power; regulator/driver loss; voltage-sag margin;
  connector/wire/fuse derating; usable battery energy; and estimated runtime.
- Treat MuJoCo as a planning result until measured dynamics exist. It may not independently confer
  `design_complete` when its inputs are assumptions.

### Electrical and runtime

- Produce a typed netlist, pin map, power tree, harness, voltage-domain checks, connector/polarity
  definitions, wire gauge/length/labels, fuse/current limits, main switch, and E-stop interruption
  path.
- Publish one reviewed CoreS3 firmware runtime and digest-pinned binary that consumes the V2
  config.
- The runtime defaults to motors off and implements watchdog/deadman, communication-loss stop,
  velocity/acceleration/jerk limits, servo limits, current/voltage/temperature handling, brownout,
  stall/fault behavior, install, rollback, and recovery.
- V1 behavior comprises safe boot, idle, greet, commanded stop, communication-loss stop, and fault.
  Decorative listen/think/delight/sleep flows are deferred.

### Manufacturing validation

Generate manufacturing probes directly from the real B-Rep. Validate manifold/solid count,
minimum wall, holes and fits, cavity containment, connector access, cable clearance, swept volume,
support footprint, estimated mass/CoG, printer-neutral part bounds, and artifact readability.

Keep material, wall, and fit requirements in the design. Remove printer/nozzle/layer settings from
the canonical design unless explicitly supplied as optional advice. Do not emit G-code.

## Design-Complete Build Pack

Generate the Pack only when every required domain is `checked` and no required unknown remains.

Mechanical artifacts:

- `robot-system.json`
- `preview.glb`
- `assembly.step`
- `parts/*.stl`
- `print-project.3mf`
- `critical-dimensions.json`
- digest-bound exterior, interior, section, and exploded PNGs

Electrical artifacts:

- `bom.json` and `bom.csv`
- `electrical-netlist.json`
- `pin-map.csv`
- `power-budget.json`
- `wiring.svg`
- `harness.csv`

Runtime artifacts:

- CoreS3 firmware binary
- `runtime-lock.json`
- `character-config.json`
- install/recovery instructions
- source/build/binary digests

Manufacturing and verification artifacts:

- `manufacturing-plan.json`
- `assembly.md`
- `calibration-plan.json`
- `verification-plan.json`
- `requirements-coverage.json`
- `readiness.json`
- limitations with `physical_verification_pending`
- canonical manifest binding every file by SHA-256

Delete the static supplemental BOM, static per-profile wiring table, and provisional Pack fallback.
An incomplete design returns missing SKU/design/check lists and next actions, not downloadable
placeholder instructions.

## Pull-request dependency graph

Create the issue before beginning each new row.

| Order | Branch or existing PR | Executable outcome | Depends on |
| --- | --- | --- | --- |
| 0 | PR #84 | Reverify and land face rendering on compiler GLBs. | `main` |
| 1 | PR #86, reworked in place | Retain target-bound canonical views; remove pseudo-precision pixel metrics and duplicate rendering; expose image artifacts to the host/eval runner. | #84 |
| 2 | Issue #88 (`yu/studio-v2-project-contract`) | Add `character-robot/v2`, immutable requirements, readiness matrix, target token, and explicit V1 rejection. | #84 |
| 3 | Issue #89 (`yu/cores3-datasheet-catalog`) | Add the provenance-aware V2 component catalog; retain official CoreS3/GoPlus2 manufacturer-document gaps as unknowns. | #88 |
| 4 | Issue #102 (`yu/cores3-reference-stack`) | Select a datasheet-complete reference power/drive stack, isolating CoreS3 where supported and never assuming GoPlus2 is a motor/servo power stage. | #89 |
| 5 | Issue #90 (`yu/robot-system-architecture`) | Add component instances, interfaces, placement, and dependency invalidation. | #88, #89, #102 |
| 6 | Issue #91 (`yu/robot-engineering-solvers`) | Add drive/servo sizing plus power, runtime, mass, CoG, and support solvers. | #89, #90, #102 |
| 7 | Issue #92 (`yu/robot-mechanical-feature-cad`) | Generate enclosure, mechanisms, mounts, cavities, fasteners, cable paths, caster/skid, and manufacturing features. | #90, #91 |
| 8 | Issue #93 (`yu/robot-electrical-design`) | Generate validated netlist, pin map, power tree, protection, and harness. | #90, #91, #102 |
| 9 | Issue #94 (`yu/cores3-safe-runtime`) | Publish the fixed safe runtime, binary, config contract, installation, and recovery. | #93 |
| 10 | Issue #95 (`yu/robot-behavior-safety`) | Implement the V1 state machine and bind scenarios to runtime limits and faults. | #91, #94 |
| 11 | Issue #96 (`yu/robot-manufacturing-checks`) | Connect real B-Rep probes and printer-neutral manufacturing checks. | #92 |
| 12 | Issue #97 (`yu/studio-standalone-entrypoint`) | Extract the standalone Studio application entrypoint and product-agent instructions. | #88 |
| 13 | Issue #98 (`yu/studio-v2-webmcp`) | Expose compact V2 tools, read/write semantics, async operations, and structured errors. | #90–#97 |
| 14 | Issue #99 (`yu/studio-system-inspection-ui`) | Show exterior/interior/section/exploded/wiring/scenario views and domain readiness. | #86, #92, #93, #95, #98 |
| 15 | Issue #100 (`yu/robot-design-readiness`) | Enforce requirement and cross-artifact consistency across CAD, BOM, electrical, runtime, manufacturing, and verification. | #91–#99 |
| 16 | Existing #81 (`yu/robot-build-pack-v2`) | Generate the strict Design-Complete Build Pack and satisfy the deep instruction checks. | #100 |
| 17 | Issue #101 (`yu/astra-whole-robot-eval-runner`) | Add the configurable GPT-6 Astra (`gpt-6-astra`) Responses evaluation runner, image bridge, and append-only trial ledger. | #98–#100 and #81 |
| 18 | Follow-on evidence gate | Run frozen-head GPT-6 Astra goal-only trials and publish evidence only after all gates pass. | #101, #80, #82 |

Mechanical, electrical, and runtime lanes may run in parallel after the catalog and architecture
merge. Pack-delivery reliability and the GPT-6 Astra runner may run in parallel after the Pack contract
stabilizes. Branch every stacked PR from its exact listed dependency, then retarget to `main` after
the dependency merges.

Issue disposition:

- #83/#84: retain and complete first.
- #85/#86: rework as the bounded visual evidence foundation.
- #88: own the V2 project, requirements, readiness, target-token, and V1-admission boundary.
- #89: use provenance-aware eligibility; do not fill the audited CoreS3/GoPlus2 documentation gaps.
- #102: resolve the reference power/drive stack after #89, isolating CoreS3 where supportable and
  replacing or constraining undocumented GoPlus2 power uses.
- #90–#101: execute the numbered V2 DAG in the table above.
- #87: remains a separate issue; architecture must not claim to close it.
- #81: incorporate its deep instruction checks into the strict V2 Build Pack gate.
- #80 and #82: keep as independent post-Pack reliability PRs.
- #79: replace its acceptance criteria with the whole-robot V2 evaluation below.
- #77: defer until a physical-reference-build goal; V2 physical promotion remains disabled.

## Verification and merge gates

Every PR must pass:

- its issue acceptance criteria;
- focused unit, contract, and integration tests for changed behavior;
- malformed-input, stale-target, and downstream-invalidation cases where applicable;
- `git diff --check` and a clean candidate tree;
- exact-head CI for required jobs;
- real-artifact verification when the PR affects CAD, runtime, UI rendering, or packaging.

Run the full Python/Web/Ruff/build/audit suites at integration checkpoints 4, 10, 14, and 18,
and whenever a change plausibly affects unrelated product behavior. Qodo and Codex review results
are advisory; merge waits only for required verification and resolution of known in-scope
correctness or safety findings.

Before merging an implementation PR, the controlling agent records the issue, dependency head,
candidate SHA, changed-file scope, exact test commands/results, unresolved findings, and why the
acceptance criteria are satisfied. Merge in dependency order and immediately refresh downstream
bases.

## GPT-6 Astra evaluation gate

Run the exact reference request ten times on one frozen SHA using model `gpt-6-astra` with the
configured maximum reasoning effort. Initial prompts may not reveal tool names, order, entity IDs,
or numeric repair values. No manual Spec edits are allowed.

All 10 runs must:

- reach `design_complete`;
- preserve the 180 mm, indoor, low-speed, greeting requirements;
- select only eligible CoreS3 catalog components;
- include every required component in the BOM;
- keep CAD, BOM, netlist, runtime, assembly, and test-plan IDs consistent;
- pass torque, speed, power, mass, CoG, support, fit, clearance, electrical, runtime, and
  manufacturing checks;
- expose recognizable exterior views and inspectable internal/section views;
- preview greeting and all required safety states;
- produce byte-readable STEP, STL, 3MF, GLB, JSON, CSV, SVG, binary, and ZIP artifacts;
- produce one digest-consistent immutable revision and Build Pack;
- leave unknown physical facts unknown and report `physical_verification_pending`.

Then run two trials each for owl, cat, rabbit, duck, and a non-animal mascot. At least 8 of these 10
held-out trials must reach `design_complete` without character-specific compiler rules. Record the
goal, model/snapshot, prompt and tool-schema digest, response/tool call IDs, tool arguments/results,
changed entities, invalidations, Spec/geometry/image/manifest hashes, latency, usage, terminal
reason, and typed failure code.

The final evidence change is docs-only. If any production change is required after trials start,
retain prior runs as diagnostics, freeze a new SHA, and restart the counted trials.

## Final acceptance

The plan is complete when the numbered #88–#101 DAG and the supporting Build Pack/reliability work
are merged and the frozen V2 head satisfies all deterministic, artifact, browser, and GPT-6 Astra
gates. The final report must list every merged PR and SHA, exact test results, 10/10 reference-goal
results, held-out results, Build Pack manifest digest, known physical unknowns, and the explicit
statement that no assembled robot or physical safety claim was produced.
