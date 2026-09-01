import "./style.css";
import {
  acceptRevision,
  callTool,
  getContext,
  getTrace,
  rejectRevision,
  resetSession,
  type JsonObject,
} from "./api";
import { createRobotView } from "./robot";
import {
  bindTaskMetrics,
  type TaskMetricsState,
  type TaskResult,
} from "./task-metrics";
import { registerWebMcpTools } from "./webmcp";

type Joint = {
  name: string;
  axis: [number, number, number];
  damping: number;
  armature: number;
  frictionloss: number;
};

type CanonicalDiff = {
  target: string;
  attribute: string;
  before: string;
  after: string;
};

type ExperimentTraceSummary = {
  run_id: string;
  revision_id: string;
  asset_sha256: string;
  signals: string[];
  row_count: number;
  start_time_s: number;
  end_time_s: number;
};

type ExperimentTraceColumn =
  | { kind: "time" }
  | { kind: "qpos" | "qvel"; joint_name: string }
  | { kind: "energy"; component: "potential" | "kinetic" }
  | { kind: "contact_count" }
  | { kind: "body_position"; body_name: string; axis: "x" | "y" | "z" }
  | { kind: "control"; actuator_name: string };

type ExperimentTrace = {
  columns: ExperimentTraceColumn[];
  rows: { time_s: number; values: Record<string, number> }[];
};

type Context = JsonObject & {
  case: {
    case_id: string;
    qualification_state: string;
    remaining_budgets: Record<string, number>;
    revision_history: {
      revision_id: string;
      asset_sha256: string;
      parent_revision_id: string | null;
      canonical_diff: CanonicalDiff[];
    }[];
    event_tail: { kind: string; summary: string }[];
  };
  design: { joints: Joint[]; bodies: { name: string; parent: string | null }[]; actuators: { name: string; joint_name: string }[] };
  head_revision_id: string;
  head_asset_sha256: string;
  draft: { patch: { target: { name: string }; attribute: string; new_value: number | number[] } } | null;
  feedback: { feedback: string; revision_id: string; asset_sha256: string }[];
  experiment_traces: ExperimentTraceSummary[];
  latest_task: TaskResult | null;
  rejections: { feedback: string; revision_id: string; asset_sha256: string }[];
  editing_locked: boolean;
  accepted: boolean;
  accept_ticket_digest: string | null;
};

const app = document.querySelector<HTMLElement>("#app");
if (!app) throw new Error("Missing app root");

app.innerHTML = `
  <header>
    <div><p class="eyebrow">HUMAN × CODEX ENGINEERING LOOP</p><h1>Asset <i>Autopsy</i></h1></div>
    <div class="header-actions"><span id="webmcp-status" class="pill">Checking site tools…</span><button id="reset" class="ghost">Reset session</button></div>
  </header>
  <section id="compatibility" class="compatibility" hidden>Site tools are unavailable in this browser. The visual workbench still works; use the latest Codex desktop browser with GPT-5.6 Sol or Terra for direct agent control.</section>
  <div class="workspace">
    <section class="viewport panel">
      <div class="panel-title"><div><span>LIVE DESIGN</span><strong id="revision">—</strong></div><span id="lock-state" class="pill">Editable</span></div>
      <div id="robot-canvas"></div>
      <div class="view-footer"><span>Drag to orbit · Scroll to zoom</span><span id="hash">—</span></div>
    </section>
    <aside class="inspector panel">
      <div class="panel-title"><div><span>PHYSICAL ATTRIBUTES</span><strong>One-change draft</strong></div></div>
      <form id="draft-form">
        <label>Joint<select id="joint"></select></label>
        <label>Attribute<select id="attribute"><option>axis</option><option>damping</option><option>armature</option><option>frictionloss</option></select></label>
        <label>New value<input id="new-value" required autocomplete="off" /></label>
        <p id="current-value" class="fine">Current: —</p>
        <button class="primary" type="submit">Preview shared draft</button>
      </form>
      <div id="draft-card" class="draft-card empty">No uncommitted draft</div>
      <div class="divider"></div>
      <form id="feedback-form">
        <label>Design feedback<textarea id="feedback" placeholder="Feels too twitchy near the target…" required></textarea></label>
        <button class="ghost" type="submit">Attach to this revision</button>
      </form>
    </aside>
    <section class="evidence panel">
      <div class="panel-title"><div><span>EVIDENCE</span><strong>Hard requirements</strong></div><button id="run-task" class="ghost">Run public task</button></div>
      <div class="evidence-stack">
        <section class="evidence-block">
          <div class="evidence-heading"><span>PUBLIC TASK</span><strong id="behavior-verdict">No result</strong></div>
          <div id="metrics" class="metrics"><p class="empty-copy">Ask Codex to experiment, or run the fixed task here.</p></div>
          <div id="behavior-diff" class="behavior-diff"></div>
        </section>
        <section class="evidence-block">
          <div class="evidence-heading trace-heading">
            <div><span>LATEST EXPERIMENT TRACE</span><strong>Session evidence</strong></div>
            <div class="trace-selectors">
              <label>Run<select id="trace-run" aria-label="Experiment run"></select></label>
              <label>Signal<select id="trace-signal" aria-label="Trace signal"></select></label>
            </div>
          </div>
          <div id="trace-chart" class="trace-chart"><p class="empty-copy">No completed experiment trace yet.</p></div>
        </section>
        <section class="evidence-block">
          <div class="evidence-heading"><span>CURRENT REVISION DIFF</span><strong id="diff-revision">Original</strong></div>
          <div id="revision-diff" class="revision-diff"><p class="empty-copy">The original revision has no authored change.</p></div>
        </section>
      </div>
    </section>
    <section class="activity panel">
      <div class="panel-title"><div><span>SHARED CONTEXT</span><strong>Revision-bound activity</strong></div></div>
      <div id="activity-list"></div>
    </section>
  </div>
  <section id="accept-bar" class="accept-bar" hidden>
    <div><span>QUALIFICATION PASSED</span><strong>Accept, or add design feedback and restart from a fresh case.</strong></div>
    <div class="accept-actions"><button id="reject" class="danger">Reject &amp; restart</button><button id="accept" class="primary">Accept revision</button></div>
  </section>
  <div id="toast" role="status"></div>
`;

