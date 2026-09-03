import {
  EVIDENCE_LEVELS,
  type BuildPackArtifact,
  type BuildPackManifest,
  type BuildPackResult,
  type CharacterSpecView,
  type DesignTarget,
  type EvidenceLevel,
  type HardwareProfileSummary,
  type MorphologyNodeSummary,
  type ScenarioFrame,
  type ScenarioPreview,
  type StudioContext,
  type StudioWarning,
  type ValidationCheck,
  type ValidationSummary,
  type WarningSeverity,
} from "./types";

export type JsonObject = Record<string, unknown>;

export class StudioContractError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "StudioContractError";
  }
}

export class StudioApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly retryable: boolean;
  readonly nextAction: string | null;

  constructor(
    code: string,
    message: string,
    status: number,
    retryable = false,
    nextAction: string | null = null,
  ) {
    super(`${code}: ${message}`);
    this.name = "StudioApiError";
    this.code = code;
    this.status = status;
    this.retryable = retryable;
    this.nextAction = nextAction;
  }
}

const isRecord = (value: unknown): value is JsonObject => (
  typeof value === "object" && value !== null && !Array.isArray(value)
);

const record = (value: unknown, path: string): JsonObject => {
  if (!isRecord(value)) throw new StudioContractError(`${path} must be an object`);
  return value;
};

const optionalRecord = (value: unknown, path: string): JsonObject | null => {
  if (value === null || value === undefined) return null;
  return record(value, path);
};

const requiredString = (value: unknown, path: string): string => {
  if (typeof value !== "string" || value.length === 0) {
    throw new StudioContractError(`${path} must be a non-empty string`);
  }
  return value;
};

const optionalString = (value: unknown, path: string): string | null => {
  if (value === null || value === undefined) return null;
  return requiredString(value, path);
};

const finiteNumber = (value: unknown, path: string): number => {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new StudioContractError(`${path} must be a finite number`);
  }
  return value;
};

const optionalFiniteNumber = (value: unknown, path: string): number | null => {
  if (value === null || value === undefined) return null;
  return finiteNumber(value, path);
};

const wheelCommand = (value: unknown, path: string): number => {
  const command = finiteNumber(value, path);
  if (command < -1 || command > 1) {
    throw new StudioContractError(`${path} must be between -1 and 1`);
  }
  return command;
};

const stringArray = (value: unknown, path: string): string[] => {
  if (value === null || value === undefined) return [];
  if (!Array.isArray(value)) throw new StudioContractError(`${path} must be an array`);
  return value.map((item, index) => {
    if (typeof item === "string") return requiredString(item, `${path}[${index}]`);
    const itemRecord = record(item, `${path}[${index}]`);
    return requiredString(
      itemRecord.label ?? itemRecord.name ?? itemRecord.component_id ?? itemRecord.capability_id,
      `${path}[${index}].label`,
    );
  });
};

const evidenceLevel = (value: unknown, path: string): EvidenceLevel => {
  if (typeof value === "string" && EVIDENCE_LEVELS.includes(value as EvidenceLevel)) {
    return value as EvidenceLevel;
  }
  throw new StudioContractError(`${path} is not a supported evidence level`);
};

const profileEvidenceLevel = (value: unknown, path: string): EvidenceLevel => {
  if (value === "digital_only") return "concept_only";
  if (value === "profile_qualified") return "within_qualified_profile";
  if (value === "exact_build_verified") return "exact_build_verified";
  return evidenceLevel(value ?? "concept_only", path);
};

const warningSeverity = (value: unknown, path: string): WarningSeverity => {
  if (value === undefined || value === null) return "warning";
  if (value === "info" || value === "warning" || value === "error") return value;
  throw new StudioContractError(`${path} is not a supported warning severity`);
};

