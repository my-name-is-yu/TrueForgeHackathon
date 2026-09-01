import "./style.css";
import { acceptRevision, callTool, getContext, resetSession, type JsonObject } from "./api";
import { createRobotView } from "./robot";
import { bindTaskMetrics, retainTaskMetrics, type TaskMetricsState } from "./task-metrics";
import { registerWebMcpTools } from "./webmcp";

type Joint = {
  name: string;
  axis: [number, number, number];
  damping: number;
  armature: number;
  frictionloss: number;
};

type Context = JsonObject & {
  case: { case_id: string; qualification_state: string; remaining_budgets: Record<string, number>; event_tail: { kind: string; summary: string }[] };
  design: { joints: Joint[]; bodies: { name: string; parent: string | null }[]; actuators: { name: string; joint_name: string }[] };
  head_revision_id: string;
  head_asset_sha256: string;
  draft: { patch: { target: { name: string }; attribute: string; new_value: number | number[] } } | null;
  feedback: { feedback: string; revision_id: string }[];
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
      <div id="metrics" class="metrics"><p class="empty-copy">Ask Codex to experiment, or run the fixed task here.</p></div>
    </section>
    <section class="activity panel">
      <div class="panel-title"><div><span>SHARED CONTEXT</span><strong>Revision-bound activity</strong></div></div>
      <div id="activity-list"></div>
    </section>
  </div>
  <section id="accept-bar" class="accept-bar" hidden>
    <div><span>QUALIFICATION PASSED</span><strong>Hidden requirements are satisfied. Editing is locked.</strong></div>
    <button id="accept" class="primary">Accept revision</button>
  </section>
  <div id="toast" role="status"></div>
`;

const canvas = document.querySelector<HTMLElement>("#robot-canvas")!;
const robot = createRobotView(canvas);
let context: Context;
let taskMetrics: TaskMetricsState | null = null;

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
  $("#metrics").innerHTML = taskMetrics
    ? taskMetrics.observations.map((item) => `<article><span>${escapeHtml(item.metric.replaceAll("_", " "))}</span><strong>${item.value === null ? "—" : Number(item.value).toPrecision(4)}</strong></article>`).join("")
    : `<p class="empty-copy">Ask Codex to experiment, or run the fixed task here.</p>`;
}

function render(next: Context): void {
  context = next;
  taskMetrics = retainTaskMetrics(taskMetrics, {
    revisionId: context.head_revision_id,
    assetSha256: context.head_asset_sha256,
  });
  renderTaskMetrics();
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
    ...context.feedback.map((item) => ({ kind: "HUMAN FEEDBACK", summary: `${item.revision_id}: ${item.feedback}` })),
    ...context.case.event_tail.slice().reverse(),
  ];
  $("#activity-list").innerHTML = activities.length
    ? activities.slice(0, 10).map((item) => `<article><span>${escapeHtml(item.kind.replaceAll("_", " "))}</span><p>${escapeHtml(item.summary)}</p></article>`).join("")
    : `<p class="empty-copy">No activity yet.</p>`;

  const acceptBar = $("#accept-bar");
  acceptBar.hidden = !context.editing_locked || context.accepted;
  $("#accept").toggleAttribute("disabled", !context.accept_ticket_digest);
}

async function refresh(): Promise<void> {
  render(await getContext() as Context);
}

$("#joint").addEventListener("change", updateCurrentValue);
$("#attribute").addEventListener("change", updateCurrentValue);

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
    const result = await callTool("run_task", { case_id: context.case.case_id, revision_id: context.head_revision_id, scenario_id: "public_center", capture: "metrics" }) as { result: string; observations: { metric: string; value: number | null }[] };
    taskMetrics = bindTaskMetrics(
      { revisionId: context.head_revision_id, assetSha256: context.head_asset_sha256 },
      result.observations,
    );
    renderTaskMetrics();
    await refresh();
    toast(`Public task: ${result.result.toUpperCase()}`);
  } catch (error) { toast(String(error), true); }
  finally { $("#run-task").removeAttribute("disabled"); }
});

$("#reset").addEventListener("click", async () => {
  try { await resetSession(); taskMetrics = null; await refresh(); toast("Fresh isolated case created."); }
  catch (error) { toast(String(error), true); }
});

$("#accept").addEventListener("click", async () => {
  if (!context.accept_ticket_digest) return;
  try { await acceptRevision(context.accept_ticket_digest); await refresh(); toast("Revision accepted by the human designer."); }
  catch (error) { toast(String(error), true); }
});

document.addEventListener("asset-autopsy:changed", () => { void refresh(); });

const siteToolsAvailable = await registerWebMcpTools();
$("#webmcp-status").textContent = siteToolsAvailable ? "9 site tools ready" : "Visual mode";
$("#compatibility").hidden = siteToolsAvailable;
await refresh();