const canvas = document.querySelector<HTMLElement>("#robot-canvas")!;
const robot = createRobotView(canvas);
let context: Context;
let taskMetrics: TaskMetricsState | null = null;
let selectedTraceRunId: string | null = null;
let selectedTraceSignal: string | null = null;
let knownLatestTraceRunId: string | null = null;
let traceState: { runId: string; trace: ExperimentTrace } | null = null;
let traceError: string | null = null;
let refreshSequence = 0;

const $ = <T extends HTMLElement>(selector: string) => document.querySelector<T>(selector)!;
const escapeHtml = (value: unknown) => String(value)
  .replaceAll("&", "&amp;")
  .replaceAll("<", "&lt;")
  .replaceAll(">", "&gt;")
  .replaceAll('"', "&quot;")
  .replaceAll("'", "&#039;");
const toast = (message: string, error = false) => {
  const element = $("#toast");
  element.textContent = message;
  element.className = error ? "show error" : "show";
  window.setTimeout(() => { element.className = ""; }, 3800);
};
const formatNumber = (value: number | null): string => {
  if (value === null) return "—";
  if (value === 0) return "0";
  const magnitude = Math.abs(value);
  return magnitude >= 1000 || magnitude < 0.001
    ? value.toExponential(3)
    : value.toPrecision(4);
};

function selectedJoint(): Joint {
  return context.design.joints.find((item) => item.name === $<HTMLSelectElement>("#joint").value)!;
}

function updateCurrentValue(): void {
  if (!context) return;
  const joint = selectedJoint();
  const attribute = $<HTMLSelectElement>("#attribute").value as keyof Joint;
  const value = joint[attribute];
  $("#current-value").textContent = `Current: ${Array.isArray(value) ? value.join(" ") : value}`;
  $("#new-value").setAttribute("placeholder", attribute === "axis" ? "0 0 1" : String(value));
  robot.update({ joint: joint.name });
}

