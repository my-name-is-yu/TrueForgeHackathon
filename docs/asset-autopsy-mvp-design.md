# Asset Autopsy

## MVP implementation design

Status: design frozen; Phase 0 integration gates remain

Date: 2026-08-29

Target: TrueForge Agent Harness Hackathon

Time box: 42 hours

Primary demo case: compound-arm-01

## 1. Decision

Asset Autopsy is not a new simulator and not a generic 3D validator.

It is a harness that lets a TrueForge agent close this loop without another human prompt:

~~~text
observe behavior
  -> state a falsifiable hypothesis
  -> run a discriminating experiment
  -> create one immutable revision
  -> compare behavior under the same conditions
  -> qualify against public and hidden contracts
  -> stop for human approval
  -> publish the repaired asset
~~~

The demo starts with a MuJoCo asset that is syntactically valid and renders correctly, but behaves incorrectly. Two independent authored parameters are wrong. The agent must discover and repair them from behavior, not from a fault label or a golden file.

The central product claim is deliberately narrow:

> Existing tools can operate a simulator. Asset Autopsy adds the missing causal loop from behavior to evidence-backed repair.

## 2. What the MVP proves

One prompt must be enough:

> Autopsy compound-arm-01. Do not change its controller or tests. Qualify and publish the repaired asset.

The run is successful only if all of the following are true:

- The original MJCF compiles and renders, but fails its behavior contract.
- The agent receives no root-cause hint and no human re-prompt.
- The agent repairs two different causes in at least two immutable revisions.
- Every patch cites a hypothesis and an earlier probe run.
- The final revision passes one public scenario and three hidden scenarios.
- Original asset, controller, contract, runner, and holdout commitment hashes remain unchanged.
- Replaying the same run produces metrics within the deterministic tolerance.
- The Evidence Ledger hash chain verifies.
- TrueForge genuinely pauses before publication.
- Denial creates no published artifact; approval publishes exactly the qualified revision.

This is a compound-fault case study with demonstrated depth 2. It is not yet a benchmark and should not be marketed as a general Autonomy Horizon measurement.

## 3. Explicit non-goals

The 42-hour version does not attempt:

- arbitrary 3D formats, arbitrary MJCF, URDF, USD, meshes, or textures;
- contact-rich manipulation, locomotion, cameras, learned controllers, or reinforcement learning;
- automatic generation of behavior contracts;
- a general physics root-cause oracle;
- agent-generated controller or test changes;
- a self-improving or dynamically rewritten harness;
- a standalone UI beyond TrueForge and generated evidence artifacts;
- multi-user authentication, remote attestation, or a distributed verifier;
- cryptographic secrecy against a hostile host administrator;
- modifying or forking the upstream MuJoCo MCP server;
- copying Nova source code or assets.

## 4. System boundary

~~~mermaid
flowchart LR
    U[Human: one prompt] --> TF[TrueForge 0.1.4]
    TF -->|7 domain tools over Streamable HTTP| AA[Asset Autopsy facade<br/>127.0.0.1:8712/mcp]
    AA -->|private stdio MCP child process| MM[MuJoCo MCP server<br/>pinned commit]
    MM --> MJ[MuJoCo 3.5.0<br/>CGL offscreen]

    AA --> DB[(SQLite evidence state)]
    AA --> OBJ[(content-addressed artifacts)]
    AA --> HC[public behavior contract]
    AA --> HO[private holdout verifier]

    TF -. no direct access .-> MM
    TF -. no target, seed, path, or golden XML .-> HO
    AA -->|publish only after TrueForge approval| OUT[repaired MJCF + manifest]
~~~

### Runtime ownership

| Concern | Owner | Why |
|---|---|---|
| Agent reasoning and loop choice | TrueForge agent | The harness must not answer the diagnosis |
| Approval UI and pause | TrueForge | This is the hackathon harness capability being demonstrated |
| Domain tool contract | Asset Autopsy | Converts generic simulation operations into a safe experimental protocol |
| Hypothesis preregistration | Asset Autopsy | Prevents post-hoc explanations |
| Immutable revisions and provenance | Asset Autopsy | Upstream model mutation would erase causal history |
| Contract metrics and BehaviorDiff | Asset Autopsy | Upstream comparison is not sufficiently controlled |
| Simulation, state stepping, and rendering | Existing MuJoCo MCP | This is mature generic execution machinery we should reuse |
| Physics | MuJoCo | No simulator is reimplemented |
| Hidden qualification | Asset Autopsy verifier | Agent cannot choose test inputs or inspect individual hidden traces |

## 5. Reuse versus new work

### Reused unchanged

| Component | Use in this project |
|---|---|
| TrueForge 0.1.4 | Model loop, tool calls, local sandbox, agent configuration, human approval |
| Model Context Protocol | Facade transport and private facade-to-engine transport |
| MuJoCo MCP server at ce9bed80ec3698d7b778230abc21f2228a3ce94b | Model loading, reset/state setup, deterministic stepping, model summary, traces, optional rendering |
| MuJoCo 3.5.0 | Physics compilation and simulation |
| SQLite | Local causal state and ledger index |
| Standard Python numeric/XML libraries | Metrics, canonicalization, hashing, and patch validation |

### Built specifically for Asset Autopsy

- Seven TrueForge-visible domain tools.
- A behavior-contract schema for reach-and-hold.
- Hypothesis, competing explanation, prediction, and falsifier preregistration.
- Two safe probe recipes: joint pulse and pose hold.
- Copy-on-write, single-attribute MJCF revisions.
- A strict adapter that normalizes unsafe or ambiguous upstream responses.
- Same-condition BehaviorDiff and contract qualification.
- Public plus committed hidden scenarios.
- Append-only Evidence Ledger with artifact hashes and a hash chain.
- Promotion tickets bound to the exact qualified revision.
- The compound-arm-01 fixture, evaluation suite, agent instruction, and demo report.

### Not copied

