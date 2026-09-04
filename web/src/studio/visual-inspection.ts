import {
  CANONICAL_VIEWS,
  VISUAL_RENDER_SIZE_PX,
  type CanonicalViewName,
  type StudioCanonicalCapture,
} from "./viewer";
import {
  parseCharacterSpecView,
  StudioContractError,
  type JsonObject,
} from "./api";
import type { CharacterSpecView, DesignTarget } from "./types";

export { CANONICAL_VIEWS, VISUAL_RENDER_SIZE_PX } from "./viewer";
export { canonicalCameraPosition } from "./viewer";
export type { CanonicalViewName, StudioCanonicalCapture } from "./viewer";

export const VISUAL_RENDER_CONTRACT_VERSION = "studio-render-v1";
const MAXIMUM_GLB_BYTES = 16 * 1024 * 1024;
export const MAXIMUM_CANONICAL_IMAGE_BYTES = 512 * 1024;
export const MAXIMUM_CANONICAL_IMAGE_TOTAL_BYTES = 2 * 1024 * 1024;

export type VisualInspectionDiagnostic = {
  code: string;
  severity: "info" | "warning" | "error";
  message: string;
  nodeId?: string;
  view?: CanonicalViewName;
  suggestion?: string;
};

/**
 * The data URL is an in-memory bridge between the viewer and the shared page.
 * It is converted to an MCP image block before leaving the browser.
 */
export type VisualInspectionView = {
  view: CanonicalViewName;
  label: string;
  widthPx: number;
  heightPx: number;
  cameraDirection: [number, number, number];
  dataUrl: string;
};

export type VisualInspectionNode = {
  nodeId: string;
  role: string;
  expectedInPreview: boolean;
  rendered: boolean;
  structurallyVisible: boolean;
};

export type StudioVisualInspection = {
  status: "ready" | "unavailable";
  code?: string;
  message?: string;
  renderContractVersion: string;
  source?: {
    target: DesignTarget;
    specHash: string;
    geometrySha256: string;
    glbSha256: string;
  };
  views: VisualInspectionView[];
  nodes: VisualInspectionNode[];
  diagnostics: VisualInspectionDiagnostic[];
};

export type ParsedInspectSource = {
  target: DesignTarget;
  specHash: string;
  geometrySha256: string;
  glbSha256: string;
  glbByteSize: number;
  spec: CharacterSpecView;
  partRoles: Map<string, string>;
};

export type StudioCanonicalCaptureRunner = (
  source: Pick<ParsedInspectSource, "target" | "specHash" | "geometrySha256" | "glbSha256" | "glbByteSize">,
) => Promise<StudioCanonicalCapture>;

export class VisualInspectionCaptureError extends Error {
  readonly code: string;

  constructor(code: string, message: string) {
    super(message);
    this.name = "VisualInspectionCaptureError";
    this.code = code;
  }
}

const isRecord = (value: unknown): value is JsonObject => (
  typeof value === "object" && value !== null && !Array.isArray(value)
);

const record = (value: unknown, path: string): JsonObject => {
  if (!isRecord(value)) throw new StudioContractError(`${path} must be an object`);
  return value;
};

const requiredString = (value: unknown, path: string): string => {
  if (typeof value !== "string" || value.length === 0) {
    throw new StudioContractError(`${path} must be a non-empty string`);
  }
  return value;
};

const digest = (value: unknown, path: string): string => {
  const result = requiredString(value, path);
  if (!/^[0-9a-f]{64}$/.test(result)) {
    throw new StudioContractError(`${path} must be a lowercase SHA-256`);
  }
  return result;
};

const designTarget = (value: unknown, path: string): DesignTarget => {
  const target = record(value, path);
  if (target.kind === "draft") {
    return { kind: "draft", draft_hash: digest(target.draft_hash, `${path}.draft_hash`) };
  }
  if (target.kind === "revision") {
    return {
      kind: "revision",
      revision_id: requiredString(target.revision_id, `${path}.revision_id`),
    };
  }
  throw new StudioContractError(`${path}.kind is not supported`);
};

export const designTargetsEqual = (left: DesignTarget, right: DesignTarget): boolean => (
  left.kind === right.kind
  && (left.kind === "draft"
    ? right.kind === "draft" && left.draft_hash === right.draft_hash
    : right.kind === "revision" && left.revision_id === right.revision_id)
);