function renderTaskMetrics(): void {
  const verdict = $("#behavior-verdict");
  verdict.className = "";
  if (!taskMetrics) {
    verdict.textContent = "No result";
    $("#metrics").innerHTML = `<p class="empty-copy">Ask Codex to experiment, or run the fixed task here.</p>`;
    $("#behavior-diff").innerHTML = "";
    return;
  }

  $("#metrics").innerHTML = taskMetrics.observations.map((item) => `<article><span>${escapeHtml(item.metric.replaceAll("_", " "))}</span><strong>${formatNumber(item.value)}</strong></article>`).join("");
  const diff = taskMetrics.behaviorDiff;
  if (!diff) {
    verdict.textContent = `${taskMetrics.result.toUpperCase()} · baseline`;
    verdict.className = taskMetrics.result;
    $("#behavior-diff").innerHTML = `<p class="empty-copy">Run the public task on a child revision to compare it with its parent.</p>`;
    return;
  }

  const comparisonVerdict = diff.verdict.replaceAll("_", " ").toUpperCase();
  verdict.textContent = taskMetrics.result === "pass" && diff.verdict === "public_pass"
    ? comparisonVerdict
    : `${taskMetrics.result.toUpperCase()} · ${comparisonVerdict}`;
  verdict.className = taskMetrics.result;
  const divergence = diff.first_divergence
    ? `<p class="divergence">First divergence: <strong>${escapeHtml(diff.first_divergence.signal.replaceAll("_", " "))}</strong> at ${formatNumber(diff.first_divergence.time_s)}s · magnitude ${formatNumber(diff.first_divergence.magnitude)}</p>`
    : `<p class="divergence">No trace divergence above the fixed threshold.</p>`;
  const deltas = diff.metric_deltas.map((item) => {
    const delta = item.delta === null ? "—" : `${item.delta > 0 ? "+" : ""}${formatNumber(item.delta)}`;
    return `<article><span>${escapeHtml(item.metric.replaceAll("_", " "))}</span><div><code>${formatNumber(item.before)}</code><b>→</b><code>${formatNumber(item.after)}</code></div><em>Δ ${delta}</em></article>`;
  }).join("");
  const clauses = diff.clause_outcomes.map((item) => `<span class="clause ${item.outcome}">${escapeHtml(item.clause_id.replaceAll("_", " "))}: ${item.outcome}</span>`).join("");
  $("#behavior-diff").innerHTML = `${divergence}<div class="delta-grid">${deltas}</div><div class="clause-list">${clauses}</div>`;
}

function renderRevisionDiff(): void {
  const revision = context.case.revision_history.find((item) => item.revision_id === context.head_revision_id);
  $("#diff-revision").textContent = revision?.parent_revision_id
    ? `${revision.parent_revision_id} → ${revision.revision_id}`
    : "Original";
  const diffs = revision?.canonical_diff ?? [];
  $("#revision-diff").innerHTML = diffs.length
    ? diffs.map((item) => `<article><span>${escapeHtml(item.target)}.${escapeHtml(item.attribute)}</span><div><code>${escapeHtml(item.before)}</code><b>→</b><code>${escapeHtml(item.after)}</code></div></article>`).join("")
    : `<p class="empty-copy">The original revision has no authored change.</p>`;
}

function currentTraceSummary(): ExperimentTraceSummary | null {
  return context.experiment_traces.find((item) => item.run_id === selectedTraceRunId) ?? null;
}

function renderTraceChart(): void {
  const runSelect = $<HTMLSelectElement>("#trace-run");
  runSelect.innerHTML = context.experiment_traces.map((item) => `<option value="${escapeHtml(item.run_id)}">${escapeHtml(item.run_id)}</option>`).join("");
  runSelect.disabled = context.experiment_traces.length === 0;
  if (selectedTraceRunId) runSelect.value = selectedTraceRunId;

  const summary = currentTraceSummary();
  const signalSelect = $<HTMLSelectElement>("#trace-signal");
  if (!summary) {
    signalSelect.innerHTML = "";
    signalSelect.disabled = true;
    $("#trace-chart").innerHTML = `<p class="empty-copy">No completed experiment trace yet.</p>`;
    return;
  }

  if (!selectedTraceSignal || !summary.signals.includes(selectedTraceSignal)) {
    selectedTraceSignal = summary.signals[0] ?? null;
  }
  signalSelect.innerHTML = summary.signals.map((signal) => `<option value="${escapeHtml(signal)}">${escapeHtml(signal)}</option>`).join("");
  signalSelect.disabled = summary.signals.length === 0;
  if (selectedTraceSignal) signalSelect.value = selectedTraceSignal;

  if (traceError) {
    $("#trace-chart").innerHTML = `<p class="empty-copy error-copy">${escapeHtml(traceError)}</p>`;
    return;
  }
  if (traceState?.runId !== summary.run_id || !selectedTraceSignal) {
    $("#trace-chart").innerHTML = `<p class="empty-copy">Loading ${escapeHtml(summary.run_id)}…</p>`;
    return;
  }

  const rows = traceState.trace.rows;
  const values = rows.map((row) => row.values[selectedTraceSignal!]).filter((value): value is number => Number.isFinite(value));
  if (!values.length || values.length !== rows.length) {
    $("#trace-chart").innerHTML = `<p class="empty-copy error-copy">The selected signal is missing from this trace.</p>`;
    return;
  }
  const width = 720;
  const height = 190;
  const padding = 16;
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const range = maximum - minimum;
  const points = values.map((value, index) => {
    const x = padding + (index / Math.max(1, values.length - 1)) * (width - padding * 2);
    const y = range === 0
      ? height / 2
      : padding + ((maximum - value) / range) * (height - padding * 2);
    return `${x.toFixed(2)},${y.toFixed(2)}`;
  }).join(" ");
  $("#trace-chart").innerHTML = `
    <div class="trace-meta"><span>${escapeHtml(summary.run_id)} · ${escapeHtml(summary.revision_id)}@${escapeHtml(summary.asset_sha256.slice(0, 8))} · ${summary.row_count} rows · ${formatNumber(summary.start_time_s)}–${formatNumber(summary.end_time_s)}s</span><span>min ${formatNumber(minimum)} · max ${formatNumber(maximum)}</span></div>
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${escapeHtml(selectedTraceSignal)} trace">
      <line x1="${padding}" y1="${height / 2}" x2="${width - padding}" y2="${height / 2}"></line>
      <polyline points="${points}"></polyline>
    </svg>`;
}