Nova is design inspiration for joining mature tools into a closed loop. Its repository has no top-level license in the inspected version, so Asset Autopsy copies no Nova code, prompts, assets, or documentation.

## 6. Dependency and license boundary

Known direct dependencies are permissive:

| Dependency | Pin | License | Required action |
|---|---|---|---|
| TrueForge | 0.1.4 | MIT | Preserve package license and attribution |
| MuJoCo MCP server | commit ce9bed80... | MIT | Keep the upstream LICENSE and pin visible in notices |
| MuJoCo | 3.5.0 | Apache-2.0 | Include Apache-2.0 license and required notices |
| Python MCP SDK | 1.26.0 for the verified MVP environment | MIT | Preserve license |

Primary references:

- https://github.com/truefoundry/trueforge
- https://github.com/Rongxuan-Zhou/mujoco-mcp-server/tree/ce9bed80ec3698d7b778230abc21f2228a3ce94b
- https://github.com/google-deepmind/mujoco
- https://github.com/modelcontextprotocol/python-sdk

The repository currently has no top-level LICENSE. Before submission, choose the project license; MIT is the recommended default because it is compatible with the direct dependencies and the intended reuse story.

Release gates:

1. Generate a complete dependency inventory automatically from the final lockfiles.
2. Generate THIRD_PARTY_NOTICES automatically, then review direct and transitive license texts.
3. Confirm no dependency or copied asset has an unknown or incompatible license.
4. Preserve upstream copyright notices.
5. Do not claim the license table is legal advice.

## 7. The fixed MVP fixture

compound-arm-01 is a self-created, primitive-only MJCF model:

- three hinge joints;
- position actuators;
- one named end-effector body/site;
- no external meshes, includes, textures, plugins, or network assets;
- one fixed controller family;
- one public target and initial pose;
- three hidden target/initial-pose pairs from the same task family.

The broken source contains two independent authored faults from different causal families:

1. one kinematic fault that changes the observed motion plane;
2. one dynamic fault that prevents stable convergence.

The exact joint, faulty values, repair values, hidden targets, and provisioning seed are not stored in agent-visible artifacts. The public repository may describe the fault families for reproducibility, but the demo claim is supported by an execution audit: the agent receives neither the private manifest nor repository/network access during the run.

The expected revision progression is:

| Revision | Reach clause | Stable-hold clause | Purpose |
|---|---:|---:|---|
| r000 | fail | fail | Compound broken source |
| r001, first cause corrected | pass | fail | Proves the first intervention changed the symptom |
| r002, second cause corrected | pass | pass | Final candidate |

The clean fixture is used offline to calibrate contract thresholds. It is removed from the demo runtime after calibration and is never exposed to the agent or used as a runtime diff target.

## 8. Behavior Contract v1

The public contract defines the intended behavior without naming the faulty fields.

~~~yaml
contract_id: reach-and-hold-v1
controller:
  id: compound-arm-position-v1
  sha256: "<fixed>"
public_scenarios:
  - id: public_center
task:
  reach_target: true
  hold_duration_s: 2.0
rules:
  - id: reach_error
    metric: hold_error_p95_m
    op: lte
    threshold: 0.03
  - id: stable_hold
    metric: joint_speed_rms_rad_s
    op: lte
    threshold: 0.05
  - id: settling
    metric: settling_time_s
    op: lte
    threshold: 2.0
  - id: finite_state
    metric: non_finite_count
    op: lte
    threshold: 0
  - id: joint_limits
    metric: joint_limit_violation_count
    op: lte
    threshold: 0
~~~

All rules are combined with AND. The numeric values above are initial targets, not assumptions: fixture calibration must prove that the clean model passes each threshold by at least 20% and the broken stage intended to fail is at least 20% outside its threshold.

The contract and controller are content-hashed before the case begins and are never editable through tools.

Metric definitions are fixed before agent execution:

- total simulation duration: 4.0 s;
- hold window: the final 2.0 s;
- hold_error_p95_m: p95 of Euclidean end-effector target error in the hold window;
- joint_speed_rms_rad_s: RMS across all three joint velocities and all hold-window samples;
- settled: target error and joint-speed bands both remain satisfied for every remaining sample;
- settling_time_s: the earliest settled sample, or null when settled is false;
- a null settling time always fails the settling clause.

## 9. Agent loop and enforced state transitions

Case and revision state are separate:

~~~mermaid
stateDiagram-v2
    state "Revision evaluation" as R {
      [*] --> Untested
      Untested --> PublicFail: run_task fails
      PublicFail --> PublicFail: hypothesis, probe, child revision
      Untested --> PublicPass: run_task passes
    }

    state "Case qualification" as Q {
      [*] --> QualificationUnused
      QualificationUnused --> QualificationRunning: verify public-pass head
      QualificationRunning --> Qualified: passes 3/3
      QualificationRunning --> QualificationFailed: completes below 3/3
      QualificationRunning --> QualificationRecovering: infrastructure interruption
      QualificationRecovering --> Qualified: exact attempt replay passes 3/3
      QualificationRecovering --> QualificationFailed: exact attempt replay completes below 3/3
    }

    state "Case lifecycle" as C {
      [*] --> Open
      Open --> Promoted: approved publish_revision executes
      Promoted --> [*]
    }
~~~

Approval waiting and denial are TrueForge session states, not Asset Autopsy database states. TrueForge stops before calling publish_revision, so the server remains QUALIFIED while the UI waits and observes nothing when the human denies.

Server-side preconditions:

- A revision needs a public baseline before it can be probed.
- A probe writes its hypothesis event before the engine is called.
- A revision cites exactly one completed probe run from the same base revision.
- The patch target must appear in the cited hypothesis or competing explanation.
- One revision changes exactly one XML attribute.
- MVP history is linear: only the current head revision can be patched.
- Public pass is required before holdout.
- Final holdout is available once per case; failure ends qualification for that case.
- Publication requires an unmodified promotion ticket.
- Publication seals the case.

