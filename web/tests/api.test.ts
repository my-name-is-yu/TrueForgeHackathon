import { afterEach, describe, expect, it, vi } from "vitest";

import * as api from "../src/api";

describe("workbench evidence API", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("loads a trace from the session-only endpoint", async () => {
    const trace = {
      columns: [
        { kind: "time" },
        { kind: "qpos", joint_name: "joint_b" },
        { kind: "control", actuator_name: "motor_b" },
      ],
      rows: [{
        time_s: 0.002,
        values: { "qpos:joint_b": 0, "control:motor_b": -0.45 },
      }],
    };
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => trace,
    });
    vi.stubGlobal("fetch", fetch);

    await expect(api.getTrace("run_001")).resolves.toBe(trace);
    expect(fetch).toHaveBeenCalledWith("/api/traces/run_001", {
      credentials: "same-origin",
    });
  });

  it("keeps reset as the only session-level action", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
    });
    vi.stubGlobal("fetch", fetch);

    await expect(api.resetSession()).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledWith("/api/reset", {
      method: "POST",
      credentials: "same-origin",
    });
    expect("acceptRevision" in api).toBe(false);
    expect("rejectRevision" in api).toBe(false);
  });
});
