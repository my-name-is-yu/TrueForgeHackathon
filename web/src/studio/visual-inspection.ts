import * as THREE from "three";
import { GLTFLoader } from "three/addons/loaders/GLTFLoader.js";

import {
  parseCharacterSpecView,
  StudioContractError,
  type JsonObject,
} from "./api";
import type { CharacterSpecView, DesignTarget } from "./types";
import { hideDiagnosticGeometry, rigStudioModel } from "./viewer";

export const VISUAL_RENDER_CONTRACT_VERSION = "studio-render-v1";
export const VISUAL_RENDER_SIZE_PX = 384;
const MAXIMUM_GLB_BYTES = 16 * 1024 * 1024;
const VISIBILITY_GRID_SIZE = 28;

export const CANONICAL_VIEWS = [
  { view: "front", label: "Front", direction: [0, 0, 1] },
  { view: "three_quarter", label: "Three-quarter", direction: [1, 0.35, 1] },
  { view: "side", label: "Side", direction: [1, 0, 0] },
  { view: "back", label: "Back", direction: [0, 0, -1] },
] as const;

export type CanonicalViewName = (typeof CANONICAL_VIEWS)[number]["view"];

export type VisualInspectionDiagnostic = {
  code: string;
  severity: "info" | "warning" | "error";
  message: string;
  nodeId?: string;
  view?: CanonicalViewName;
  suggestion?: string;
};

export type VisualInspectionView = {
  view: CanonicalViewName;
  label: string;
  widthPx: number;
  heightPx: number;
  cameraDirection: [number, number, number];
  pngSha256: string;
  dataUrl: string;
  foregroundSamples: number;
  faceDisplaySamples: number;
  visibleNodeIds: string[];
};

export type VisualInspectionNode = {
  nodeId: string;
  role: string;
  expectedInPreview: boolean;
  rendered: boolean;
  visibleViews: CanonicalViewName[];
  sampledVisiblePixels: number;
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

type ParsedInspectSource = {
  target: DesignTarget;
  specHash: string;
  geometrySha256: string;
  glbSha256: string;
  glbByteSize: number;
  spec: CharacterSpecView;
  partRoles: Map<string, string>;
};

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

const bytesSha256 = async (bytes: ArrayBuffer): Promise<string> => {
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(hash)].map((item) => item.toString(16).padStart(2, "0")).join("");
};

const dataUrlSha256 = async (dataUrl: string): Promise<string> => {
  const encoded = dataUrl.split(",", 2)[1];
  if (!encoded) throw new StudioContractError("canonical render did not produce PNG data");
  const binary = atob(encoded);
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytesSha256(bytes.buffer);
};

