import { afterEach, describe, expect, it, vi } from "vitest";

import { getTrace, rejectRevision } from "../src/api";

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

    await expect(getTrace("run_001")).resolves.toBe(trace);
    expect(fetch).toHaveBeenCalledWith("/api/traces/run_001", {
      credentials: "same-origin",
    });
  });

  it("sends ticket-bound feedback to the human-only reject endpoint", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ rejected: true }),
    });
    vi.stubGlobal("fetch", fetch);

    await expect(rejectRevision("d".repeat(64), "Keep the movement calmer.")).resolves.toBeUndefined();
    expect(fetch).toHaveBeenCalledWith("/api/reject", {
      method: "POST",
      credentials: "same-origin",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        ticket_digest: "d".repeat(64),
        feedback: "Keep the movement calmer.",
      }),
    });
  });
});
