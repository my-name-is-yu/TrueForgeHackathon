import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  callTool: vi.fn(),
  getContext: vi.fn(),
  getTrace: vi.fn(),
  resetSession: vi.fn(),
  robotUpdate: vi.fn(),
}));

vi.mock("../src/api", () => ({
  callTool: mocks.callTool,
  getContext: mocks.getContext,
  getTrace: mocks.getTrace,
  resetSession: mocks.resetSession,
}));
vi.mock("../src/robot", () => ({
  createRobotView: () => ({ update: mocks.robotUpdate }),
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
  },
  design,
  head_revision_id: "r001",
  head_asset_sha256: hash("b"),
  head_parent_revision_id: "r000",
  head_canonical_diff: [{
    target: "joint_b",
    attribute: "axis",
    before: "0 0 1",
    after: "0 1 0",
  }],
  draft: null,
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
      revision_id: "r001",
      asset_sha256: hash("b"),
      signals: ["qvel:joint_b"],
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
  editing_locked: true,
};

const restartedContext = {
  ...qualifiedContext,
  case: {
    ...qualifiedContext.case,
    qualification_state: "unused",
  },
  design: {
    ...design,
    joints: [{ ...design.joints[0], axis: [0, 0, 1] }],
  },
  head_revision_id: "r000",
  head_asset_sha256: hash("a"),
  head_parent_revision_id: null,
  head_canonical_diff: [],
  experiment_traces: [],
  latest_task: null,
  editing_locked: false,
};

const failedQualificationContext = {
  ...qualifiedContext,
  case: {
    ...qualifiedContext.case,
    qualification_state: "failed",
  },
  editing_locked: true,
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

function traceFor(runId: string) {
  const signal = runId === "run_001" ? "qpos:joint_b" : "qvel:joint_b";
  return {
    columns: [{ kind: "time" }],
    rows: [
      { time_s: 0.002, values: { [signal]: 0 } },
      { time_s: 0.004, values: { [signal]: 0.4 } },
      { time_s: 0.006, values: { [signal]: 0.1 } },
    ],
  };
}

describe("shared evidence UI", () => {
  it("renders current-head evidence, keeps qualification read-only, and rejects stale refreshes", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let currentContext:
      | typeof qualifiedContext
      | typeof restartedContext
      | typeof improvedFailContext
      | typeof failedQualificationContext = qualifiedContext;
    mocks.getContext.mockImplementation(async () => currentContext);
    mocks.getTrace.mockImplementation(async (runId: string) => traceFor(runId));
    mocks.resetSession.mockImplementation(async () => {
      currentContext = restartedContext;
    });

    await import("../src/main");

    expect(document.querySelector("#behavior-verdict")!.textContent).toBe("PUBLIC PASS");
    expect(document.querySelector("#behavior-diff")!.textContent).toContain("reach error: improved");
    expect(document.querySelector("#diff-revision")!.textContent).toBe("r000 → r001");
    expect(document.querySelector("#revision-diff")!.textContent).toContain("joint_b.axis");
    expect(document.querySelector("#revision-diff")!.textContent).toContain("0 0 1");
    expect(document.querySelector("#lock-state")!.textContent).toBe("Qualified — editing locked");
    expect([...document.querySelectorAll<HTMLInputElement>("#draft-form input, #draft-form select, #draft-form button")].every((element) => element.disabled)).toBe(true);
    expect(document.querySelector<HTMLButtonElement>("#run-task")!.disabled).toBe(false);
    expect(document.querySelector<HTMLButtonElement>("#reset")!.disabled).toBe(false);
    expect(document.querySelector("#feedback-form")).toBeNull();
    expect(document.querySelector("#activity-list")).toBeNull();
    expect(document.querySelector("#accept-bar")).toBeNull();
    expect(mocks.robotUpdate).toHaveBeenLastCalledWith({
      revisionId: "r001",
      joints: design.joints,
      selectedJointName: "joint_b",
      draft: null,
      editingLocked: true,
      qualificationState: "passed",
    });

    expect(mocks.getTrace).toHaveBeenCalledWith("run_002");
    expect(document.querySelector<HTMLSelectElement>("#trace-run")!.value).toBe("run_002");
    expect(document.querySelector("#trace-chart svg")!.getAttribute("aria-label")).toBe("qvel:joint_b trace");

    currentContext = failedQualificationContext;
    document.dispatchEvent(new Event("asset-autopsy:changed"));
    await vi.waitFor(() => {
      expect(document.querySelector("#lock-state")!.textContent).toBe("Qualification failed — reset required");
    });
    expect([...document.querySelectorAll<HTMLInputElement>("#draft-form input, #draft-form select, #draft-form button")].every((element) => element.disabled)).toBe(true);
    expect(document.querySelector<HTMLButtonElement>("#run-task")!.disabled).toBe(false);
    expect(document.querySelector<HTMLButtonElement>("#reset")!.disabled).toBe(false);
    expect(mocks.robotUpdate).toHaveBeenLastCalledWith(expect.objectContaining({
      editingLocked: true,
      qualificationState: "failed",
    }));

    currentContext = qualifiedContext;
    document.dispatchEvent(new Event("asset-autopsy:changed"));
    await vi.waitFor(() => {
      expect(document.querySelector("#lock-state")!.textContent).toBe("Qualified — editing locked");
    });

    currentContext = improvedFailContext;
    document.dispatchEvent(new Event("asset-autopsy:changed"));
    await vi.waitFor(() => {
      expect(document.querySelector("#behavior-verdict")!.textContent).toBe("FAIL · IMPROVED");
    });
    currentContext = qualifiedContext;
    document.dispatchEvent(new Event("asset-autopsy:changed"));
    await vi.waitFor(() => {
      expect(document.querySelector("#behavior-verdict")!.textContent).toBe("PUBLIC PASS");
    });

    let resolveOldTrace!: (trace: ReturnType<typeof traceFor>) => void;
    mocks.getTrace
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOldTrace = resolve; }))
      .mockImplementationOnce(async (runId: string) => traceFor(runId));
    const runSelect = document.querySelector<HTMLSelectElement>("#trace-run")!;
    runSelect.value = "run_001";
    runSelect.dispatchEvent(new Event("change"));
    await vi.waitFor(() => expect(resolveOldTrace).toBeTypeOf("function"));
    runSelect.value = "run_002";
    runSelect.dispatchEvent(new Event("change"));
    await vi.waitFor(() => {
      expect(document.querySelector("#trace-chart svg")!.getAttribute("aria-label")).toBe("qvel:joint_b trace");
    });
    resolveOldTrace(traceFor("run_001"));
    await new Promise((resolve) => window.setTimeout(resolve, 10));
    expect(document.querySelector("#trace-chart svg")!.getAttribute("aria-label")).toBe("qvel:joint_b trace");

    document.querySelector<HTMLButtonElement>("#reset")!.click();
    await vi.waitFor(() => {
      expect(document.querySelector("#revision")!.textContent).toContain("r000");
    });
    expect(document.querySelector("#lock-state")!.textContent).toBe("Editable");
    expect([...document.querySelectorAll<HTMLInputElement>("#draft-form input, #draft-form select, #draft-form button")].every((element) => !element.disabled)).toBe(true);
    expect(document.querySelector<HTMLInputElement>("#new-value")!.value).toBe("");
    expect(document.querySelector("#diff-revision")!.textContent).toBe("Original");
    expect(document.querySelector("#trace-chart")!.textContent).toContain("No completed experiment trace yet.");

    let resolveOlder!: (context: typeof restartedContext) => void;
    let resolveNewest!: (context: typeof qualifiedContext) => void;
    mocks.getContext
      .mockImplementationOnce(() => new Promise((resolve) => { resolveOlder = resolve; }))
      .mockImplementationOnce(() => new Promise((resolve) => { resolveNewest = resolve; }));
    document.dispatchEvent(new Event("asset-autopsy:changed"));
    document.dispatchEvent(new Event("asset-autopsy:changed"));
    await vi.waitFor(() => expect(resolveNewest).toBeTypeOf("function"));

    resolveNewest(qualifiedContext);
    await vi.waitFor(() => {
      expect(document.querySelector("#revision")!.textContent).toContain("r001");
    });
    resolveOlder(restartedContext);
    await new Promise((resolve) => window.setTimeout(resolve, 10));

    expect(document.querySelector("#revision")!.textContent).toContain("r001");
    expect(document.querySelector("#diff-revision")!.textContent).toBe("r000 → r001");
    expect(mocks.robotUpdate).toHaveBeenLastCalledWith(expect.objectContaining({
      revisionId: "r001",
      editingLocked: true,
      qualificationState: "passed",
    }));
  });
});
