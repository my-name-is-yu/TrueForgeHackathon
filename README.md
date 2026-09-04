# Character Robot Studio + Asset Autopsy

This repository now contains two local WebMCP workbenches:

- **Character Robot Studio** at `/studio` for designing a bounded indoor character robot from
  a typed semantic specification generated from natural-language intent.
- **Asset Autopsy** at `/` or `/autopsy` for the original fixed MuJoCo arm evidence exercise.

Both surfaces share the same browser session with Codex or ChatGPT. Neither page embeds a second
agent or chat loop.

## Character Robot Studio Maker/Beta foundation

Character Robot Studio is a from-zero design surface, not a gallery of finished robot presets. A
WebMCP-capable model translates a request such as “a timid but curious duck guide” into one bounded
`CharacterRobotSpec`. The model may compose rounded solids, revolves, lofts, sweeps, CSG, mirrors,
and semantic anchors, but it cannot submit Python, arbitrary meshes, MJCF, or firmware source.

```text
natural language
      |
      v
typed CharacterRobotSpec / SemanticEdit
      |
      v
CharacterRobotService --- immutable revision + stale-write guards
      |                         |
      v                         v
isolated CadCompiler       headless MuJoCo 3.5.0
(build123d==0.11.1)        planning checks
      |                         |
      GLB / STEP / STL / 3MF    MJCF / report
      |                         |
      +------------+------------+
                   v
fixed-runtime config + evidence-gated Build Pack
                   |
                   v
content-addressed artifacts + optional durable ProjectStore
```

The first robot family is limited to low-speed indoor differential drive with a pan/tilt neck.
The two initial hardware profiles are `m5-cores3-goplus2/v1` and
`pi-zero2wh-crickit-ws2/v1`. Both are deliberately marked `digital_only`: component dimensions,
planning envelopes, and digital geometry checks do not establish wiring, power, thermal, motion,
fabrication, or safety suitability.

Profile-space component and connector-access keep-out envelopes move as one deterministic layout.
The Showcase compiler grows one eligible rounded shell around that digital AABB with a documented
planning margin, then rejects a user maximum that cannot contain the result. This is enclosure
reflow for preview and constraint feedback, not evidence of a printable cavity or mounting fit.

The Studio registers exactly these eight semantic site tools:

1. `get_studio_context`
2. `set_design_draft`
3. `revise_design_draft`
4. `inspect_design`
5. `preview_scenario`
6. `validate_design`
7. `create_revision_from_draft`
8. `prepare_build_pack`

Draft writes cite the current revision or exact draft hash. An oversized compile is rejected with
measured dimensions and the minimum required expansion, leaving the existing draft untouched.
Revisions are immutable; the first committed draft becomes `r000` (an empty Studio has no
revision). Clicking a semantic part stores an exact draft/revision-bound selection for the next
`get_studio_context` call. The experimental manifest binds the canonical Spec, geometry, catalog,
compiler, CAD-engine, firmware-runtime versions, and every artifact digest. Its
`build_subject_hash` separately binds the build-affecting files without the self-referential
physical-evidence envelope; exact-build records can promote only that service-derived subject.
Preparing a manifest never purchases parts, flashes hardware, or downloads files; downloads remain
visible human actions in the Studio.

Scenario playback rigs the compiler GLB in the browser as wheels plus pan → tilt → generated head
subtree. Deterministic face-display content follows the head and switches from the Spec's expression
timeline; it does not replace or redraw the compiler-provided robot geometry.
`inspect_design` also binds the exact compiler GLB to its draft/revision, Spec, and geometry hashes,
then asks the browser to show fixed front, three-quarter, side, and back observations. The returned
structural semantic-node presence/visibility diagnostics and generic render diagnostics help Codex
compare those views with the motif and design brief; no pixel sampling or pseudo-metrics are used,
and they remain visual observations, not manufacturing or safety evidence.

The current experimental pack includes GLB, canonicalized STEP/STL/3MF, canonical Spec JSON,
provisional BOM and wiring, assembly/calibration instructions, `character.json`, a normalized
fixed-runtime configuration ZIP, calibration template, internally compiled MJCF, simulation and
validation reports, a physical-evidence gate, and a portable project snapshot. CoreS3 and Pi use
separate fixed runtime targets; neither bundle contains model-generated source or an executable.
The same files and a canonical internal index are also packaged into one normalized Build Pack ZIP
when it fits the bounded object and session budgets. If only the aggregate is too large, the
manifest keeps every individual artifact available and reports that the ZIP was omitted. The
runtime lock explicitly reports that no digest-pinned release binary is published yet.

The Maker validation kernel distinguishes manufacturer specifications, physical measurements,
values derived from measurements, and planning allowances. It checks one-solid/manifold status,
printable bounds, minimum wall, holes and fits, component cavities, connector access, swept
volumes, mass/CoG, and loaded power observations. A trusted adapter must provide a complete
measured catalog and B-Rep probes
before those checks can raise evidence above `digital_checks_passed`. Physical records are bound to
the exact profile/catalog/Spec/build digests and require signed profile tests plus exact-print,
assembly, 100-cycle, and emergency-stop evidence for the strongest level.