export function parseInspectVisualSource(
  input: JsonObject,
  value: JsonObject,
): ParsedInspectSource {
  const requestedTarget = designTarget(input.target, "inspect_design.input.target");
  const returnedTarget = designTarget(value.target, "inspect_design.target");
  if (!designTargetsEqual(requestedTarget, returnedTarget)) {
    throw new StudioContractError("inspect_design returned a different design target");
  }
  const artifact = record(value.preview_artifact, "inspect_design.preview_artifact");
  if (artifact.kind !== "glb" || artifact.media_type !== "model/gltf-binary") {
    throw new StudioContractError("inspect_design.preview_artifact must be a GLB");
  }
  const byteSize = artifact.byte_size;
  if (
    typeof byteSize !== "number"
    || !Number.isInteger(byteSize)
    || byteSize <= 0
    || byteSize > MAXIMUM_GLB_BYTES
  ) {
    throw new StudioContractError("inspect_design.preview_artifact has an invalid byte size");
  }
  const partRoles = new Map<string, string>();
  if (
    !Array.isArray(value.compiled_parts)
    || value.compiled_parts.length < 1
    || value.compiled_parts.length > 256
  ) {
    throw new StudioContractError("inspect_design.compiled_parts must contain between 1 and 256 parts");
  }
  value.compiled_parts.forEach((item, index) => {
    const part = record(item, `inspect_design.compiled_parts[${index}]`);
    const name = requiredString(part.name, `inspect_design.compiled_parts[${index}].name`);
    if (partRoles.has(name)) {
      throw new StudioContractError("inspect_design.compiled_parts contains duplicate names");
    }
    partRoles.set(
      name,
      requiredString(part.role, `inspect_design.compiled_parts[${index}].role`),
    );
  });
  return {
    target: returnedTarget,
    specHash: digest(value.spec_hash, "inspect_design.spec_hash"),
    geometrySha256: digest(value.geometry_sha256, "inspect_design.geometry_sha256"),
    glbSha256: digest(artifact.sha256, "inspect_design.preview_artifact.sha256"),
    glbByteSize: byteSize,
    spec: parseCharacterSpecView(value.spec, "inspect_design.spec"),
    partRoles,
  };
}

const unavailable = (code: string, message: string): StudioVisualInspection => ({
  status: "unavailable",
  code,
  message,
  renderContractVersion: VISUAL_RENDER_CONTRACT_VERSION,
  views: [],
  nodes: [],
  diagnostics: [{ code, severity: "warning", message }],
});

const PNG_SIGNATURE = [137, 80, 78, 71, 13, 10, 26, 10];

const decodePngDataUrl = (
  dataUrl: string,
  path: string,
): { encoded: string; bytes: Uint8Array } => {
  const match = /^data:image\/png;base64,([A-Za-z0-9+/]+={0,2})$/.exec(dataUrl);
  if (!match || match[1].length % 4 !== 0) {
    throw new StudioContractError(`${path} must be a base64 PNG data URL`);
  }
  let binary: string;
  try {
    binary = atob(match[1]);
  } catch {
    throw new StudioContractError(`${path} contains invalid base64 PNG data`);
  }
  const bytes = Uint8Array.from(binary, (character) => character.charCodeAt(0));
  if (bytes.byteLength > MAXIMUM_CANONICAL_IMAGE_BYTES) {
    throw new StudioContractError(`${path} exceeds the canonical image size limit`);
  }
  if (bytes.byteLength < 24 || !PNG_SIGNATURE.every((value, index) => bytes[index] === value)) {
    throw new StudioContractError(`${path} is not a decodable PNG`);
  }
  if (
    bytes[12] !== 73
    || bytes[13] !== 72
    || bytes[14] !== 68
    || bytes[15] !== 82
  ) {
    throw new StudioContractError(`${path} is missing a PNG header`);
  }
  const dataView = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const width = dataView.getUint32(16);
  const height = dataView.getUint32(20);
  if (width !== VISUAL_RENDER_SIZE_PX || height !== VISUAL_RENDER_SIZE_PX) {
    throw new StudioContractError(`${path} does not match the bounded canonical dimensions`);
  }
  return { encoded: match[1], bytes };
};