## 10. TrueForge-visible tool surface

TrueForge sees exactly these seven tools. The generic MuJoCo MCP catalog is never registered with TrueForge.

### Shared rules

All input models reject unknown fields. All input numbers must be finite. IDs use strict server-defined patterns.

The MVP deliberately avoids a general distributed idempotency subsystem:

- open_case and inspect_asset are fresh reads;
- each completed task or probe is a distinct, budgeted run;
- an identical parent-plus-patch returns the existing revision;
- final qualification is one case-level attempt; an interrupted attempt may replay only the exact committed suite and revision;
- publication is reconciled from one fixed export manifest.

request_id is generated by the server for correlation; it is not supplied by the agent.

Budget accounting is event-derived:

- invalid schema, hash, or policy input consumes nothing;
- a completed task or probe consumes one run even when its domain result is fail or falsified;
- an upstream failure before physics begins consumes nothing; a partially executed probe is recorded failed and must be visibly retried;
- a policy-valid compile rejection consumes one patch attempt;
- returning an already-existing identical revision consumes nothing extra;
- final qualification is reserved immediately before the first hidden step;
- a completed pass or fail consumes it; an infrastructure interruption enters RECOVERING and may rerun only the same committed attempt without exposing partial results.

Common output:

~~~json
{
  "schema_version": "asset-autopsy/v1",
  "request_id": "req_...",
  "case_id": "case_compound_arm_01",
  "event_ids": ["evt_..."],
  "warnings": [],
  "artifacts": [
    {
      "artifact_id": "art_...",
      "kind": "trace_json",
      "uri": "autopsy://case_compound_arm_01/art_...",
      "media_type": "application/json",
      "sha256": "...",
      "bytes": 1234
    }
  ]
}
~~~

Absolute paths, raw hidden inputs, upstream slot names, traceback text, and free-form private summaries never appear in an agent-visible result. Human-readable messages use server-owned fixed templates.

### 10.1 open_case

Purpose: return the public contract, budget, revision summaries, topology summary, patch policy, and the last 20 ledger events for a pre-provisioned case.

Input:

~~~json
{"case_id":"case_compound_arm_01"}
~~~

Important output fields:

- promotion_state: open or promoted;
- qualification_state: unused, running, recovering, passed, or failed;
- original revision and asset hash;
- public scenarios and contract clauses;
- compiled dimensions and named joints/bodies/actuators;
- available probe kinds and their observable metric names;
- patch allowlist and numeric ranges;
- remaining run, probe, revision, and qualification budgets;
- linear revision history and an agent-safe ledger tail.

open_case does not create arbitrary cases and does not reveal a golden asset or fault manifest. event_tail is serialized through an event-type-specific PublicEventSummary; raw ledger payload JSON is never copied into a tool response. Patch ranges are broad fixture-family safety limits and are not derived from golden values.

### 10.2 inspect_asset

Purpose: inspect authored topology and editable values on one immutable revision.

Input:

~~~json
{
  "case_id":"case_compound_arm_01",
  "revision_id":"r000",
  "view":"both"
}
~~~

Output includes normalized authored values for joint axis, damping, armature, frictionloss, ranges, body parents, actuator-to-joint mapping, compiled nq/nv/nu, and timestep.

It never labels a field as faulty, returns a correct value, or recommends a patch.

### 10.3 run_task

Purpose: run a fixed public scenario and return contract observations.

Input:

~~~json
{
  "case_id":"case_compound_arm_01",
  "revision_id":"r000",
  "scenario_id":"public_center",
  "capture":"metrics_and_filmstrip"
}
~~~

Output includes:

- pass or fail as a normal domain outcome;
- rule-by-rule observed values;
- final and p95 target error;
- hold velocity RMS;
- settling time;
- peak energy and joint-limit/non-finite counts;
- at most 51 uniformly sampled trace points in the normal response;
- at most four selected images when rendering is available;
- automatic same-condition BehaviorDiff against the direct parent when one exists.

The agent cannot choose the target, controller, seed, timestep, or initial state.

### 10.4 run_probe

Purpose: atomically preregister a causal hypothesis and then execute one discriminating experiment.

Input shape:

~~~json
{
  "case_id":"case_compound_arm_01",
  "revision_id":"r000",
  "hypothesis":{
    "claim":"The elbow motion plane conflicts with the task plane.",
    "suspected_elements":[{"kind":"joint","name":"elbow","attributes":["axis"]}],
    "competing_explanation":{
      "claim":"The elbow actuator mapping is inverted.",
      "suspected_elements":[{"kind":"actuator","name":"elbow_motor","attributes":["joint"]}],
      "discriminating_reason":"A pulse direction separates geometric motion from controller sign."
    },
    "prediction":{
      "rationale":"The end effector should leave the intended XY plane.",
      "all_of":[{"metric":"abs_ee_dz_m","op":"gte","value":0.02}]
    },
    "falsifier":{
      "rationale":"Motion remains almost entirely in the intended plane.",
      "any_of":[{"metric":"abs_ee_dz_m","op":"lte","value":0.003}]
    }
  },
  "probe":{
    "kind":"joint_pulse",
    "joint_name":"elbow",
    "direction":1,
    "amplitude_rad":0.15,
    "duration_s":0.3,
    "observe_body":"end_effector"
  },
  "capture":"analysis_trace"
}
~~~

Supported recipes:

- joint_pulse: fixed initial state, a bounded control segment, then a recovery segment;
- pose_hold: a ringdown experiment from a fixed offset pose into the public hold target, returning peak times, peak amplitudes, decay ratio, oscillation period, and the velocity envelope.

The server first commits HYPOTHESIS_RECORDED, then calls the engine. If execution fails, the preregistration remains and a PROBE_FAILED event follows.

Output reports only observations and whether the agent's own predicates matched:

- prediction_matched;
- falsifier_triggered;
- inconclusive;
- conflicting.

It never returns hypothesis_supported, axis_error_deg, damping_too_low, or another diagnosed cause.

