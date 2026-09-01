import { describe, expect, it, vi } from "vitest";

import { registerWebMcpTools, webmcpTools } from "../src/webmcp";

describe("WebMCP registration", () => {
  it("registers the nine browser tools without exposing human review actions", async () => {
    const registerTool = vi.fn();
    Object.defineProperty(document, "modelContext", {
      configurable: true,
      value: { registerTool },
    });

    await expect(registerWebMcpTools(document)).resolves.toBe(true);
    const names = registerTool.mock.calls.map(([tool]) => tool.name);
    expect(names).toEqual(webmcpTools.map((tool) => tool.name));
    expect(names).toHaveLength(9);
    expect(names).not.toContain("accept_revision");
    expect(names).not.toContain("accept");
    expect(names).not.toContain("reject_revision");
    expect(names).not.toContain("reject");
  });

  it("keeps the visual workbench usable when WebMCP is unsupported", async () => {
    Object.defineProperty(document, "modelContext", {
      configurable: true,
      value: undefined,
    });
    await expect(registerWebMcpTools(document)).resolves.toBe(false);
  });
});