const validateCapture = (capture: StudioCanonicalCapture): void => {
  if (typeof capture.previewUrl !== "string" || capture.previewUrl.length === 0) {
    throw new StudioContractError("canonical capture is not bound to a loaded preview URL");
  }
  if (capture.views.length !== CANONICAL_VIEWS.length) {
    throw new StudioContractError("canonical capture must contain exactly four views");
  }
  let totalBytes = 0;
  capture.views.forEach((view, index) => {
    const definition = CANONICAL_VIEWS[index];
    if (
      view.view !== definition.view
      || view.label !== definition.label
      || view.widthPx !== VISUAL_RENDER_SIZE_PX
      || view.heightPx !== VISUAL_RENDER_SIZE_PX
      || view.cameraDirection.length !== definition.direction.length
      || view.cameraDirection.some((value, directionIndex) => value !== definition.direction[directionIndex])
    ) {
      throw new StudioContractError("canonical capture contains an unexpected view definition");
    }
    const decoded = decodePngDataUrl(view.dataUrl, `canonical capture ${view.view}`);
    totalBytes += decoded.bytes.byteLength;
  });
  if (totalBytes > MAXIMUM_CANONICAL_IMAGE_TOTAL_BYTES) {
    throw new StudioContractError("canonical capture exceeds the total image size limit");
  }
  [capture.renderedNodeIds, capture.structurallyVisibleNodeIds].forEach((ids) => {
    if (!Array.isArray(ids) || ids.some((id) => typeof id !== "string" || id.length === 0)) {
      throw new StudioContractError("canonical capture contains invalid semantic node metadata");
    }
  });
  if (typeof capture.faceDisplayAvailable !== "boolean") {
    throw new StudioContractError("canonical capture contains invalid face-display metadata");
  }
};

export const semanticInspectionDiagnostics = (
  nodes: readonly VisualInspectionNode[],
): VisualInspectionDiagnostic[] => {
  const diagnostics: VisualInspectionDiagnostic[] = [];
  nodes.filter((node) => node.expectedInPreview && !node.rendered).forEach((node) => {
    diagnostics.push({
      code: "SEMANTIC_NODE_MISSING",
      severity: "error",
      nodeId: node.nodeId,
      message: `Semantic node ${node.nodeId} is absent from the compiler GLB.`,
      suggestion: "Repair the compiler output or provide an explicit compiler omission reason.",
    });
  });
  nodes.filter((node) => (
    node.expectedInPreview
    && node.rendered
    && !node.structurallyVisible
    && node.role !== "internal_mount"
  )).forEach((node) => {
    diagnostics.push({
      code: "SEMANTIC_NODE_NOT_VISIBLE",
      severity: "warning",
      nodeId: node.nodeId,
      message: `Semantic node ${node.nodeId} is hidden in the loaded compiler model.`,
      suggestion: "Inspect attachment placement or visibility flags before making a visual judgment.",
    });
  });
  return diagnostics;
};

export const faceDisplayInspectionDiagnostic = (
  faceDisplayAvailable: boolean,
): VisualInspectionDiagnostic | null => {
  if (faceDisplayAvailable) return null;
  return {
    code: "FACE_DISPLAY_MISSING",
    severity: "error",
    message: "The runtime face decorator could not be placed on a semantic surface.",
    suggestion: "Add a non-degenerate face bezel or head surface.",
  };
};