const parseWarning = (value: unknown, path: string): StudioWarning => {
  if (typeof value === "string") {
    return { code: "DESIGN_WARNING", message: requiredString(value, path), severity: "warning" };
  }
  const item = record(value, path);
  return {
    code: requiredString(item.code ?? "DESIGN_WARNING", `${path}.code`),
    message: requiredString(item.message, `${path}.message`),
    severity: warningSeverity(item.severity, `${path}.severity`),
    path: optionalString(item.path, `${path}.path`),
    measuredValue: optionalFiniteNumber(item.measured_value, `${path}.measured_value`),
    limitValue: optionalFiniteNumber(item.limit_value, `${path}.limit_value`),
    suggestion: optionalString(item.suggestion, `${path}.suggestion`),
  };
};

const parseWarnings = (value: unknown, path: string): StudioWarning[] => {
  if (value === null || value === undefined) return [];
  if (!Array.isArray(value)) throw new StudioContractError(`${path} must be an array`);
  return value.map((item, index) => parseWarning(item, `${path}[${index}]`));
};

const parseMorphologyNodes = (value: unknown, path: string): MorphologyNodeSummary[] => {
  if (value === null || value === undefined) return [];
  if (!Array.isArray(value)) throw new StudioContractError(`${path} must be an array`);
  return value.map((raw, index) => {
    const node = record(raw, `${path}[${index}]`);
    const nodeId = requiredString(node.node_id, `${path}[${index}].node_id`);
    const role = requiredString(node.role, `${path}[${index}].role`);
    const attachment = optionalRecord(node.attachment, `${path}[${index}].attachment`);
    return {
      nodeId,
      role,
      label: optionalString(node.label, `${path}[${index}].label`) ?? role.replaceAll("_", " "),
      parentNodeId: optionalString(attachment?.parent_node_id, `${path}[${index}].attachment.parent_node_id`),
      parentAnchor: optionalString(attachment?.parent_anchor, `${path}[${index}].attachment.parent_anchor`),
    };
  });
};

const parseScenarioIds = (behavior: JsonObject | null, path: string): string[] => {
  if (!behavior || behavior.scenarios === undefined || behavior.scenarios === null) return [];
  if (Array.isArray(behavior.scenarios)) {
    return behavior.scenarios.map((raw, index) => {
      if (typeof raw === "string") return requiredString(raw, `${path}.scenarios[${index}]`);
      const scenario = record(raw, `${path}.scenarios[${index}]`);
      return requiredString(
        scenario.scenario_id ?? scenario.id ?? scenario.name,
        `${path}.scenarios[${index}].scenario_id`,
      );
    });
  }
  const scenarios = record(behavior.scenarios, `${path}.scenarios`);
  return Object.keys(scenarios);
};

const parsePersonalityTraits = (value: unknown, path: string): string[] => {
  if (value === null || value === undefined) return [];
  if (Array.isArray(value)) return stringArray(value, path);
  const personality = record(value, path);
  if (Array.isArray(personality.traits)) return stringArray(personality.traits, `${path}.traits`);
  return Object.entries(personality).flatMap(([name, traitValue]) => {
    if (typeof traitValue === "number" || typeof traitValue === "boolean") {
      return [name.replaceAll("_", " ")];
    }
    if (typeof traitValue === "string" && (name === "voice_style" || name === "motion_style")) {
      return [traitValue.replaceAll("_", " ")];
    }
    return [];
  });
};