async function loadSelectedTrace(sequence = refreshSequence): Promise<void> {
  const runId = selectedTraceRunId;
  traceState = null;
  traceError = null;
  renderTraceChart();
  if (!runId) return;
  try {
    const trace = await getTrace(runId) as ExperimentTrace;
    if (sequence !== refreshSequence || selectedTraceRunId !== runId) return;
    traceState = { runId, trace };
  } catch (error) {
    if (sequence !== refreshSequence || selectedTraceRunId !== runId) return;
    traceError = String(error);
  }
  renderTraceChart();
}

function render(next: Context): void {
  context = next;
  taskMetrics = context.latest_task?.revision_id === context.head_revision_id
    ? bindTaskMetrics(
      {
        revisionId: context.head_revision_id,
        assetSha256: context.head_asset_sha256,
      },
      context.latest_task,
    )
    : null;
  renderTaskMetrics();
  renderRevisionDiff();
  renderTraceChart();
  $("#revision").textContent = `${context.case.case_id} / ${context.head_revision_id}`;
  $("#hash").textContent = context.head_asset_sha256.slice(0, 12);
  $("#lock-state").textContent = context.accepted ? "Accepted" : context.editing_locked ? "Locked" : "Editable";
  $("#lock-state").classList.toggle("locked", context.editing_locked);
  $("#draft-form").querySelectorAll("input, select, button").forEach((element) => {
    (element as HTMLInputElement).disabled = context.editing_locked;
  });

  const jointSelect = $("#joint") as HTMLSelectElement;
  const previousJoint = jointSelect.value;
  jointSelect.innerHTML = context.design.joints.map((joint) => `<option value="${escapeHtml(joint.name)}">${escapeHtml(joint.name)}</option>`).join("");
  if (context.design.joints.some((joint) => joint.name === previousJoint)) jointSelect.value = previousJoint;
  updateCurrentValue();

  const draftCard = $("#draft-card");
  if (context.draft) {
    const patch = context.draft.patch;
    draftCard.className = "draft-card";
    draftCard.innerHTML = `<span>UNCOMMITTED PREVIEW</span><strong>${escapeHtml(patch.target.name)}.${escapeHtml(patch.attribute)}</strong><code>${escapeHtml(JSON.stringify(patch.new_value))}</code><p>Visible to Codex. Not yet a revision.</p>`;
    robot.update({ joint: patch.target.name });
  } else {
    draftCard.className = "draft-card empty";
    draftCard.textContent = "No uncommitted draft";
  }

  const activities = [
    ...context.rejections.slice().reverse().map((item) => ({ kind: "HUMAN REJECTION", summary: `${item.revision_id}: ${item.feedback}` })),
    ...context.feedback.map((item) => ({ kind: "HUMAN FEEDBACK", summary: `${item.revision_id}: ${item.feedback}` })),
    ...context.case.event_tail.slice().reverse(),
  ];
  $("#activity-list").innerHTML = activities.length
    ? activities.slice(0, 10).map((item) => `<article><span>${escapeHtml(item.kind.replaceAll("_", " "))}</span><p>${escapeHtml(item.summary)}</p></article>`).join("")
    : `<p class="empty-copy">No activity yet.</p>`;

  const acceptBar = $("#accept-bar");
  acceptBar.hidden = !context.editing_locked || context.accepted;
  $("#accept").toggleAttribute("disabled", !context.accept_ticket_digest);
  $("#reject").toggleAttribute("disabled", !context.accept_ticket_digest);
}