Generated payloads use the existing content-addressed ObjectStore; the compile cache retains only
descriptors and reloads bytes only while packaging or downloading. With
`CHARACTER_ROBOT_STUDIO_ROOT` configured, a SQLite/WAL ProjectStore
atomically persists draft, immutable revisions, recent runs, and artifact manifests with an
optimistic generation guard. The browser session can reopen that project after a server restart,
and the descriptor index is rebuilt from verified manifest digests. CAD runs in a bounded child
process with wall-time, CPU, file-size, open-file, transport-digest, and optional memory limits.
The canonical Hardware Profile and its digest travel with every isolated job, so CAD, validation,
and maker artifacts cannot silently use different catalogs. Failed writes roll the live service
back to its last durable snapshot, while failed compile/validation run metadata is persisted when
storage remains available.
Recent compile, simulation, validation, and Build Pack runs expose their pinned versions, elapsed
time, cache status, and warning/error codes through Studio context.

The portable project JSON can be selected with **Replace with shared project…** (or posted to
`/api/studio/v1/project-import`) as an explicit human action. Import requires the currently viewed
project generation, installs the validated immutable revision chain into a fresh session
generation, and never trusts included artifacts. Rebuilding the imported revision must reproduce
the original artifact and manifest digests.

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
CHARACTER_ROBOT_STUDIO_ROOT=trueforge-data/character-studio \
  uv run uvicorn asset_autopsy.workbench:create_workbench_app --factory --host 127.0.0.1 --port 8713
```

Open `http://127.0.0.1:8713/studio` for Character Robot Studio, or
`http://127.0.0.1:8713/autopsy` for Asset Autopsy, in the Codex desktop browser. The visual editor remains usable when
WebMCP is unavailable and shows a compatibility banner in that case. During frontend development,
run `npm run dev` in `web/` alongside the Python server and open
`http://127.0.0.1:5173/studio` or `http://127.0.0.1:5173/autopsy`.

Omit `CHARACTER_ROBOT_STUDIO_ROOT` for disposable Studio sessions. With it set, the cookie is kept
for 30 days and points back to a private persisted project directory. **Reset session** deliberately
starts a new generation and removes the previous local generation.

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

After these checks, serve the built artifact with the Python workbench command above and run each
product's goal-only trial separately. Passing deterministic tests is not evidence that a model can
complete a goal-only run.

For Character Robot Studio, give Codex only a character goal and the user's size boundary. Do not
provide a tool order or geometry parameters. Confirm in `/studio` that:

1. all eight Studio site tools are available and the empty session has no revision or preset;
2. one request creates the first compiler-provided GLB within 60 seconds;
3. five semantic follow-ups remain on the same draft and preserve unrelated parts;
4. `inspect_design` shows four target-bound canonical views, and a selected 3D part is visible to
   Codex as shared session context;
5. `idle`, `greet`, `listen`, `think`, and `delight` visibly synchronize face, neck, and wheels;
6. switching from CoreS3 to Pi changes the digital component layout and enclosure calculation;
7. an over-limit change is rejected with measurements and a repair suggestion without changing
   the draft hash; and
8. after the human commits `r000`, Build Pack preparation exposes versioned digests but performs no
   automatic download or hardware write.

Record ten independent goal-only attempts before claiming the Showcase E2E criterion. This
repository does not treat the unit/integration suite or a single manual trial as that evidence.

For Asset Autopsy, give Codex the arm-design goal and protected boundaries, but not a diagnosis,
tool order, or patch value. Confirm in `/autopsy` that:

1. all eight Autopsy site tools are available and the initial revision is `r000`;
2. Codex chooses an experiment and trace query, and the resulting trace becomes visible;
3. the shared draft changes the selected joint's live preview before commit, then the revision and
   canonical change become visible;
4. the public child run shows a parent-to-child `BehaviorDiff` before qualification;
5. qualification displays `Qualified — editing locked` without exposing approval controls or a
   promotion-ticket digest; and
6. Reset returns the same visitor to a fresh editable `r000` with no draft, trace, task result, or
   qualification lock.

## Asset Autopsy current limits

This milestone intentionally supports one fixed simulated arm and local browser sessions. It does
not include deployment, durable accounts, arbitrary CAD or URDF import, multiple robots, FEA, real
hardware, asset-file materialization, Git-backed asset revisions, or export.

## Character Robot Studio current limits

The Maker Alpha/Multi-core Beta digital foundations and their gated completion path are implemented,
but the physical qualification milestones are not. A trusted deployment can inject a published,
digest-pinned runtime catalog, complete version-bound BOM/wiring/calibration instructions, measured
manufacturing probes, and signed profile/exact-build evidence; only that full conjunction can emit
a non-experimental `ready` pack. The checked-in M5 and Pi catalogs still lack measured
hole locations, selected motors/servos/battery/fasteners, complete cable geometry, loaded current
and voltage, thermal results, calibrated mass/CoG, published runtime binaries, and a reference
build. Consequently the BOM, wiring, assembly, calibration, CAD exports, and MuJoCo dynamics remain
experimental, `replication_ready` remains false, and both built-in profiles are capped at
`digital_checks_passed`.

MuJoCo remains a planning model even if total assembly mass is measured; qualifying dynamics still
requires versioned wheel geometry, mass distribution/inertia, actuator response, latency,
backlash, and friction data.

Durable local projects, restart recovery, human project import/export, deterministic Build Pack
regeneration, both runtime target contracts, and isolated CAD jobs are available. Cloud accounts,
public sharing/authentication, streaming distribution, catalog lifecycle management, and hosted
artifact retention remain Public Platform work. Completing Maker Alpha still requires measuring
the real parts, publishing and digest-pinning the CoreS3 runtime, printing and assembling one exact
revision without CAD/code edits, then passing the recorded power/thermal/motion/100-cycle/emergency
stop gates. Completing the blind-build Beta criterion still requires three other builders and a
physically qualified Pi reference build. A passing digital report must never be described as
manufacturable, safe, or physically verified.