const parseSpec = (value: unknown, path: string): CharacterSpecView => {
  const spec = record(value, path);
  const identity = optionalRecord(spec.identity, `${path}.identity`) ?? {};
  const morphology = optionalRecord(spec.morphology, `${path}.morphology`);
  const behavior = optionalRecord(spec.behavior, `${path}.behavior`);
  const appearance = optionalRecord(spec.appearance, `${path}.appearance`) ?? {};
  const face = optionalRecord(spec.face, `${path}.face`) ?? {};
  const defaultExpression = optionalString(
    face.default_expression,
    `${path}.face.default_expression`,
  ) ?? "neutral";
  const supportedExpressions = stringArray(
    face.supported_expressions ?? [defaultExpression],
    `${path}.face.supported_expressions`,
  );
  return {
    name: optionalString(identity.name, `${path}.identity.name`) ?? "Untitled character",
    role: optionalString(identity.role, `${path}.identity.role`) ?? "Companion robot",
    motif: optionalString(identity.motif, `${path}.identity.motif`) ?? "Original character",
    designBrief: optionalString(identity.design_brief, `${path}.identity.design_brief`) ?? "No design brief yet.",
    hardwareProfileId: optionalString(spec.hardware_profile_id, `${path}.hardware_profile_id`),
    morphologyNodes: parseMorphologyNodes(morphology?.nodes, `${path}.morphology.nodes`),
    scenarioIds: parseScenarioIds(behavior, `${path}.behavior`),
    personalityTraits: parsePersonalityTraits(spec.personality, `${path}.personality`),
    appearance: {
      primaryColor: optionalString(appearance.primary_color, `${path}.appearance.primary_color`) ?? "#E8C44A",
      secondaryColor: optionalString(appearance.secondary_color, `${path}.appearance.secondary_color`) ?? "#F7E9AF",
      accentColor: optionalString(appearance.accent_color, `${path}.appearance.accent_color`) ?? "#EF7F1A",
      eyeColor: optionalString(appearance.eye_color, `${path}.appearance.eye_color`) ?? "#111111",
    },
    face: {
      defaultExpression,
      supportedExpressions: supportedExpressions.includes(defaultExpression)
        ? supportedExpressions
        : [defaultExpression, ...supportedExpressions],
    },
  };
};

const parseProfile = (value: unknown, path: string): HardwareProfileSummary => {
  const profile = record(value, path);
  const profileId = requiredString(profile.profile_id, `${path}.profile_id`);
  const enclosure = optionalRecord(profile.minimum_enclosure_mm, `${path}.minimum_enclosure_mm`);
  const controller = optionalString(profile.controller, `${path}.controller`);
  const componentCount = typeof profile.component_count === "number" ? profile.component_count : null;
  const components = profile.components === undefined
    ? [
      ...(controller ? [controller] : []),
      ...(componentCount === null ? [] : [`${componentCount} catalog components`]),
    ]
    : stringArray(profile.components, `${path}.components`);
  return {
    profileId,
    label: optionalString(profile.label ?? profile.display_name, `${path}.label`) ?? profileId,
    evidenceLevel: profileEvidenceLevel(
      profile.evidence_level ?? profile.qualification,
      `${path}.evidence_level`,
    ),
    controller,
    minimumEnclosureMm: enclosure
      ? {
        x: finiteNumber(enclosure.x, `${path}.minimum_enclosure_mm.x`),
        y: finiteNumber(enclosure.y, `${path}.minimum_enclosure_mm.y`),
        z: finiteNumber(enclosure.z, `${path}.minimum_enclosure_mm.z`),
      }
      : null,
    components,
    capabilities: stringArray(profile.capabilities, `${path}.capabilities`),
    unknowns: stringArray(profile.unknowns, `${path}.unknowns`),
  };
};

const parseValidationCheck = (value: unknown, path: string): ValidationCheck => {
  const check = record(value, path);
  const rawStatus = requiredString(check.status, `${path}.status`);
  if (!["passed", "info", "warning", "failed", "not_run"].includes(rawStatus)) {
    throw new StudioContractError(`${path}.status is not supported`);
  }
  const code = requiredString(check.code, `${path}.code`);
  return {
    code,
    label: optionalString(check.label, `${path}.label`) ?? code.replaceAll("_", " "),
    status: rawStatus as ValidationCheck["status"],
    message: optionalString(check.message, `${path}.message`),
    path: optionalString(check.path, `${path}.path`),
    measuredValue: optionalFiniteNumber(check.measured_value, `${path}.measured_value`),
    limitValue: optionalFiniteNumber(check.limit_value, `${path}.limit_value`),
    suggestion: optionalString(check.suggestion, `${path}.suggestion`),
  };
};

