import { describe, expect, it } from "vitest";

import { bindTaskMetrics, retainTaskMetrics } from "../src/task-metrics";

const identity = { revisionId: "r000", assetSha256: "a".repeat(64) };
const observations = [{ metric: "final_target_error_m", value: 0.1 }];

describe("task metric identity", () => {
  it("retains metrics only for the exact revision and asset hash", () => {
    const state = bindTaskMetrics(identity, observations);

    expect(retainTaskMetrics(state, identity)).toBe(state);
    expect(
      retainTaskMetrics(state, { ...identity, revisionId: "r001" }),
    ).toBeNull();
    expect(
      retainTaskMetrics(state, { ...identity, assetSha256: "b".repeat(64) }),
    ).toBeNull();
  });

  it("represents reset as an explicit cleared state", () => {
    const state = bindTaskMetrics(identity, observations);
    const resetState = null;

    expect(state.observations).toEqual(observations);
    expect(retainTaskMetrics(resetState, identity)).toBeNull();
  });
});