export function buildVisualInspection(
  source: ParsedInspectSource,
  capture: StudioCanonicalCapture,
): StudioVisualInspection {
  validateCapture(capture);
  const expectedIds = new Set(
    source.spec.morphologyNodes
      .filter((node) => node.visible !== false)
      .map((node) => node.nodeId),
  );
  const renderedIds = new Set(capture.renderedNodeIds);
  const structurallyVisibleIds = new Set(capture.structurallyVisibleNodeIds);
  const nodes = source.spec.morphologyNodes.map((node) => ({
    nodeId: node.nodeId,
    role: node.role,
    expectedInPreview: expectedIds.has(node.nodeId),
    rendered: renderedIds.has(node.nodeId),
    structurallyVisible: structurallyVisibleIds.has(node.nodeId),
  }));
  const diagnostics = semanticInspectionDiagnostics(nodes);
  const faceDiagnostic = faceDisplayInspectionDiagnostic(capture.faceDisplayAvailable);
  if (faceDiagnostic) diagnostics.push(faceDiagnostic);
  const diagnosticPartCount = [...source.partRoles.values()]
    .filter((role) => role === "hardware_keepout").length;
  if (diagnosticPartCount > 0) {
    diagnostics.push({
      code: "DIAGNOSTIC_GEOMETRY_EXCLUDED",
      severity: "info",
      message: `${diagnosticPartCount} hardware keepout ${diagnosticPartCount === 1 ? "part was" : "parts were"} excluded from character views.`,
    });
  }
  return {
    status: "ready",
    renderContractVersion: VISUAL_RENDER_CONTRACT_VERSION,
    source: {
      target: source.target,
      specHash: source.specHash,
      geometrySha256: source.geometrySha256,
      glbSha256: source.glbSha256,
    },
    views: capture.views,
    nodes,
    diagnostics,
  };
}

export async function inspectDesignVisuals(
  input: JsonObject,
  value: JsonObject,
  capture: StudioCanonicalCaptureRunner,
): Promise<StudioVisualInspection> {
  let source: ParsedInspectSource;
  try {
    source = parseInspectVisualSource(input, value);
  } catch (error) {
    return unavailable(
      "VISUAL_SOURCE_UNAVAILABLE",
      error instanceof Error ? error.message : "The visual source contract is unavailable.",
    );
  }
  try {
    return buildVisualInspection(source, await capture(source));
  } catch (error) {
    return unavailable(
      error instanceof VisualInspectionCaptureError ? error.code : "VISUAL_RENDER_FAILED",
      error instanceof Error ? error.message : "Canonical rendering failed.",
    );
  }
}

type ImageContentBlock = {
  type: "image";
  data: string;
  mimeType: "image/png";
};

const imageContentBlock = (view: VisualInspectionView): ImageContentBlock => ({
  type: "image",
  data: decodePngDataUrl(view.dataUrl, `canonical view ${view.view}`).encoded,
  mimeType: "image/png",
});

export function visualInspectionToolResult(inspection: StudioVisualInspection): JsonObject {
  const content = inspection.status === "ready"
    ? [
        {
          type: "text",
          text: "Four fixed canonical PNG views are attached. Visual identity remains a model/human judgment; these images are not manufacturing evidence.",
        },
        ...inspection.views.map(imageContentBlock),
      ]
    : [{
        type: "text",
        text: inspection.message ?? "Canonical views are unavailable.",
      }];
  return {
    status: inspection.status,
    render_contract_version: inspection.renderContractVersion,
    content,
    ...(inspection.code ? { code: inspection.code } : {}),
    ...(inspection.message ? { message: inspection.message } : {}),
    ...(inspection.source ? {
      source: {
        target: inspection.source.target,
        spec_hash: inspection.source.specHash,
        geometry_sha256: inspection.source.geometrySha256,
        glb_sha256: inspection.source.glbSha256,
      },
    } : {}),
    views: inspection.views.map((view) => ({
      view: view.view,
      label: view.label,
      width_px: view.widthPx,
      height_px: view.heightPx,
      camera_direction: view.cameraDirection,
      observation: "Compare the attached canonical image with the requested motif and design brief.",
    })),
    nodes: inspection.nodes.map((node) => ({
      node_id: node.nodeId,
      role: node.role,
      expected_in_preview: node.expectedInPreview,
      rendered: node.rendered,
      structurally_visible: node.structurallyVisible,
    })),
    diagnostics: inspection.diagnostics.map((diagnostic) => ({
      code: diagnostic.code,
      severity: diagnostic.severity,
      message: diagnostic.message,
      ...(diagnostic.nodeId ? { node_id: diagnostic.nodeId } : {}),
      ...(diagnostic.view ? { view: diagnostic.view } : {}),
      ...(diagnostic.suggestion ? { suggestion: diagnostic.suggestion } : {}),
    })),
    requires_visual_judgment: true,
    affects_manufacturing_evidence: false,
  };
}