const parseValidation = (value: unknown, path: string): ValidationSummary | null => {
  const validation = optionalRecord(value, path);
  if (!validation) return null;
  const rawChecks = validation.checks ?? validation.issues ?? [];
  if (!Array.isArray(rawChecks)) throw new StudioContractError(`${path}.checks must be an array`);
  const isIssueReport = validation.checks === undefined && validation.issues !== undefined;
  const checks = isIssueReport
    ? rawChecks.map((raw, index): ValidationCheck => {
      const issue = record(raw, `${path}.issues[${index}]`);
      const severity = warningSeverity(issue.severity, `${path}.issues[${index}].severity`);
      const code = requiredString(issue.code, `${path}.issues[${index}].code`);
      return {
        code,
        label: code.replaceAll("_", " "),
        status: severity === "error" ? "failed" : severity,
        message: optionalString(issue.message, `${path}.issues[${index}].message`),
        path: optionalString(issue.path, `${path}.issues[${index}].path`),
        measuredValue: optionalFiniteNumber(
          issue.measured_value,
          `${path}.issues[${index}].measured_value`,
        ),
        limitValue: optionalFiniteNumber(issue.limit_value, `${path}.issues[${index}].limit_value`),
        suggestion: optionalString(issue.suggestion, `${path}.issues[${index}].suggestion`),
      };
    })
    : rawChecks.map((check, index) => parseValidationCheck(check, `${path}.checks[${index}]`));
  return {
    evidenceLevel: evidenceLevel(validation.evidence_level ?? "concept_only", `${path}.evidence_level`),
    checks,
    warnings: parseWarnings(validation.warnings, `${path}.warnings`),
  };
};

export function parseStudioContext(value: unknown): StudioContext {
  const context = record(value, "context");
  const rawProfiles = context.hardware_profiles ?? context.profiles ?? [];
  if (!Array.isArray(rawProfiles)) throw new StudioContractError("context.profiles must be an array");
  const preview = optionalRecord(context.preview, "context.preview") ?? {};
  const rawDraft = optionalRecord(context.draft, "context.draft");
  const rawCurrentSpec = optionalRecord(context.current_spec, "context.current_spec");
  const activeSpec = rawDraft?.spec ?? rawCurrentSpec;
  const activeSpecView = activeSpec ? parseSpec(activeSpec, "context.active_spec") : null;
  const rawArtifact = optionalRecord(
    rawDraft
      ? rawDraft.preview_artifact
      : (preview.preview_artifact ?? context.current_preview_artifact),
    "context.preview_artifact",
  );
  const artifactSha = optionalString(rawArtifact?.sha256, "context.preview_artifact.sha256");
  const previewWarnings = [
    ...parseWarnings(preview.warnings, "context.preview.warnings"),
    ...parseWarnings(rawDraft?.warnings, "context.draft.warnings"),
  ];

  return {
    schemaVersion: requiredString(context.schema_version, "context.schema_version"),
    projectId: optionalString(context.project_id, "context.project_id") ?? "character-robot-studio",
    projectGeneration: context.project_generation === undefined
      ? 0
      : finiteNumber(context.project_generation, "context.project_generation"),
    storageMode: context.storage_mode === "durable" ? "durable" : "ephemeral",
    artifactManifestCount: context.artifact_manifest_count === undefined
      ? 0
      : finiteNumber(context.artifact_manifest_count, "context.artifact_manifest_count"),
    headRevisionId: optionalString(context.head_revision_id, "context.head_revision_id"),
    headSpecSha256: optionalString(context.head_spec_sha256, "context.head_spec_sha256"),
    draft: rawDraft
      ? {
        spec: parseSpec(rawDraft.spec, "context.draft.spec"),
        draftHash: requiredString(rawDraft.draft_hash, "context.draft.draft_hash"),
        baseRevisionId: optionalString(rawDraft.base_revision_id, "context.draft.base_revision_id"),
      }
      : null,
    currentSpec: rawCurrentSpec ? parseSpec(rawCurrentSpec, "context.current_spec") : null,
    profiles: rawProfiles.map((profile, index) => parseProfile(profile, `context.profiles[${index}]`)),
    preview: {
      glbUrl: optionalString(preview.glb_url, "context.preview.glb_url")
        ?? (artifactSha ? `/api/studio/v1/artifacts/${artifactSha}` : null),
      partNames: preview.part_names === undefined
        ? activeSpecView?.morphologyNodes.map((node) => node.nodeId) ?? []
        : stringArray(preview.part_names, "context.preview.part_names"),
      compiledAt: optionalString(preview.compiled_at, "context.preview.compiled_at"),
      warnings: previewWarnings,
    },
    selectedNodeId: optionalString(context.selected_node_id, "context.selected_node_id"),
    latestValidation: parseValidation(context.latest_validation, "context.latest_validation"),
  };
}

