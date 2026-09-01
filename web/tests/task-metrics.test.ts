import { describe, expect, it } from "vitest";

import { bindTaskMetrics } from "../src/task-metrics";

const identity = { revisionId: "r000", assetSha256: "a".repeat(64) };
const observations = [{ metric: "final_target_error_m", value: 0.1 }];
const result = {
  revision_id: "r000",
  result: "fail" as const,
  observations,
  behavior_diff: null,
};

describe("task metric identity", () => {
  it("binds task evidence to the exact revision and asset hash", () => {
    const state = bindTaskMetrics(identity, result);

    expect(state.revisionId).toBe(identity.revisionId);
    expect(state.assetSha256).toBe(identity.assetSha256);
    expect(state.observations).toEqual(observations);
    expect(state.result).toBe("fail");
  });
});
