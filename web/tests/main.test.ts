import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  acceptRevision: vi.fn(),
  callTool: vi.fn(),
  getContext: vi.fn(),
  resetSession: vi.fn(),
}));

vi.mock("../src/api", () => mocks);
vi.mock("../src/robot", () => ({
  createRobotView: () => ({ update: vi.fn() }),
}));
vi.mock("../src/webmcp", () => ({
  registerWebMcpTools: vi.fn().mockResolvedValue(false),
}));

const context = (revisionId: string, assetSha256: string) => ({
  case: {
    case_id: "compound-arm-01",
    qualification_state: "open",
    remaining_budgets: {},
    event_tail: [],
  },
  design: {
    joints: [{
      name: "joint_a",
      axis: [0, 0, 1],
      damping: 0.1,
      armature: 0.01,
      frictionloss: 0,
    }],
    bodies: [],
    actuators: [],
  },
  head_revision_id: revisionId,
  head_asset_sha256: assetSha256,
  draft: null,
  feedback: [],
  editing_locked: false,
  accepted: false,
  accept_ticket_digest: null,
});

describe("public task metrics", () => {
  beforeEach(() => {
    document.body.innerHTML = '<main id="app"></main>';
    mocks.callTool.mockReset();
    mocks.getContext.mockReset();
  });

  it("does not attach a pending task result to a concurrently refreshed revision", async () => {
    let currentContext = context("r000", "a".repeat(64));
    let resolveTask!: (result: unknown) => void;
    const taskResult = new Promise((resolve) => { resolveTask = resolve; });
    mocks.getContext.mockImplementation(async () => currentContext);
    mocks.callTool.mockImplementation(async (name: string) => {
      if (name === "run_task") return taskResult;
      throw new Error(`Unexpected tool: ${name}`);
    });

    await import("../src/main");
    document.querySelector<HTMLButtonElement>("#run-task")!.click();
    await vi.waitFor(() => expect(mocks.callTool).toHaveBeenCalledOnce());

    currentContext = context("r001", "b".repeat(64));
    document.dispatchEvent(new Event("asset-autopsy:changed"));
    await vi.waitFor(() => {
      expect(document.querySelector("#revision")!.textContent).toContain("r001");
    });
    mocks.getContext.mockRejectedValue(new Error("context unavailable"));

    resolveTask({
      result: "pass",
      observations: [{ metric: "final_target_error_m", value: 0.001 }],
    });
    await vi.waitFor(() => {
      expect(document.querySelector("#run-task")!.hasAttribute("disabled")).toBe(false);
      expect(document.querySelector("#metrics")!.textContent).toContain(
        "Ask Codex to experiment",
      );
      expect(document.querySelector("#metrics")!.textContent).not.toContain(
        "final target error m",
      );
    });
  });
});