const parseFrame = (value: unknown, path: string, useMilliseconds: boolean): ScenarioFrame => {
  const frame = record(value, path);
  const neck = optionalRecord(frame.neck, `${path}.neck`) ?? {};
  const face = optionalRecord(frame.face, `${path}.face`) ?? {};
  const rawTime = frame.time_s ?? frame.at_ms;
  return {
    timeS: finiteNumber(rawTime, `${path}.time_s`) / (useMilliseconds ? 1000 : 1),
    wheels: {
      leftCommand: wheelCommand(frame.wheel_left ?? 0, `${path}.wheel_left`),
      rightCommand: wheelCommand(frame.wheel_right ?? 0, `${path}.wheel_right`),
    },
    neck: {
      panDeg: finiteNumber(neck.pan_deg ?? frame.head_pan_deg ?? 0, `${path}.neck.pan_deg`),
      tiltDeg: finiteNumber(neck.tilt_deg ?? frame.head_tilt_deg ?? 0, `${path}.neck.tilt_deg`),
    },
    face: {
      expression: requiredString(face.expression ?? frame.face_expression ?? "neutral", `${path}.face.expression`),
    },
    soundCue: optionalString(frame.sound_cue, `${path}.sound_cue`),
  };
};

export function parseScenarioPreview(value: unknown): ScenarioPreview {
  const preview = record(value, "scenario_preview");
  const scenarioValue = preview.scenario_id ?? preview.scenario;
  const scenario = typeof scenarioValue === "string"
    ? scenarioValue
    : requiredString(record(scenarioValue, "scenario_preview.scenario").scenario_id, "scenario_preview.scenario.scenario_id");
  const useMilliseconds = preview.duration_s === undefined && preview.duration_ms !== undefined;
  const duration = finiteNumber(
    preview.duration_s ?? preview.duration_ms,
    "scenario_preview.duration_s",
  ) / (useMilliseconds ? 1000 : 1);
  if (duration < 0) throw new StudioContractError("scenario_preview.duration_s must be non-negative");
  if (!Array.isArray(preview.keyframes) || preview.keyframes.length === 0) {
    throw new StudioContractError("scenario_preview.keyframes must contain at least one frame");
  }
  const keyframes = preview.keyframes.map((frame, index) => (
    parseFrame(frame, `scenario_preview.keyframes[${index}]`, useMilliseconds)
  ));
  keyframes.forEach((frame, index) => {
    if (frame.timeS < 0 || frame.timeS > duration) {
      throw new StudioContractError(`scenario_preview.keyframes[${index}] is outside the scenario duration`);
    }
    if (index > 0 && frame.timeS < keyframes[index - 1].timeS) {
      throw new StudioContractError("scenario_preview.keyframes must be sorted by time");
    }
  });
  return {
    scenarioId: requiredString(scenario, "scenario_preview.scenario_id"),
    durationS: duration,
    evidenceLevel: evidenceLevel(preview.evidence_level ?? "concept_only", "scenario_preview.evidence_level"),
    keyframes,
  };
}