export const readBoundedArtifactBytes = async (
  response: Response,
  expectedBytes: number,
): Promise<ArrayBuffer> => {
  const contentLengthValue = response.headers.get("content-length");
  if (contentLengthValue !== null) {
    const contentLength = Number(contentLengthValue);
    if (!Number.isSafeInteger(contentLength) || contentLength < 0 || contentLength > MAXIMUM_GLB_BYTES) {
      throw new StudioContractError("The compiler GLB Content-Length is invalid or too large.");
    }
  }
  if (!response.body) {
    throw new StudioContractError("The compiler GLB response cannot be read with a bounded stream.");
  }

  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let receivedBytes = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      receivedBytes += value.byteLength;
      if (receivedBytes > expectedBytes || receivedBytes > MAXIMUM_GLB_BYTES) {
        await reader.cancel("compiler GLB exceeded its declared byte size");
        throw new StudioContractError("The compiler GLB exceeded its declared byte size.");
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  if (receivedBytes !== expectedBytes) {
    throw new StudioContractError("The compiler GLB byte size changed.");
  }
  const result = new Uint8Array(receivedBytes);
  let offset = 0;
  chunks.forEach((chunk) => {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  });
  return result.buffer;
};

const loadGltf = async (bytes: ArrayBuffer): Promise<THREE.Object3D> => (
  new Promise((resolve, reject) => {
    new GLTFLoader().parse(
      bytes,
      "",
      (gltf) => resolve(gltf.scene),
      (error) => reject(error instanceof Error ? error : new Error("GLB parsing failed")),
    );
  })
);

const disposeObject = (object: THREE.Object3D): void => {
  const textures = new Set<THREE.Texture>();
  object.traverse((child) => {
    if (!(child instanceof THREE.Mesh)) return;
    child.geometry.dispose();
    const materials = Array.isArray(child.material) ? child.material : [child.material];
    materials.forEach((material) => {
      Object.values(material).forEach((value) => {
        if (value instanceof THREE.Texture) textures.add(value);
      });
      material.dispose();
    });
  });
  textures.forEach((texture) => texture.dispose());
};

const visibleWorldBounds = (root: THREE.Object3D): THREE.Box3 => {
  root.updateWorldMatrix(true, true);
  const bounds = new THREE.Box3();
  const visit = (object: THREE.Object3D): void => {
    if (!object.visible) return;
    if (object instanceof THREE.Mesh) {
      object.geometry.computeBoundingBox();
      if (object.geometry.boundingBox && !object.geometry.boundingBox.isEmpty()) {
        bounds.union(object.geometry.boundingBox.clone().applyMatrix4(object.matrixWorld));
      }
    }
    object.children.forEach(visit);
  };
  visit(root);
  return bounds;
};

export const canonicalCameraPosition = (
  center: THREE.Vector3,
  radius: number,
  direction: readonly [number, number, number],
): THREE.Vector3 => (
  center.clone().add(new THREE.Vector3(...direction).normalize().multiplyScalar(radius * 4))
);

const configureCamera = (
  camera: THREE.OrthographicCamera,
  center: THREE.Vector3,
  radius: number,
  direction: readonly [number, number, number],
): void => {
  const extent = Math.max(radius * 1.18, 0.01);
  camera.left = -extent;
  camera.right = extent;
  camera.top = extent;
  camera.bottom = -extent;
  camera.near = Math.max(radius * 0.01, 0.0001);
  camera.far = Math.max(radius * 10, 1);
  camera.position.copy(canonicalCameraPosition(center, radius, direction));
  camera.up.set(0, 1, 0);
  camera.lookAt(center);
  camera.updateProjectionMatrix();
  camera.updateMatrixWorld(true);
};

const isActuallyVisible = (object: THREE.Object3D, root: THREE.Object3D): boolean => {
  let candidate: THREE.Object3D | null = object;
  while (candidate) {
    if (!candidate.visible) return false;
    if (candidate === root) return true;
    candidate = candidate.parent;
  }
  return false;
};

const semanticOwner = (
  object: THREE.Object3D,
  root: THREE.Object3D,
  semanticIds: ReadonlySet<string>,
): string | null => {
  let candidate: THREE.Object3D | null = object;
  while (candidate) {
    const metadataId = candidate.userData.node_id;
    if (typeof metadataId === "string" && semanticIds.has(metadataId)) return metadataId;
    if (semanticIds.has(candidate.name)) return candidate.name;
    if (candidate === root) break;
    candidate = candidate.parent;
  }
  return null;
};

const sampleVisibility = (
  model: THREE.Object3D,
  camera: THREE.Camera,
  semanticIds: ReadonlySet<string>,
): {
  foregroundSamples: number;
  faceDisplaySamples: number;
  nodeSamples: Map<string, number>;
} => {
  const raycaster = new THREE.Raycaster();
  const pointer = new THREE.Vector2();
  const nodeSamples = new Map<string, number>();
  let foregroundSamples = 0;
  let faceDisplaySamples = 0;
  for (let row = 0; row < VISIBILITY_GRID_SIZE; row += 1) {
    for (let column = 0; column < VISIBILITY_GRID_SIZE; column += 1) {
      pointer.set(
        ((column + 0.5) / VISIBILITY_GRID_SIZE) * 2 - 1,
        -(((row + 0.5) / VISIBILITY_GRID_SIZE) * 2 - 1),
      );
      raycaster.setFromCamera(pointer, camera);
      const hit = raycaster.intersectObject(model, true)
        .find((candidate) => isActuallyVisible(candidate.object, model));
      if (!hit) continue;
      foregroundSamples += 1;
      if (hit.object.name === "__face_display_content") faceDisplaySamples += 1;
      const nodeId = semanticOwner(hit.object, model, semanticIds);
      if (nodeId) nodeSamples.set(nodeId, (nodeSamples.get(nodeId) ?? 0) + 1);
    }
  }
  return { foregroundSamples, faceDisplaySamples, nodeSamples };
};

const unavailable = (code: string, message: string): StudioVisualInspection => ({
  status: "unavailable",
  code,
  message,
  renderContractVersion: VISUAL_RENDER_CONTRACT_VERSION,
  views: [],
  nodes: [],
  diagnostics: [{ code, severity: "warning", message }],
});

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
      suggestion: "Repair the compiler output or remove the stale compiled-part metadata.",
    });
  });
  nodes.filter((node) => (
    node.expectedInPreview
    && node.rendered
    && node.sampledVisiblePixels === 0
    && node.role !== "internal_mount"
  )).forEach((node) => {
    diagnostics.push({
      code: "SEMANTIC_NODE_NOT_VISIBLE",
      severity: "warning",
      nodeId: node.nodeId,
      message: `Semantic node ${node.nodeId} is not visible in any canonical view sample.`,
      suggestion: "Inspect attachment placement, occlusion, or scale.",
    });
  });
  return diagnostics;
};

