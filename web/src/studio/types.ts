export const EVIDENCE_LEVELS = [
  "concept_only",
  "digital_checks_passed",
  "within_qualified_profile",
  "exact_build_verified",
] as const;

export type EvidenceLevel = (typeof EVIDENCE_LEVELS)[number];

export type WarningSeverity = "info" | "warning" | "error";

export type StudioWarning = {
  code: string;
  message: string;
  severity: WarningSeverity;
  path?: string | null;
  measuredValue?: number | null;
  limitValue?: number | null;
  suggestion?: string | null;
};

export type HardwareProfileSummary = {
  profileId: string;
  label: string;
  evidenceLevel: EvidenceLevel;
  controller: string | null;
  minimumEnclosureMm: { x: number; y: number; z: number } | null;
  components: string[];
  capabilities: string[];
  unknowns: string[];
};

export type MorphologyNodeSummary = {
  nodeId: string;
  role: string;
  label: string;
  parentNodeId: string | null;
  parentAnchor: string | null;
  visible?: boolean;
};

export type CharacterAppearanceView = {
  primaryColor: string;
  secondaryColor: string;
  accentColor: string;
  eyeColor: string;
};

export type CharacterFaceView = {
  defaultExpression: string;
  supportedExpressions: string[];
};

export type CharacterSpecView = {
  name: string;
  role: string;
  motif: string;
  designBrief: string;
  hardwareProfileId: string | null;
  morphologyNodes: MorphologyNodeSummary[];
  scenarioIds: string[];
  personalityTraits: string[];
  appearance: CharacterAppearanceView;
  face: CharacterFaceView;
};

export type ValidationCheck = {
  code: string;
  label: string;
  status: "passed" | "info" | "warning" | "failed" | "not_run";
  message: string | null;
  path: string | null;
  measuredValue: number | null;
  limitValue: number | null;
  suggestion: string | null;
};

export type ValidationSummary = {
  evidenceLevel: EvidenceLevel;
  checks: ValidationCheck[];
  warnings: StudioWarning[];
};

export type StudioPreview = {
  glbUrl: string | null;
  partNames: string[];
  compiledAt: string | null;
  warnings: StudioWarning[];
};

export type StudioDraft = {
  spec: CharacterSpecView;
  draftHash: string;
  specHash: string;
  baseRevisionId: string | null;
};

export type StudioContext = {
  schemaVersion: string;
  projectId: string;
  projectGeneration: number;
  storageMode: "ephemeral" | "durable";
  artifactManifestCount: number;
  headRevisionId: string | null;
  headSpecSha256: string | null;
  draft: StudioDraft | null;
  currentSpec: CharacterSpecView | null;
  profiles: HardwareProfileSummary[];
  preview: StudioPreview;
  selectedNodeId: string | null;
  latestValidation: ValidationSummary | null;
};

export type ScenarioFrame = {
  timeS: number;
  wheels: {
    leftCommand: number;
    rightCommand: number;
  };
  neck: {
    panDeg: number;
    tiltDeg: number;
  };
  face: {
    expression: string;
  };
  soundCue: string | null;
};

export type ScenarioPreview = {
  scenarioId: string;
  specHash: string;
  durationS: number;
  evidenceLevel: EvidenceLevel;
  keyframes: ScenarioFrame[];
};

export type ScenarioPlaybackFrame = ScenarioFrame & {
  progress: number;
};

export type BuildPackArtifact = {
  kind: string;
  fileName: string;
  mediaType: string;
  sha256: string;
  byteSize: number;
  experimental: boolean;
  downloadUrl: string;
};

export type BuildPackResult = {
  status: "blocked" | "experimental_ready" | "ready";
  manifest: BuildPackManifest | null;
  artifacts: BuildPackArtifact[];
  blockers: StudioWarning[];
  nextAction: string;
  humanActionRequired: true;
};

export type BuildPackManifest = {
  revisionId: string;
  specHash: string;
  buildSubjectHash: string;
  geometrySha256: string;
  profileId: string;
  profileSha256: string;
  catalogVersion: string;
  compilerVersion: string;
  cadEngineVersion: string;
  simulationEngineVersion: string | null;
  firmwareRuntimeVersion: string;
  evidenceLevel: EvidenceLevel;
  manifestHash: string;
  downloadRequiresHumanAction: true;
};

export type DraftDesignTarget = {
  kind: "draft";
  draft_hash: string;
};

export type RevisionDesignTarget = {
  kind: "revision";
  revision_id: string;
};

export type DesignTarget = DraftDesignTarget | RevisionDesignTarget;
