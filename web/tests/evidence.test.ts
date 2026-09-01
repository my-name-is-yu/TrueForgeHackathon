import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  acceptRevision: vi.fn(),
  callTool: vi.fn(),
  getContext: vi.fn(),
  getTrace: vi.fn(),
  rejectRevision: vi.fn(),
  resetSession: vi.fn(),
}));

vi.mock("../src/api", () => mocks);
vi.mock("../src/robot", () => ({
  createRobotView: () => ({ update: vi.fn() }),
}));
vi.mock("../src/webmcp", () => ({
  registerWebMcpTools: vi.fn().mockResolvedValue(false),
}));

const hash = (character: string) => character.repeat(64);

const design = {
  joints: [{
    name: "joint_b",
    axis: [0, 1, 0],
    damping: 0.1,
    armature: 0.01,
    frictionloss: 0,
  }],
  bodies: [],
  actuators: [],
};

const qualifiedContext = {
  case: {
    case_id: "compound-arm-01",
    qualification_state: "passed",
    remaining_budgets: {},
    revision_history: [
      {
        revision_id: "r000",
        asset_sha256: hash("a"),
        parent_revision_id: null,
        canonical_diff: [],
      },
      {
        revision_id: "r001",
        asset_sha256: hash("b"),
        parent_revision_id: "r000",
        canonical_diff: [{
          target: "joint_b",
          attribute: "axis",
          before: "0 0 1",
          after: "0 1 0",
        }],
      },
    ],
    event_tail: [],
  },
  design,
  head_revision_id: "r001",
  head_asset_sha256: hash("b"),
  draft: null,
  feedback: [],
  experiment_traces: [
    {
      run_id: "run_001",
      revision_id: "r000",
      asset_sha256: hash("a"),
      signals: ["qpos:joint_b"],
      row_count: 3,
      start_time_s: 0.002,
      end_time_s: 0.006,
    },
    {
      run_id: "run_002",
      revision_id: "r000",
      asset_sha256: hash("a"),
      signals: ["qvel:joint_b", "control:motor_b"],
      row_count: 3,
      start_time_s: 0.002,
      end_time_s: 0.006,
    },
  ],
  latest_task: {
    revision_id: "r001",
    result: "pass",
    observations: [{ metric: "final_target_error_m", value: 0.001 }],
    behavior_diff: {
      changed: true,
      first_divergence: {
        step: 12,
        time_s: 0.024,
        signal: "qpos",
        magnitude: 0.2,
      },
      metric_deltas: [{
        metric: "final_target_error_m",
        before: 0.1,
        after: 0.001,
        delta: -0.099,
      }],
      clause_outcomes: [{ clause_id: "reach_error", outcome: "improved" }],
      verdict: "public_pass",
    },
  },
  rejections: [],
  editing_locked: true,
  accepted: false,
  accept_ticket_digest: hash("c"),
};

const restartedContext = {
  ...qualifiedContext,
  case: {
    ...qualifiedContext.case,
    qualification_state: "unused",
    revision_history: [qualifiedContext.case.revision_history[0]],
  },
  design: {
    ...design,
    joints: [{ ...design.joints[0], axis: [0, 0, 1] }],
  },
  head_revision_id: "r000",
  head_asset_sha256: hash("a"),
  experiment_traces: [],
  latest_task: null,
  rejections: [
    ...Array.from({ length: 10 }, (_, index) => ({
      revision_id: "r001",
      asset_sha256: hash("b"),
      feedback: `Earlier rejection ${index + 1}.`,
    })),
    {
      revision_id: "r001",
      asset_sha256: hash("b"),
      feedback: "The repaired motion still feels too abrupt.",
    },
  ],
  editing_locked: false,
  accept_ticket_digest: null,
};

const improvedFailContext = {
  ...qualifiedContext,
  latest_task: {
    ...qualifiedContext.latest_task,
    result: "fail",
    behavior_diff: {
      ...qualifiedContext.latest_task.behavior_diff,
      verdict: "improved",
    },
  },
};