With capture=analysis_trace, the response contains 256 uniformly sampled rows of time, qpos, qvel, known control, and end-effector XYZ. TrueForge Large Tool Response stores the exact JSON in its sandbox tool-results directory; the agent can analyze that file with Python after the direct tool call. The facade still computes fixed contract metrics for enforcement, but causal interpretation remains with the agent.

### 10.5 create_revision

Purpose: create a copy-on-write child revision with exactly one permitted attribute change.

Input shape:

~~~jsonc
{
  "case_id":"case_compound_arm_01",
  "base_revision_id":"r000",
  "expected_base_sha256":"...",
  "basis_hypothesis_id":"hyp_...",
  "basis_probe_run_id":"run_probe_001",
  "patch":{
    "target":{"kind":"joint","name":"<observed joint>"},
    "attribute":"<one allowed attribute>",
    "expected_old_value":"<exact authored value>",
    "new_value":"<agent-proposed value>"
  },
  "rationale":"<why the cited observation discriminates this change>",
  "expected_effect":{
    "scenario_id":"public_center",
    "predicates":[{"metric":"hold_error_p95_m","op":"lte","value":0.03}]
  }
}
~~~

The MVP patch allowlist is:

- joint.axis, unit vector only;
- joint.damping;
- joint.armature;
- joint.frictionloss.

Numeric ranges are broad family-level safety policy, not agent input and not optimization guidance. The patch field is a single object, never an array.

Creation sequence:

1. Verify that the base is the current head, its hash matches, and the cited probe completed on it.
2. Verify that the patch target and attribute were named in the cited hypothesis or competing explanation.
3. Parse the stored base artifact.
4. Apply one typed attribute patch to a temporary copy.
5. Reparse and prove no undeclared XML difference exists.
6. Validate and compile in a fresh upstream slot.
7. Store the XML and patch manifest by content hash.
8. Commit revision and ledger event in one SQLite transaction.

Schema, allowlist, hash, or expected-old-value violations are typed tool errors and consume no attempt. A policy-valid candidate that MuJoCo cannot compile is a recorded rejected domain outcome and consumes one patch attempt.

### 10.6 verify_revision

Purpose: independently rerun public tests, then consume the one final hidden qualification attempt for the case.

Input:

~~~json
{
  "case_id":"case_compound_arm_01",
  "revision_id":"r002",
  "expected_asset_sha256":"..."
}
~~~

On success it returns:

- integrity checks for original, controller, contract, runner, and lineage;
- public pass count;
- hidden aggregate pass count and violated clause IDs only;
- no hidden mean, worst value, target, seed, control, scenario ID, or trace;
- a server-recorded, digest-bound promotion ticket containing a human-readable canonical diff, public/holdout counts, exact revision hash, fixed export name, and qualified-core hash.

The holdout is three scenarios and requires 3/3. Qualification is one case-level attempt. RUNNING commits the attempt ID, revision, suite commitment, and exact scenario hashes before execution. No partial result is agent-visible. An infrastructure interruption changes it to RECOVERING and deterministically reruns all three scenarios under the same attempt; partial private artifacts are discarded or remain linked only to that attempt. A repeated call after a terminal result returns the stored result; any attempt to qualify another revision is rejected. A completed failure creates no ticket and ends qualification for that case.

### 10.7 publish_revision

Purpose: publish only the exact qualified revision. This is the only TrueForge approval tool.

Input:

~~~json
{
  "case_id":"case_compound_arm_01",
  "promotion_ticket":{
    "ticket_id":"evt_qualification_passed_...",
    "case_id":"case_compound_arm_01",
    "revision_id":"r002",
    "asset_sha256":"...",
    "canonical_diff":[{"target":"joint_b","attribute":"...","before":"...","after":"..."}],
    "public_result":{"passed":1,"total":1},
    "holdout_result":{"passed":3,"total":3},
    "export_name":"compound-arm-01-repaired",
    "qualified_core_sha256":"...",
    "ticket_digest":"..."
  }
}
~~~

The complete ticket is intentionally present in the tool arguments so the TrueForge approval UI shows what will be published. The server compares every field against the stored QUALIFICATION_PASSED event; agent-supplied display data is never trusted. The agent cannot provide a destination path or overwrite flag.

Output is published or already_published and contains content hashes for:

- repaired MJCF;
- patch manifest;
- evidence-ledger export;
- qualification report.

## 11. TrueForge approval and annotations

The configured tool allowlist and approval policy are exact:

~~~json
{
  "model":{
    "name":"<configured model>",
    "params":{"parallel_tool_calls":false}
  },
  "mcp_servers":[{
    "name":"asset-autopsy",
    "enable_tools":[
      "open_case",
      "inspect_asset",
      "run_task",
      "run_probe",
      "create_revision",
      "verify_revision",
      "publish_revision"
    ],
    "require_approval_for_tools":["publish_revision"],
    "preload":true
  }],
  "config":{
    "iteration_limit":30,
    "sandbox":{"enabled":true,"file_downloads":true},
    "context_management":{"large_tool_response":{"enabled":true}}
  }
}
~~~

Tool annotations must describe real side effects:

| Tool | readOnly | destructive | Idempotent |
|---|---:|---:|---:|
| open_case | true | false | true |
| inspect_asset | true | false | true |
| run_task | false | false | false |
| run_probe | false | false | false |
| create_revision | false | false | true |
| verify_revision | false | false | true |
| publish_revision | false | true | true |

Runs and probes are additive and safe, but they consume budgets and append evidence, so they are not mislabeled read-only.

Approval semantics:

- Before approval: no export exists.
- Deny: TrueForge does not call the server, and no export exists.
- Approve: the server validates the full ticket and performs a reconcilable atomic publication.
- Uncertain response: the fixed export manifest and promotion event determine the recorded result.
- The agent instruction forbids repeatedly requesting the same denied promotion.

## 12. Private MuJoCo MCP integration

