export type TaskMetric = { metric: string; value: number | null };

export type BehaviorDiff = {
  changed: boolean;
  first_divergence: {
    step: number;
    time_s: number;
    signal: "end_effector_position" | "qpos" | "qvel";
    magnitude: number;
  } | null;
  metric_deltas: {
    metric: string;
    before: number | null;
    after: number | null;
    delta: number | null;
  }[];
  clause_outcomes: {
    clause_id: string;
    outcome: "improved" | "regressed" | "unchanged";
  }[];
  verdict: "regressed" | "changed" | "improved" | "public_pass" | "unchanged_failure";
};

export type TaskResult = {
  revision_id: string;
  result: "pass" | "fail";
  observations: TaskMetric[];
  behavior_diff: BehaviorDiff | null;
};

export type DesignIdentity = {
  revisionId: string;
  assetSha256: string;
};

export type TaskMetricsState = DesignIdentity & {
  result: "pass" | "fail";
  observations: TaskMetric[];
  behaviorDiff: BehaviorDiff | null;
};

export function bindTaskMetrics(
  identity: DesignIdentity,
  result: TaskResult,
): TaskMetricsState {
  return {
    ...identity,
    result: result.result,
    observations: result.observations,
    behaviorDiff: result.behavior_diff,
  };
}
