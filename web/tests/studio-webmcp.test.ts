import { afterEach, describe, expect, it, vi } from "vitest";

import {
  registerStudioWebMcpTools,
  STUDIO_CHANGED_EVENT,
  STUDIO_TOOL_NAMES,
} from "../src/studio/webmcp";

describe("Character Robot Studio WebMCP", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    Object.defineProperty(document, "modelContext", { configurable: true, value: undefined });
  });

  it("loads and registers exactly the eight backend-owned semantic contracts", async () => {
    const definitions = [...STUDIO_TOOL_NAMES].reverse().map((name) => ({
      name,
      description: `${name} description`,
      inputSchema: { type: "object", additionalProperties: false },
      annotations: { readOnlyHint: name === "get_studio_context" },
    }));
    const fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => definitions })
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ ok: true, result: { refreshed: true } }) })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        json: async () => ({
          ok: true,
          result: {
            status: "experimental_ready",
            manifest: {
              revision_id: "r003",
              spec_hash: "b".repeat(64),
              geometry_sha256: "e".repeat(64),
              profile_id: "m5-cores3-goplus2/v1",
              catalog_version: "hardware-catalog-v1",
              compiler_version: "character-cad-v1",
              cad_engine_version: "0.11.1",
              firmware_runtime_version: "character-runtime-v1",
              evidence_level: "digital_checks_passed",
              manifest_hash: "f".repeat(64),
              download_requires_human_action: true,
              artifacts: [{
                kind: "stl",
                file_name: "pico.stl",
                media_type: "model/stl",
                sha256: "d".repeat(64),
                byte_size: 512,
                experimental: true,
              }],
            },
            blockers: [],
            next_action: "Review the manifest.",
            human_action_required: true,
          },
        }),
      });
    vi.stubGlobal("fetch", fetch);
    const registerTool = vi.fn();
    Object.defineProperty(document, "modelContext", {
      configurable: true,
      value: { registerTool },
    });

    await expect(registerStudioWebMcpTools(document)).resolves.toBe(true);
    expect(registerTool.mock.calls.map(([tool]) => tool.name)).toEqual(STUDIO_TOOL_NAMES);
    expect(registerTool).toHaveBeenCalledTimes(8);

    const changed = vi.fn();
    document.addEventListener(STUDIO_CHANGED_EVENT, changed, { once: true });
    const revise = registerTool.mock.calls
      .map(([tool]) => tool)
      .find((tool) => tool.name === "revise_design_draft");
    await expect(revise.execute({ draft_hash: "c".repeat(64), edits: [] })).resolves.toEqual({ refreshed: true });
    expect(fetch).toHaveBeenLastCalledWith(
      "/api/studio/v1/tools/revise_design_draft",
      expect.objectContaining({ method: "POST" }),
    );
    expect((changed.mock.calls[0][0] as CustomEvent).detail).toEqual({
      tool: "revise_design_draft",
      ok: true,
    });

    const packChanged = vi.fn();
    document.addEventListener(STUDIO_CHANGED_EVENT, packChanged, { once: true });
    const prepare = registerTool.mock.calls
      .map(([tool]) => tool)
      .find((tool) => tool.name === "prepare_build_pack");
    await prepare.execute({ revision_id: "r003", expected_spec_hash: "b".repeat(64) });
    expect((packChanged.mock.calls[0][0] as CustomEvent).detail).toMatchObject({
      tool: "prepare_build_pack",
      ok: true,
      buildPackResult: {
        status: "experimental_ready",
        manifest: { revisionId: "r003", cadEngineVersion: "0.11.1" },
        artifacts: [{ fileName: "pico.stl", downloadUrl: `/api/studio/v1/artifacts/${"d".repeat(64)}` }],
      },
    });
  });

  it("publishes a typed failure so the shared page can retain a rejected edit", async () => {
    const definitions = STUDIO_TOOL_NAMES.map((name) => ({
      name,
      description: `${name} description`,
      inputSchema: { type: "object", additionalProperties: false },
    }));
    const fetch = vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => definitions })
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        statusText: "Conflict",
        json: async () => ({
          ok: false,
          error: {
            code: "STALE_DRAFT",
            message: "The draft changed.",
            next_action: "Read the current draft hash.",
          },
        }),
      });
    vi.stubGlobal("fetch", fetch);
    const registerTool = vi.fn();
    Object.defineProperty(document, "modelContext", {
      configurable: true,
      value: { registerTool },
    });
    await registerStudioWebMcpTools(document);
    const changed = vi.fn();
    document.addEventListener(STUDIO_CHANGED_EVENT, changed, { once: true });
    const revise = registerTool.mock.calls
      .map(([tool]) => tool)
      .find((tool) => tool.name === "revise_design_draft");

    await expect(revise.execute({})).rejects.toThrow("STALE_DRAFT");
    expect((changed.mock.calls[0][0] as CustomEvent).detail).toEqual({
      tool: "revise_design_draft",
      ok: false,
      error: {
        code: "STALE_DRAFT",
        message: "STALE_DRAFT: The draft changed.",
        nextAction: "Read the current draft hash.",
      },
    });
  });

  it("does not fetch tool definitions when the browser has no WebMCP", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    await expect(registerStudioWebMcpTools(document)).resolves.toBe(false);
    expect(fetch).not.toHaveBeenCalled();
  });
});