The Asset Autopsy process owns one stdio child process:

~~~text
MUJOCO_GL=cgl
MUJOCO_MCP_MAX_WORKERS=1
MUJOCO_MCP_RENDER_WIDTH=640
MUJOCO_MCP_RENDER_HEIGHT=480
python -m mujoco_mcp --transport stdio
~~~

The locked mujoco-mcp console entrypoint is equivalent. The module form is preferred because it uses the facade's exact Python environment without PATH ambiguity. Environment variables must be set before import because MuJoCo selects the GL backend at import time.

The child has no TCP port. The facade binds only to 127.0.0.1. Streamable HTTP requests require an ignored-environment bearer token; requests with an Origin header are accepted only from the configured TrueForge localhost origin. Secrets unrelated to physics, including model-provider and Linear credentials, are removed from the child environment.

The facade verifies at startup:

1. Locked dependency and upstream commit.
2. MCP initialize and required upstream tool schemas.
3. CGL snapshot smoke test.
4. Fixture, controller, and contract hashes.
5. Deterministic replay of a tiny fixture.

### Upstream tools used

| Facade need | Upstream operation |
|---|---|
| Parse/compile candidate | validate_mjcf |
| Fresh immutable slot | sim_load with xml_string |
| Fixed initial condition | sim_reset, sim_set_state |
| Deterministic fixed-step segment | run_and_analyze |
| Authored model summary | model_summary |
| Optional visual evidence | render_snapshot or sparse run keyframes |

server_diagnostics is an installation-time diagnostic, not a demo startup requirement: on macOS it may spend roughly 30 seconds probing irrelevant Linux backends. The actual CGL snapshot is authoritative.

The runner is an explicit constant-segment orchestrator:

~~~text
fresh load -> reset -> set state
  -> run_and_analyze(ctrl=A, n_steps=N)
  -> run_and_analyze(ctrl=B, n_steps=M)
  -> concatenate traces
  -> attach the known, content-hashed control value to every sample
~~~

reach-and-hold uses one constant position setpoint. joint_pulse uses pulse then neutral recovery. pose_hold/ringdown uses a fixed offset initial state followed by one constant hold target. Arbitrary control schedules and wall-clock controller helpers are outside the MVP.

The facade rejects over-limit step counts before calling upstream and verifies that every returned segment length equals the requested value; it never relies on upstream's silent clamp.

### Upstream tools deliberately not used

- modify_model or reload_from_xml;
- diagnose_instability;
- compare_trajectories;
- run_sweep and batch workers;
- model/file export;
- Menagerie download;
- viewer, vision, RL, optimization, or media tools;
- any input that accepts an agent-controlled file path or URL.

Every revision is passed upstream as xml_string and loaded into an opaque slot such as aa_<case-hash>_r002. Slot names are never returned to the agent.

### Required response adapter

The pinned upstream safe_tool sometimes reports exceptions as successful TextContent containing an error JSON object and can include traceback fragments. Every call therefore passes through:

~~~text
MCP result
  -> collect expected content types
  -> parse JSON
  -> validate the exact expected schema
  -> detect root-level error even when MCP says success
  -> remove traceback, host paths, raw XML, and private values
  -> map to a typed Asset Autopsy error
~~~

Unexpected content or schema drift fails closed as UPSTREAM_BAD_RESPONSE.

An upstream error, timeout, or unexpected response poisons the affected slot because simulation state may have advanced before the error was wrapped. That slot is never reused; it is reloaded from immutable XML. A render hang or broken stdio session restarts the whole child. Numeric and render calls have separate timeouts.

## 13. Deterministic runner and BehaviorDiff

The upstream compare_trajectories tool is not a qualification oracle. In the pinned implementation it compares qpos by index, truncates silently to the shorter series, and does not prove equal controller, seed, timestep, or initial state.

Asset Autopsy computes its own comparison from raw run_and_analyze traces.

### Run identity

~~~text
condition_hash = SHA256(
  contract_sha
  + controller_sha
  + scenario_sha
  + seed
  + initial_qpos_qvel
  + control_schedule_sha
  + timestep
  + step_count
  + MuJoCo_version
  + MuJoCo_MCP_commit
  + runner_version
)

execution_fingerprint = SHA256(condition_hash + asset_sha)
~~~

scenario_sha commits the exact target, initial state, and control for that run; hidden runs keep it private. It is distinct from the public holdout-suite commitment. Two runs are not comparable unless condition_hash is identical.

### Recorded signals

- end-effector XYZ;
- qpos and qvel;
- control vector;
- potential and kinetic energy;
- target error;
- finite-state and joint-limit flags.

### First divergence

The first divergent step is the earliest point where any threshold holds for three consecutive steps:

- end-effector position difference greater than 1e-4 m;
- qpos difference greater than 1e-4 rad;
- qvel difference greater than 1e-3 rad/s.

BehaviorDiff reports:

- first divergent step, time, signal, and magnitude;
- metric deltas;
- contract clauses that improved, regressed, or stayed unchanged;
- verdict: regressed, changed, improved, or public_pass.

QUALIFIED is reserved for the hidden verifier. BehaviorDiff does not report a causal diagnosis.

### Determinism policy

- Pin MuJoCo, MCP, upstream commit, Python, and architecture.
- Evaluate on the same Mac.
- Fresh load, reset, state set, and forward before every run.
- Never resume from a partially simulated state.
- Use fixed timestep and fixed step count.
- Use PCG64 with stored seeds for harness-side generation.
- Never use wall clock inside the controller.
- Never use rendered pixels for qualification.
- Replay key fixtures and require metric delta at or below 1e-8 before the demo.

The claim is deterministic within the pinned local environment, not bit-identical across operating systems or MuJoCo versions.

## 14. Evidence and storage model

SQLite records causal relationships; deterministic code decides numbers; the TrueForge agent interprets meaning.