async function refresh(): Promise<void> {
  const sequence = ++refreshSequence;
  const next = await getContext() as Context;
  if (sequence !== refreshSequence) return;
  const nextLatestRunId = next.experiment_traces.at(-1)?.run_id ?? null;
  const runIds = new Set(next.experiment_traces.map((item) => item.run_id));
  const shouldFollowLatest = selectedTraceRunId === null
    || !runIds.has(selectedTraceRunId)
    || selectedTraceRunId === knownLatestTraceRunId;
  if (shouldFollowLatest) {
    selectedTraceRunId = nextLatestRunId;
    selectedTraceSignal = null;
  }
  knownLatestTraceRunId = nextLatestRunId;
  if (traceState?.runId !== selectedTraceRunId) traceState = null;
  render(next);
  await loadSelectedTrace(sequence);
}

$("#joint").addEventListener("change", updateCurrentValue);
$("#attribute").addEventListener("change", updateCurrentValue);
$<HTMLSelectElement>("#trace-run").addEventListener("change", (event) => {
  selectedTraceRunId = (event.currentTarget as HTMLSelectElement).value;
  selectedTraceSignal = null;
  void loadSelectedTrace();
});
$<HTMLSelectElement>("#trace-signal").addEventListener("change", (event) => {
  selectedTraceSignal = (event.currentTarget as HTMLSelectElement).value;
  renderTraceChart();
});

$("#draft-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const joint = selectedJoint();
    const attribute = $<HTMLSelectElement>("#attribute").value as "axis" | "damping" | "armature" | "frictionloss";
    const raw = ($("#new-value") as HTMLInputElement).value.trim();
    const newValue = attribute === "axis" ? raw.split(/[ ,]+/).map(Number) : Number(raw);
    const expected = joint[attribute];
    await callTool("set_draft_patch", {
      base_revision_id: context.head_revision_id,
      expected_base_sha256: context.head_asset_sha256,
      patch: { target: { kind: "joint", name: joint.name }, attribute, expected_old_value: expected, new_value: newValue },
    });
    await refresh();
    toast("Shared draft updated — no revision created.");
  } catch (error) { toast(String(error), true); }
});

$("#feedback-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const input = $("#feedback") as HTMLTextAreaElement;
    await callTool("record_design_feedback", { revision_id: context.head_revision_id, asset_sha256: context.head_asset_sha256, feedback: input.value });
    input.value = "";
    await refresh();
    toast("Feedback attached to the current revision.");
  } catch (error) { toast(String(error), true); }
});

$("#run-task").addEventListener("click", async () => {
  try {
    $("#run-task").setAttribute("disabled", "");
    const result = await callTool("run_task", { case_id: context.case.case_id, revision_id: context.head_revision_id, scenario_id: "public_center", capture: "metrics" }) as TaskResult;
    await refresh();
    toast(`Public task: ${result.result.toUpperCase()}`);
  } catch (error) { toast(String(error), true); }
  finally { $("#run-task").removeAttribute("disabled"); }
});

$("#reset").addEventListener("click", async () => {
  try {
    await resetSession();
    taskMetrics = null;
    selectedTraceRunId = null;
    selectedTraceSignal = null;
    knownLatestTraceRunId = null;
    traceState = null;
    await refresh();
    toast("Fresh isolated case created.");
  }
  catch (error) { toast(String(error), true); }
});

$("#accept").addEventListener("click", async () => {
  if (!context.accept_ticket_digest) return;
  try { await acceptRevision(context.accept_ticket_digest); await refresh(); toast("Revision accepted by the human designer."); }
  catch (error) { toast(String(error), true); }
});

$("#reject").addEventListener("click", async () => {
  if (!context.accept_ticket_digest) return;
  const input = $<HTMLTextAreaElement>("#feedback");
  const feedback = input.value.trim();
  if (!feedback) {
    toast("Add design feedback before rejecting this revision.", true);
    input.focus();
    return;
  }
  try {
    await rejectRevision(context.accept_ticket_digest, feedback);
    input.value = "";
    taskMetrics = null;
    selectedTraceRunId = null;
    selectedTraceSignal = null;
    knownLatestTraceRunId = null;
    traceState = null;
    await refresh();
    toast("Revision rejected. A fresh case keeps the rejection note.");
  } catch (error) { toast(String(error), true); }
});

document.addEventListener("asset-autopsy:changed", () => { void refresh(); });

await refresh();
const siteToolsAvailable = await registerWebMcpTools();
$("#webmcp-status").textContent = siteToolsAvailable ? "9 site tools ready" : "Visual mode";
$("#compatibility").hidden = siteToolsAvailable;
