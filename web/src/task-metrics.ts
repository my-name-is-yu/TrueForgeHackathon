export type TaskMetric = { metric: string; value: number | null };

export type DesignIdentity = {
  revisionId: string;
  assetSha256: string;
};

export type TaskMetricsState = DesignIdentity & {
  observations: TaskMetric[];
};

export function bindTaskMetrics(
  identity: DesignIdentity,
  observations: TaskMetric[],
): TaskMetricsState {
  return { ...identity, observations };
}

export function retainTaskMetrics(
  state: TaskMetricsState | null,
  identity: DesignIdentity,
): TaskMetricsState | null {
  if (
    state?.revisionId !== identity.revisionId
    || state.assetSha256 !== identity.assetSha256
  ) {
    return null;
  }
  return state;
}