~~~text
.asset-autopsy/
  ledger.sqlite
  objects/
    sha256/
      ab/
        abcdef...
  exports/
    case_compound_arm_01/
      repaired.xml
      manifest.json
      evidence-ledger.jsonl
      qualification.json
~~~

Large XML, trace, metric, image, and report artifacts live in the hash-named object directory, not SQLite. Artifact writes use a temporary file, fsync, SHA-256 verification, and atomic rename. The MVP has no generic artifact metadata table or garbage collector; event payloads carry the needed hash, kind, size, and media type.

### Minimal tables

~~~sql
cases(
  case_id PRIMARY KEY,
  root_revision_id,
  head_revision_id,
  qualification_revision_id,
  qualification_attempt_id,
  qualification_result,
  promoted_revision_id,
  source_asset_sha256,
  public_contract_sha256,
  controller_sha256,
  runner_sha256,
  holdout_commitment_sha256,
  created_at
)

revisions(
  case_id,
  revision_id,
  parent_revision_id,
  ordinal,
  asset_sha256,
  patch_manifest_sha256,
  hypothesis_event_id,
  probe_run_id,
  created_at,
  PRIMARY KEY(case_id, revision_id),
  UNIQUE(case_id, ordinal)
)

runs(
  run_id PRIMARY KEY,
  case_id,
  revision_id,
  run_kind,
  probe_kind,
  condition_hash,
  execution_fingerprint,
  trace_sha256,
  metrics_sha256,
  passed,
  created_at
)

ledger_events(
  seq INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id UNIQUE,
  request_id,
  case_id,
  revision_id,
  event_type,
  payload_json,
  artifact_refs_json,
  prev_hash,
  event_hash,
  created_at
)
~~~

Only the Asset Autopsy service writes these four tables. The MVP does not build database immutability triggers. Revisions, runs, and ledger events are append-only by service invariant; cases may advance head, qualification, and promoted fields in the same transaction as their ledger event.

The event chain is:

~~~text
event_hash = SHA256(bytes.fromhex(prev_hash) || canonical_json_bytes(event_without_hash))
~~~

This detects later event mutation. It is not claimed to resist a host administrator who can replace the database and all commitments.

The runtime database is the source of truth. evidence-ledger.jsonl is generated once for the approved publication so the MVP does not maintain two competing live ledgers.

## 15. Hidden holdout and anti-cheat boundary

At case creation, the verifier commits:

~~~text
SHA256(canonical_hidden_manifest + random_nonce)
~~~

The agent sees only the commitment. After publication, the human report may reveal the manifest and nonce so the commitment can be checked.

MVP controls:

- TrueForge registers only the facade.
- Upstream is an unaddressable stdio child.
- The TrueForge sandbox starts in a fresh workspace with no repository checkout.
- Demo agent configuration enables no web/search MCP and no host-file tool.
- Agent tools accept no arbitrary path, URL, XML, target, controller, or seed.
- Fixture provisioning validates the self-created MJCF subset and rejects DOCTYPE, ENTITY, include, external mesh, plugin, and external references.
- Patches are allowlisted and verified by a canonical whole-document diff.
- Controller, contract, runner, source, and holdout commitment hashes are checked before qualification.
- Qualification always reloads the revision from the artifact hash.
- Hidden scenarios are aggregate-only and once per case.
- Publication accepts only a stored promotion ticket and a server-owned output name.
- Facade HTTP validates localhost Origin and a secret bearer token.
- One global engine lock and parallel_tool_calls=false serialize physics.
- Step count, trace size, render count, revisions, probes, and wall time are bounded.

Before every recorded E2E, a sandbox probe must show that the design document, server source, clean fixture, holdout manifest, and host checkout are unreadable. Outbound network behavior is measured too. If the selected sandbox cannot disable network, the exact fixture seed and private manifest remain uncommitted until after recording, and the command audit must show no network access.

Because this is a local hackathon process, holdout privacy means withheld from the agent's observed execution surface, not protected from the machine owner. A production verifier moves the hidden suite into a separate service.

## 16. Error and recovery contract

### Domain outcomes, not tool errors

- A task contract fails.
- A prediction does not match.
- A falsifier triggers.
- A policy-valid candidate fails MuJoCo compilation.
- A holdout fails.
- Rendering is unavailable while numeric evidence succeeds.

### Typed tool errors

- CASE_NOT_FOUND
- REVISION_NOT_FOUND
- BASELINE_REQUIRED
- EVIDENCE_REQUIRED
- REVISION_HASH_MISMATCH
- PATCH_NOT_ALLOWED
- BUDGET_EXHAUSTED
- PREDICATE_INVALID
- QUALIFICATION_REQUIRED
- QUALIFICATION_ALREADY_USED
- TICKET_MISMATCH
- CASE_SEALED
- UPSTREAM_UNAVAILABLE
- UPSTREAM_BAD_RESPONSE
- PUBLISH_CONFLICT

Errors use MCP isError=true with a fixed JSON envelope containing code, safe message, retryable flag, request ID, and bounded next action. They never contain stack traces, host paths, raw XML, secrets, or private scenario values.

### Recovery

| Failure | Behavior |
|---|---|
| CGL initialization or rendering fails | Restart child once with MUJOCO_MCP_NO_RENDER=1; keep the numeric loop and mark images unavailable |
| stdio dies during a run | Mark the slot poisoned, restart/reload, return a retryable error; a new agent call becomes a visibly separate run |
| revision compile fails | Record rejection; do not create a revision |
| child dies after revision commit | Rehydrate its slot from the artifact on next use |
| qualification child dies mid-suite | Keep partial data private, mark the committed attempt RECOVERING, and replay the exact revision and suite without creating a new attempt |
| qualification response is lost | Return the already-stored terminal result for that case and revision |
| publish response is uncertain | Reconcile the fixed export manifest and promotion event; never blindly rewrite files |
| upstream schema differs at startup | Fail fast; do not silently substitute another tool |
| TrueForge approval does not pause | Demo kill gate; do not fake approval |

### Crash-safe publication