export const faceDisplayInspectionDiagnostic = (
  faceDisplayAvailable: boolean,
  frontViewSamples: number,
): VisualInspectionDiagnostic | null => {
  if (!faceDisplayAvailable) {
    return {
      code: "FACE_DISPLAY_MISSING",
      severity: "error",
      message: "The runtime face decorator could not be placed on a semantic surface.",
      suggestion: "Add a non-degenerate face bezel or head surface.",
    };
  }
  if (frontViewSamples === 0) {
    return {
      code: "FACE_DISPLAY_NOT_SAMPLED",
      severity: "warning",
      view: "front",
      message: "The coarse visibility sample did not observe the runtime face in the front view.",
      suggestion: "Inspect the canonical front render before concluding that the face is occluded.",
    };
  }
  return null;
};

async function captureDesignVisuals(
  input: JsonObject,
  value: JsonObject,
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

  const response = await fetch(`/api/studio/v1/artifacts/${source.glbSha256}`, {
    credentials: "same-origin",
  });
  if (!response.ok) {
    return unavailable(
      "VISUAL_GLB_UNAVAILABLE",
      `The exact compiler GLB could not be loaded (${response.status}).`,
    );
  }
  let bytes: ArrayBuffer;
  try {
    bytes = await readBoundedArtifactBytes(response, source.glbByteSize);
  } catch (error) {
    return unavailable(
      "VISUAL_GLB_SIZE_MISMATCH",
      error instanceof Error ? error.message : "The compiler GLB byte size changed.",
    );
  }
  if (await bytesSha256(bytes) !== source.glbSha256) {
    return unavailable("VISUAL_GLB_DIGEST_MISMATCH", "The compiler GLB digest changed.");
  }

  let model: THREE.Object3D;
  try {
    model = await loadGltf(bytes);
  } catch (error) {
    return unavailable(
      "VISUAL_GLB_INVALID",
      error instanceof Error ? error.message : "The compiler GLB could not be parsed.",
    );
  }

  const scene = new THREE.Scene();
  scene.background = new THREE.Color(0x788180);
  scene.add(new THREE.HemisphereLight(0xffffff, 0x283436, 2.8));
  const keyLight = new THREE.DirectionalLight(0xffe2b5, 4.4);
  keyLight.position.set(3, 4, 5);
  scene.add(keyLight);
  const rimLight = new THREE.DirectionalLight(0xa5f1eb, 2.2);
  rimLight.position.set(-4, 2, -3);
  scene.add(rimLight);

  model.traverse((object) => {
    const role = source.partRoles.get(object.name);
    if (role) object.userData.compiled_role = role;
  });
  const diagnosticPartCount = [...source.partRoles.values()]
    .filter((role) => role === "hardware_keepout").length;
  hideDiagnosticGeometry(model);
  const rig = rigStudioModel(model, source.spec);
  scene.add(model);

  const bounds = visibleWorldBounds(model);
  if (bounds.isEmpty()) {
    rig.dispose();
    disposeObject(model);
    return unavailable("VISUAL_SCENE_EMPTY", "The compiler GLB has no visible renderable geometry.");
  }
  const sphere = bounds.getBoundingSphere(new THREE.Sphere());
  const radius = Math.max(sphere.radius, 0.01);
  const camera = new THREE.OrthographicCamera();
  let renderer: THREE.WebGLRenderer | null = null;
  try {
    renderer = new THREE.WebGLRenderer({
      antialias: true,
      alpha: false,
      preserveDrawingBuffer: true,
    });
    renderer.setPixelRatio(1);
    renderer.setSize(VISUAL_RENDER_SIZE_PX, VISUAL_RENDER_SIZE_PX, false);
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.05;

    const semanticIds = new Set(source.spec.morphologyNodes.map((node) => node.nodeId));
    const expectedSemanticIds = new Set(
      [...source.partRoles.keys()].filter((name) => semanticIds.has(name)),
    );
    const renderedIds = new Set<string>();
    model.traverse((object) => {
      if (semanticIds.has(object.name)) renderedIds.add(object.name);
      const nodeId = object.userData.node_id;
      if (typeof nodeId === "string" && semanticIds.has(nodeId)) renderedIds.add(nodeId);
    });
    const sampleTotals = new Map<string, number>();
    const visibleViews = new Map<string, CanonicalViewName[]>();
    const views: VisualInspectionView[] = [];

    for (const definition of CANONICAL_VIEWS) {
      configureCamera(camera, sphere.center, radius, definition.direction);
      renderer.render(scene, camera);
      const visibility = sampleVisibility(model, camera, semanticIds);
      visibility.nodeSamples.forEach((count, nodeId) => {
        sampleTotals.set(nodeId, (sampleTotals.get(nodeId) ?? 0) + count);
        const nodeViews = visibleViews.get(nodeId) ?? [];
        nodeViews.push(definition.view);
        visibleViews.set(nodeId, nodeViews);
      });
      const dataUrl = renderer.domElement.toDataURL("image/png");
      views.push({
        view: definition.view,
        label: definition.label,
        widthPx: VISUAL_RENDER_SIZE_PX,
        heightPx: VISUAL_RENDER_SIZE_PX,
        cameraDirection: [...definition.direction],
        pngSha256: await dataUrlSha256(dataUrl),
        dataUrl,
        foregroundSamples: visibility.foregroundSamples,
        faceDisplaySamples: visibility.faceDisplaySamples,
        visibleNodeIds: [...visibility.nodeSamples.keys()].sort(),
      });
    }

    const nodes: VisualInspectionNode[] = source.spec.morphologyNodes.map((node) => ({
      nodeId: node.nodeId,
      role: node.role,
      expectedInPreview: expectedSemanticIds.has(node.nodeId),
      rendered: renderedIds.has(node.nodeId),
      visibleViews: visibleViews.get(node.nodeId) ?? [],
      sampledVisiblePixels: sampleTotals.get(node.nodeId) ?? 0,
    }));
    const diagnostics = semanticInspectionDiagnostics(nodes);
    const faceDiagnostic = faceDisplayInspectionDiagnostic(
      rig.faceDisplay !== null,
      views.find((view) => view.view === "front")?.faceDisplaySamples ?? 0,
    );
    if (faceDiagnostic) diagnostics.push(faceDiagnostic);
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
      views,
      nodes,
      diagnostics,
    };
  } finally {
    renderer?.dispose();
    renderer?.forceContextLoss();
    rig.dispose();
    disposeObject(model);
  }
}

export async function inspectDesignVisuals(
  input: JsonObject,
  value: JsonObject,
): Promise<StudioVisualInspection> {
  try {
    return await captureDesignVisuals(input, value);
  } catch (error) {
    return unavailable(
      "VISUAL_RENDER_FAILED",
      error instanceof Error ? error.message : "Canonical rendering failed.",
    );
  }
}

export function visualInspectionToolResult(inspection: StudioVisualInspection): JsonObject {
  return {
    status: inspection.status,
    render_contract_version: inspection.renderContractVersion,
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
      width_px: view.widthPx,
      height_px: view.heightPx,
      camera_direction: view.cameraDirection,
      png_sha256: view.pngSha256,
      foreground_samples: view.foregroundSamples,
      face_display_samples: view.faceDisplaySamples,
      visible_node_ids: view.visibleNodeIds,
      observation: "Open the canonical inspection contact sheet in the shared Studio page.",
    })),
    nodes: inspection.nodes.map((node) => ({
      node_id: node.nodeId,
      role: node.role,
      expected_in_preview: node.expectedInPreview,
      rendered: node.rendered,
      visible_views: node.visibleViews,
      sampled_visible_pixels: node.sampledVisiblePixels,
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