describe("shared evidence UI", () => {
  it("renders traces and diffs, then preserves human rejection feedback across restart", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let currentContext:
      | typeof qualifiedContext
      | typeof restartedContext
      | typeof improvedFailContext = qualifiedContext;
    mocks.getContext.mockImplementation(async () => currentContext);
    mocks.getTrace.mockImplementation(async (runId: string) => ({
      columns: runId === "run_001"
        ? [
          { kind: "time" },
          { kind: "qpos", joint_name: "joint_b" },
          { kind: "control", actuator_name: "motor_b" },
        ]
        : [
          { kind: "time" },
          { kind: "qvel", joint_name: "joint_b" },
          { kind: "control", actuator_name: "motor_b" },
        ],
      rows: runId === "run_001"
        ? [
          { time_s: 0.002, values: { "qpos:joint_b": 0, "control:motor_b": -0.45 } },
          { time_s: 0.004, values: { "qpos:joint_b": 0.1, "control:motor_b": -0.45 } },
          { time_s: 0.006, values: { "qpos:joint_b": 0.2, "control:motor_b": -0.25 } },
        ]
        : [
          { time_s: 0.002, values: { "qvel:joint_b": 0, "control:motor_b": 0.2 } },
          { time_s: 0.004, values: { "qvel:joint_b": 0.4, "control:motor_b": 0.2 } },
          { time_s: 0.006, values: { "qvel:joint_b": 0.1, "control:motor_b": 0.2 } },
        ],
    }));
    mocks.rejectRevision.mockImplementation(async () => {
      currentContext = restartedContext;
    });

    await import("../src/main");

    expect(document.querySelector("#behavior-verdict")!.textContent).toBe("PUBLIC PASS");
    expect(document.querySelector("#behavior-diff")!.textContent).toContain("0.1000");
    expect(document.querySelector("#behavior-diff")!.textContent).toContain("0.001000");
    expect(document.querySelector("#behavior-diff")!.textContent).toContain("reach error: improved");
    expect(document.querySelector("#diff-revision")!.textContent).toBe("r000 → r001");
    expect(document.querySelector("#revision-diff")!.textContent).toContain("joint_b.axis");
    expect(document.querySelector("#revision-diff")!.textContent).toContain("0 0 1");
    expect(mocks.getTrace).toHaveBeenCalledWith("run_002");
    expect(document.querySelector("#trace-run")!.getAttribute("disabled")).toBeNull();
    expect(document.querySelector<HTMLSelectElement>("#trace-run")!.value).toBe("run_002");
    expect(document.querySelector("#trace-chart polyline")!.getAttribute("points")).not.toBe("");

    currentContext = improvedFailContext;
    document.dispatchEvent(new Event("asset-autopsy:changed"));
    await vi.waitFor(() => {
      expect(document.querySelector("#behavior-verdict")!.textContent).toBe("FAIL · IMPROVED");
      expect(document.querySelector("#behavior-verdict")!.className).toBe("fail");
    });
    currentContext = qualifiedContext;
    document.dispatchEvent(new Event("asset-autopsy:changed"));
    await vi.waitFor(() => {
      expect(document.querySelector("#behavior-verdict")!.textContent).toBe("PUBLIC PASS");
    });

    const runSelect = document.querySelector<HTMLSelectElement>("#trace-run")!;
    runSelect.value = "run_001";
    runSelect.dispatchEvent(new Event("change"));
    await vi.waitFor(() => expect(mocks.getTrace).toHaveBeenLastCalledWith("run_001"));
    await vi.waitFor(() => {
      expect(document.querySelector<HTMLSelectElement>("#trace-signal")!.value).toBe("qpos:joint_b");
      expect(document.querySelector("#trace-chart svg")!.getAttribute("aria-label")).toBe("qpos:joint_b trace");
    });

    const feedback = document.querySelector<HTMLTextAreaElement>("#feedback")!;
    feedback.value = "The repaired motion still feels too abrupt.";
    document.querySelector<HTMLButtonElement>("#reject")!.click();
    await vi.waitFor(() => {
      expect(mocks.rejectRevision).toHaveBeenCalledWith(
        hash("c"),
        "The repaired motion still feels too abrupt.",
      );
      expect(document.querySelector("#revision")!.textContent).toContain("r000");
    });

    expect(document.querySelector("#activity-list")!.textContent).toContain("HUMAN REJECTION");
    expect(document.querySelector("#activity-list")!.textContent).toContain("r001: The repaired motion still feels too abrupt.");
    expect(document.querySelector("#trace-chart")!.textContent).toContain("No completed experiment trace yet.");
    expect((document.querySelector<HTMLElement>("#accept-bar")!).hidden).toBe(true);

    let resolveOlder!: (context: typeof qualifiedContext) => void;
    let resolveNewest!: (context: typeof restartedContext) => void;
    mocks.getContext
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOlder = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveNewest = resolve; }));
    document.dispatchEvent(new Event("asset-autopsy:changed"));
    document.dispatchEvent(new Event("asset-autopsy:changed"));
    await vi.waitFor(() => expect(resolveNewest).toBeTypeOf("function"));

    resolveNewest(restartedContext);
    await vi.waitFor(() => {
      expect(document.querySelector("#revision")!.textContent).toContain("r000");
    });
    resolveOlder(qualifiedContext);
    await new Promise((resolve) => window.setTimeout(resolve, 10));

    expect(document.querySelector("#revision")!.textContent).toContain("r000");
    expect((document.querySelector<HTMLElement>("#accept-bar")!).hidden).toBe(true);
  });
});