verify_revision prepares and hashes only the qualified core:

- repaired.xml;
- patch-manifest.json;
- qualification.json.

QUALIFICATION_PASSED stores this qualified_core_sha256. It does not hash a ledger that contains itself.

After approval:

1. Materialize the qualified core under exports/.tmp-<ticket>.
2. Generate ledger-through-qualification.jsonl, ending at QUALIFICATION_PASSED.
3. Generate manifest.json listing the core hash and every exported file hash.
4. Verify and fsync files and directory.
5. Atomically rename the directory to the fixed case export path on the same filesystem.
6. Commit PROMOTED with manifest_sha256 to SQLite.
7. On startup, reconcile an export whose valid manifest exists but whose event was interrupted.

The exported ledger intentionally ends at qualification; the later PROMOTED receipt remains in SQLite and the TrueForge session trace. This avoids a bundle/ledger hash cycle. It is a locally reconcilable publication protocol, not a claim of general exactly-once effects.

## 17. Planned repository shape

Only the design file is created at this stage. The implementation should converge on:

~~~text
docs/
  asset-autopsy-mvp-design.md
pyproject.toml
uv.lock
THIRD_PARTY_NOTICES.md
src/asset_autopsy/
  server.py          # MCP facade and annotations
  schemas.py         # strict input/output models
  service.py         # state transitions and use cases
  qualification.py   # one-shot hidden verifier
  publisher.py       # ticket-bound atomic publication
  mujoco_client.py   # stdio child lifecycle and response normalization
  runner.py          # fixed-step scenarios and probes
  metrics.py         # contract metrics and BehaviorDiff
  storage.py         # SQLite, hash-named artifacts, hash chain
  patcher.py         # safe MJCF parsing and one-attribute COW patches
fixtures/compound-arm-01/
  public/
    contract.yaml
    controller.json
  authoring/
    template.xml
skills/asset-autopsy/
  SKILL.md
scripts/
  serve
  provision-demo     # creates broken/private runtime data outside sandbox
  reset-demo
  verify-ledger
tests/
  unit/
  upstream_contract/
  fixture/
  facade/
  e2e/
~~~

provision-demo writes the concrete broken asset, private manifest, nonce, and holdout under a configured ignored server-runtime directory outside the sandbox workspace. The clean calibration asset is not present in the demo runtime.

## 18. 42-hour implementation plan

### Phase 0 — prove the risky seams, 3 hours

Build throwaway spikes, then keep only the proven path:

1. TrueForge calls one facade tool over localhost Streamable HTTP.
2. The saved/resolved AgentSpec preserves serial calls, seven tools, and exact publish approval.
3. Facade initializes and lists tools from the pinned upstream over stdio.
4. CGL renders a 160x120 primitive model from the child process.
5. Facade detects an upstream success-wrapped error.
6. A 256-row tool result is offloaded and analyzed from TrueForge's sandbox tool-results file.
7. The sandbox cannot read the checkout/private runtime; outbound network behavior is recorded.
8. Bearer header and Origin checks work from TrueForge.
9. TrueForge exact approval pauses before a dummy publish.

Kill gate: if any transport or approval seam is unproven after three hours, stop feature work and repair the seam. Do not build the domain layer on an assumed integration.

### Phase 1 — vertical physics slice, 6 hours

- Create clean and compound-broken primitive arm.
- Implement fixed public and three hidden scenarios.
- Calibrate thresholds with 20% margins.
- Prove r000 fail/fail, first-fix pass/fail, final pass/pass through the pinned upstream.
- Prove one same-run numeric replay tolerance.
- Open a draft PR and start Qodo review before the architecture grows.

Kill gate: if the staged symptom progression is not stable, simplify fixture physics before adding agent logic.

### Phase 2 — facade and causal domain, 9 hours

- Child lifecycle, sanitized environment, schema preflight.
- Constant-segment runner, response normalization, and poisoned-slot handling.
- joint_pulse and pose_hold/ringdown.
- Strict schemas for seven tools.
- Four SQLite tables, hash-named artifacts, and event hash chain.
- Hypothesis-before-probe transaction ordering.
- Linear one-attribute revision patcher and canonical diff.
- One-shot hidden qualification and digest-bound promotion ticket.

### Phase 3 — TrueForge agent and full loop, 8 hours

- Register only the seven facade tools.
- Configure exact publish approval and serial tool calls.
- Write the Asset Autopsy agent instruction.
- Make the agent analyze the offloaded trace rather than accepting a diagnosis label.
- Run r000 -> r001 -> r002 -> qualification -> approval.
- Remove any root-cause leakage found in outputs.

Scope fallback: if the agent cannot reliably solve two faults, ship one fault only while preserving hypothesis, probe, immutable revision, holdout, and real approval. Do not replace the loop with scripted diagnosis.

### Phase 4 — critical hardening and review, 4 hours

- Test evidence-before-probe, one-change patching, same-condition rejection, one-shot holdout, forged ticket, and atomic publish.
- Address valid Qodo findings.
- Exercise numeric-only rendering fallback and one poisoned-slot recovery.
- Freeze the agent-visible schemas and prompt.

### Phase 5 — submission, evidence, and video, 8 hours

- Complete one uncut E2E and one independent numeric replay; additional full agent runs are stretch.
- Record tool calls, revisions, probes, elapsed time, and human re-prompts.
- Generate before/after filmstrip and ledger report.
- Create license notices and public README.
- Record the full run, edit the three-minute submission, and include the uncut run link.

### Buffer — 4 hours

Reserved for integration regressions, CGL, approval behavior, Qodo, and demo recording. Total: 42 hours.

## 19. Test matrix

### Unit gates

- Contract threshold boundaries.
- p95 hold error, RMS velocity, settling time, and first divergence.
- Predicate catalog and finite-number validation.
- One-attribute patch and expected-old-value guard.
- Axis normalization and numeric ranges.
- DOCTYPE, ENTITY, include, external reference, and traversal rejection.
- Canonical XML diff catches undeclared edits.
- Hash-chain verification.
- Atomic artifact and revision commit.
- Promotion-ticket mismatch.