const parseApiError = (body: unknown, response: Response): StudioApiError => {
  const envelope = isRecord(body) ? body : {};
  const rawError = isRecord(envelope.error) ? envelope.error : {};
  const code = typeof rawError.code === "string" ? rawError.code : "STUDIO_REQUEST_FAILED";
  const message = typeof rawError.message === "string" ? rawError.message : response.statusText || "Studio request failed";
  const retryable = rawError.retryable === true;
  const nextAction = typeof rawError.next_action === "string" ? rawError.next_action : null;
  return new StudioApiError(code, message, response.status, retryable, nextAction);
};

export async function callStudioTool(name: string, input: JsonObject): Promise<unknown> {
  const response = await fetch(`/api/studio/v1/tools/${encodeURIComponent(name)}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new StudioApiError("STUDIO_RESPONSE_INVALID", "Studio returned invalid JSON", response.status);
  }
  if (!response.ok || (isRecord(body) && body.ok === false)) throw parseApiError(body, response);
  if (isRecord(body) && body.ok === true && "result" in body) return body.result;
  return body;
}

export async function getStudioContext(): Promise<StudioContext> {
  const response = await fetch("/api/studio/v1/context", { credentials: "same-origin" });
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new StudioApiError("STUDIO_RESPONSE_INVALID", "Studio returned invalid JSON", response.status);
  }
  if (!response.ok) throw parseApiError(body, response);
  return parseStudioContext(body);
}

export async function setStudioSelection(input: {
  target: DesignTarget;
  node_id: string | null;
}): Promise<string | null> {
  const response = await fetch("/api/studio/v1/selection", {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
  });
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new StudioApiError("STUDIO_RESPONSE_INVALID", "Studio returned invalid JSON", response.status);
  }
  if (!response.ok) throw parseApiError(body, response);
  const result = record(body, "selection");
  return optionalString(result.selected_node_id, "selection.selected_node_id");
}

export async function getScenarioPreview(input: JsonObject): Promise<ScenarioPreview> {
  return parseScenarioPreview(await callStudioTool("preview_scenario", input));
}

const parseBuildPackArtifact = (value: unknown, path: string): BuildPackArtifact => {
  const artifact = record(value, path);
  const sha256 = requiredString(artifact.sha256, `${path}.sha256`);
  if (!/^[0-9a-f]{64}$/.test(sha256)) {
    throw new StudioContractError(`${path}.sha256 must be a lowercase SHA-256`);
  }
  const byteSize = finiteNumber(artifact.byte_size, `${path}.byte_size`);
  if (!Number.isInteger(byteSize) || byteSize < 0) {
    throw new StudioContractError(`${path}.byte_size must be a non-negative integer`);
  }
  if (typeof artifact.experimental !== "boolean") {
    throw new StudioContractError(`${path}.experimental must be a boolean`);
  }
  return {
    kind: requiredString(artifact.kind, `${path}.kind`),
    fileName: requiredString(artifact.file_name, `${path}.file_name`),
    mediaType: requiredString(artifact.media_type, `${path}.media_type`),
    sha256,
    byteSize,
    experimental: artifact.experimental,
    downloadUrl: `/api/studio/v1/artifacts/${sha256}`,
  };
};

const sha256 = (value: unknown, path: string): string => {
  const digest = requiredString(value, path);
  if (!/^[0-9a-f]{64}$/.test(digest)) {
    throw new StudioContractError(`${path} must be a lowercase SHA-256`);
  }
  return digest;
};

const parseBuildPackManifest = (value: unknown, path: string): BuildPackManifest => {
  const manifest = record(value, path);
  if (manifest.download_requires_human_action !== true) {
    throw new StudioContractError(`${path}.download_requires_human_action must be true`);
  }
  return {
    revisionId: requiredString(manifest.revision_id, `${path}.revision_id`),
    specHash: sha256(manifest.spec_hash, `${path}.spec_hash`),
    buildSubjectHash: sha256(manifest.build_subject_hash, `${path}.build_subject_hash`),
    geometrySha256: sha256(manifest.geometry_sha256, `${path}.geometry_sha256`),
    profileId: requiredString(manifest.profile_id, `${path}.profile_id`),
    profileSha256: sha256(manifest.profile_sha256, `${path}.profile_sha256`),
    catalogVersion: requiredString(manifest.catalog_version, `${path}.catalog_version`),
    compilerVersion: requiredString(manifest.compiler_version, `${path}.compiler_version`),
    cadEngineVersion: requiredString(manifest.cad_engine_version, `${path}.cad_engine_version`),
    simulationEngineVersion: optionalString(
      manifest.simulation_engine_version,
      `${path}.simulation_engine_version`,
    ),
    firmwareRuntimeVersion: requiredString(
      manifest.firmware_runtime_version,
      `${path}.firmware_runtime_version`,
    ),
    evidenceLevel: evidenceLevel(manifest.evidence_level, `${path}.evidence_level`),
    manifestHash: sha256(manifest.manifest_hash, `${path}.manifest_hash`),
    downloadRequiresHumanAction: true,
  };
};

export function parseBuildPackResult(value: unknown): BuildPackResult {
  const result = record(value, "build_pack");
  if (result.status !== "blocked" && result.status !== "experimental_ready" && result.status !== "ready") {
    throw new StudioContractError("build_pack.status is not supported");
  }
  if (result.human_action_required !== true) {
    throw new StudioContractError("build_pack.human_action_required must be true");
  }
  const manifest = optionalRecord(result.manifest, "build_pack.manifest");
  if ((result.status === "experimental_ready" || result.status === "ready") && !manifest) {
    throw new StudioContractError("build_pack.manifest is required when the pack is ready");
  }
  const rawArtifacts = manifest?.artifacts ?? [];
  if (!Array.isArray(rawArtifacts)) {
    throw new StudioContractError("build_pack.manifest.artifacts must be an array");
  }
  const rawBlockers = result.blockers ?? [];
  if (!Array.isArray(rawBlockers)) throw new StudioContractError("build_pack.blockers must be an array");
  return {
    status: result.status,
    manifest: manifest ? parseBuildPackManifest(manifest, "build_pack.manifest") : null,
    artifacts: rawArtifacts.map((artifact, index) => (
      parseBuildPackArtifact(artifact, `build_pack.manifest.artifacts[${index}]`)
    )),
    blockers: rawBlockers.map((blocker, index) => parseWarning(blocker, `build_pack.blockers[${index}]`)),
    nextAction: requiredString(result.next_action, "build_pack.next_action"),
    humanActionRequired: true,
  };
}

export async function prepareStudioBuildPack(input: JsonObject): Promise<BuildPackResult> {
  return parseBuildPackResult(await callStudioTool("prepare_build_pack", input));
}

export async function importStudioProject(file: Blob, expectedGeneration: number): Promise<void> {
  const response = await fetch("/api/studio/v1/project-import", {
    method: "POST",
    credentials: "same-origin",
    headers: {
      "content-type": "application/json",
      "x-character-project-generation": String(expectedGeneration),
    },
    body: file,
  });
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new StudioApiError("STUDIO_RESPONSE_INVALID", "Studio returned invalid JSON", response.status);
  }
  if (!response.ok) throw parseApiError(body, response);
  if (!isRecord(body) || body.imported !== true) {
    throw new StudioContractError("project import confirmation is invalid");
  }
}
