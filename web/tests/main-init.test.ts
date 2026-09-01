import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  acceptRevision: vi.fn(),
  callTool: vi.fn(),
  getContext: vi.fn(),
  registerWebMcpTools: vi.fn(),
  resetSession: vi.fn(),
}));

vi.mock("../src/api", () => ({
  acceptRevision: mocks.acceptRevision,
  callTool: mocks.callTool,
  getContext: mocks.getContext,
  resetSession: mocks.resetSession,
}));
vi.mock("../src/robot", () => ({
  createRobotView: () => ({ update: vi.fn() }),
}));
vi.mock("../src/webmcp", () => ({
  registerWebMcpTools: mocks.registerWebMcpTools,
}));

describe("workbench initialization", () => {
  it("establishes the context session before exposing callable site tools", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let resolveContext!: (context: unknown) => void;
    mocks.getContext.mockReturnValue(new Promise((resolve) => {
      resolveContext = resolve;
    }));
    mocks.registerWebMcpTools.mockImplementation(async () => {
      await mocks.callTool("get_design_context", {});
      return true;
    });

    const loading = import("../src/main");
    await vi.waitFor(() => expect(mocks.getContext).toHaveBeenCalledOnce());
    expect(mocks.registerWebMcpTools).not.toHaveBeenCalled();
    expect(mocks.callTool).not.toHaveBeenCalled();

    resolveContext({
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
      head_revision_id: "r000",
      head_asset_sha256: "a".repeat(64),
      draft: null,
      feedback: [],
      editing_locked: false,
      accepted: false,
      accept_ticket_digest: null,
    });
    await loading;

    expect(mocks.registerWebMcpTools).toHaveBeenCalledOnce();
    expect(mocks.callTool).toHaveBeenCalledOnce();
    expect(mocks.getContext.mock.invocationCallOrder[0]).toBeLessThan(
      mocks.registerWebMcpTools.mock.invocationCallOrder[0],
    );
  });
});