### Pinned upstream contract gates

- initialize and required tool list/schema;
- validate_mjcf with xml_string;
- sim_load into separate slots;
- sim_reset and sim_set_state;
- run_and_analyze signal shape;
- render snapshot when CGL is available;
- upstream error normalization;
- clean shutdown and CGL cleanup.

There is no need to retest all 65 upstream tools.

### Fixture gates

- clean asset passes public and hidden;
- r000 fails reach and hold;
- r001 passes reach but fails hold;
- r002 passes public and hidden 3/3;
- each phase changes the expected observation;
- the same numeric scenario replayed twice stays within tolerance;
- controller, contract, source, and runner hashes remain fixed.

### Facade and approval gates

- list_tools exposes exactly seven tools;
- generic tools such as modify_model are unreachable;
- arbitrary paths and XML are impossible in public schemas;
- probe without a baseline is rejected;
- the hypothesis event is stored before the upstream probe call;
- a two-attribute patch is schema-invalid;
- a forged ticket cannot publish;
- approval pending and denial leave exports empty;
- approval exposes the full human-readable ticket;
- crash/retry around export rename reconciles one valid bundle;
- sandbox cannot read the checkout or private runtime;
- the analysis trace reaches the sandbox tool-results file.

### Agent E2E gates

Required: one fresh complete case with the fixed model, prompt, and limits:

- zero human re-prompts;
- two hypothesis/probe/patch cycles;
- no controller or contract edit;
- public and hidden pass;
- actual approval pause;
- completed ledger and matching export hash.

Stretch: repeat two more fresh cases. Even three runs are reliability evidence for a case study, not a statistical benchmark.

## 20. Three-minute demo

The three-minute submission is an edited version of one genuine uncut run. Waiting and model latency may be cut or accelerated, but event order and ledger timestamps remain visible; the approval pause is shown at normal speed. The public README links the full uncut session. The claim is one-prompt autonomy, not a three-minute live runtime.

### 0:00–0:15 — the asymmetry

Show the broken motion.

> This robot is valid. It loads. But it lies.

### 0:15–0:30 — the reuse story

Show the architecture.

> TrueForge, MuJoCo MCP, and MuJoCo already existed. We built the behavioral autopsy loop between them.

### 0:30–0:40 — one prompt

Issue the single autopsy prompt.

### 0:40–1:15 — first causal experiment

Show baseline failure, the axis hypothesis with alternative and falsifier, the joint pulse, and the observed motion plane. Create r001.

### 1:15–1:50 — symptom changes, second experiment

Show that reach improves while hold remains unstable. Preregister the damping hypothesis, run pose hold, and create r002.

### 1:50–2:10 — independent qualification

Show public pass, hidden 3/3, and invariant hashes.

### 2:10–2:28 — real human boundary

Call publish_revision and visibly wait at the TrueForge approval UI. Approve.

### 2:28–2:50 — recovered artifact

Show before/after motion, the two-line revision lineage, contract deltas, and Evidence Ledger.

### 2:50–3:00 — claim

> Simulator tools already existed. Asset Autopsy closes the missing loop from behavior, to hypothesis, to experiment, to trustworthy repair.

## 21. Path to the complete product

The MVP is a vertical proof, not a finished general asset doctor.

### V1 — reusable MJCF autopsy

- fixture registration and contract authoring;
- more joint, actuator, sensor, and contact probes;
- safe multi-attribute repair plans composed of one-change revisions;
- multiple asset cases and a real benchmark;
- report viewer and revision comparison.

### V2 — multi-format asset autopsy

- URDF and USD/SimReady import adapters;
- meshes, inertia, collision, contacts, sensors, and actuator semantics;
- isolated verifier service and cryptographically signed promotion;
- contract templates for arms, grippers, mobile bases, and articulated props;
- CI gate for behavior, not only file validity.

### North-star product

Input:

- an unfamiliar simulation asset;
- a behavior contract;
- permitted repair boundaries.

Output:

- a repaired, qualified asset;
- an immutable causal revision chain;
- experiments that distinguish competing explanations;
- a replayable evidence bundle;
- an explicit human promotion decision.

The long-term moat is not a larger tool catalog. It is a trustworthy experimental protocol that lets an agent keep looping until an asset's behavior, not merely its syntax, is understood.

## 22. Frozen ADRs and remaining spikes

### Frozen for MVP

- One primitive 3-DOF arm and two faults.
- MJCF only.
- Generic MuJoCo MCP reused unchanged and hidden behind stdio.
- Seven public domain tools.
- Hypothesis folded atomically into run_probe; inspect_asset remains a separate tool.
- One attribute per immutable revision.
- Linear revision history in the MVP.
- Asset Autopsy owns BehaviorDiff and qualification.
- One public scenario plus a one-shot, three-scenario hidden suite.
- Four SQLite tables plus hash-named artifacts.
- Only publish_revision requires approval.
- Numeric evidence remains authoritative; rendering is optional.
- TrueForge Large Tool Response is the seven-tool path from raw traces into sandbox analysis.
- The generic MuJoCo MCP, repository checkout, and private fixture remain outside the agent execution surface.

### Phase 0 must answer empirically

1. Does the pinned Python MCP client/server combination preserve all required stdio content blocks and tool errors?
2. Does the resolved TrueForge AgentSpec preserve serial calls, sandbox settings, and approval only for publish_revision?
3. Does Large Tool Response place the exact 256-row trace where sandbox Python can analyze it?
4. Can the sandbox read the checkout, private runtime, or outbound network, and what isolation/evidence is required?
5. Do localhost bearer headers and Origin validation work with the saved TrueForge connection?
6. Can selected images pass through without exposing host paths or flooding model context?

These are implementation spikes, not product-design choices. No additional product decision is required before Phase 0 begins.
