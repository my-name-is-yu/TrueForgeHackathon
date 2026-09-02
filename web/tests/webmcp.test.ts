import { describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({ callTool: vi.fn() }));

vi.mock("../src/api", () => ({ callTool: mocks.callTool }));

import { registerWebMcpTools, webmcpTools } from "../src/webmcp";

describe("WebMCP registration", () => {
  it("registers exactly the eight evidence-workbench tools", async () => {
    const registerTool = vi.fn();
    Object.defineProperty(document, "modelContext", {
      configurable: true,
      value: { registerTool },
    });

    await expect(registerWebMcpTools(document)).resolves.toBe(true);
    const names = registerTool.mock.calls.map(([tool]) => tool.name);
    expect(names).toEqual([
      "get_design_context",
      "inspect_design",
      "run_task",
      "run_experiment",
      "query_trace",
      "set_draft_patch",
      "create_revision_from_draft",
      "verify_revision",
    ]);
    expect(names).toEqual(webmcpTools.map((tool) => tool.name));
    expect(names).toHaveLength(8);
    expect(names).not.toContain("record_design_feedback");
    expect(names.join(" ")).not.toMatch(/accept|reject|history/);
    expect(webmcpTools.find((tool) => tool.name === "verify_revision")?.description).toContain("resets the session");
  });

  it("keeps the visual workbench usable when WebMCP is unsupported", async () => {
    Object.defineProperty(document, "modelContext", {
      configurable: true,
      value: undefined,
    });
    await expect(registerWebMcpTools(document)).resolves.toBe(false);
  });

  it("refreshes shared state when a tool changes state before rejecting", async () => {
    const registerTool = vi.fn();
    Object.defineProperty(document, "modelContext", {
      configurable: true,
      value: { registerTool },
    });
    mocks.callTool.mockRejectedValueOnce(new Error("QUALIFICATION_EXECUTION_FAILED"));
    const changed = vi.fn();
    document.addEventListener("asset-autopsy:changed", changed, { once: true });

    await registerWebMcpTools(document);
    const verify = registerTool.mock.calls
      .map(([tool]) => tool)
      .find((tool) => tool.name === "verify_revision");

    await expect(verify.execute({})).rejects.toThrow("QUALIFICATION_EXECUTION_FAILED");
    expect(changed).toHaveBeenCalledOnce();
    expect((changed.mock.calls[0][0] as CustomEvent).detail).toEqual({
      tool: "verify_revision",
    });
  });
});
