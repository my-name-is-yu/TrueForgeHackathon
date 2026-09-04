import * as THREE from "three";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  buildVisualInspection,
  CANONICAL_VIEWS,
  canonicalCameraPosition,
  designTargetsEqual,
  faceDisplayInspectionDiagnostic,
  inspectDesignVisuals,
  parseInspectVisualSource,
  semanticInspectionDiagnostics,
  visualInspectionToolResult,
  VISUAL_RENDER_SIZE_PX,
  MAXIMUM_CANONICAL_IMAGE_BYTES,
  type StudioCanonicalCapture,
  type StudioVisualInspection,
} from "../src/studio/visual-inspection";

const PNG_SIGNATURE = [137, 80, 78, 71, 13, 10, 26, 10];
const pngDataUrl = (width = VISUAL_RENDER_SIZE_PX, height = VISUAL_RENDER_SIZE_PX): string => {
  const bytes = new Uint8Array([
    ...PNG_SIGNATURE,
    0, 0, 0, 13,
    73, 72, 68, 82,
    (width >>> 24) & 0xff,
    (width >>> 16) & 0xff,
    (width >>> 8) & 0xff,
    width & 0xff,
    (height >>> 24) & 0xff,
    (height >>> 16) & 0xff,
    (height >>> 8) & 0xff,
    height & 0xff,
    8, 6, 0, 0, 0,
  ]);
  return `data:image/png;base64,${btoa(String.fromCharCode(...bytes))}`;
};

const rawSpec = {
  identity: {
    name: "Pico",
    role: "guide",
    motif: "penguin",
    design_brief: "A rounded penguin guide.",
  },
  hardware_profile_id: "m5-cores3-goplus2/v1",
  appearance: {
    primary_color: "#111111",
    secondary_color: "#F4EED8",
    accent_color: "#F28C28",
    eye_color: "#111111",
  },
  morphology: {
    nodes: [
      {
        kind: "rounded_solid",
        node_id: "body",
        role: "chassis_shell",
        label: "Body",
        visible: true,
        size_mm: { x: 100, y: 80, z: 70 },
        corner_radius_mm: 20,
      },
      {
        kind: "rounded_solid",
        node_id: "beak",
        role: "beak",
        label: "Beak",
        visible: true,
        size_mm: { x: 20, y: 12, z: 12 },
        corner_radius_mm: 3,
      },
      {
        kind: "rounded_solid",
        node_id: "internal_mount",
        role: "internal_mount",
        label: "Internal mount",
        visible: false,
        size_mm: { x: 10, y: 10, z: 10 },
        corner_radius_mm: 2,
      },
    ],
  },
  personality: {},
  face: { default_expression: "neutral", supported_expressions: ["neutral"] },
  behavior: { scenarios: [{ scenario_id: "idle", duration_ms: 100, keyframes: [] }] },
};

const inspectResult = () => ({
  target: { kind: "draft", draft_hash: "a".repeat(64) },
  spec_hash: "b".repeat(64),
  geometry_sha256: "c".repeat(64),
  spec: rawSpec,
  preview_artifact: {
    kind: "glb",
    file_name: "preview.glb",
    media_type: "model/gltf-binary",
    sha256: "d".repeat(64),
    byte_size: 4096,
    experimental: true,
  },
  compiled_parts: [
    {
      name: "body",
      role: "chassis_shell",
      bounds: { minimum_mm: [-1, -1, -1], maximum_mm: [1, 1, 1] },
      volume_mm3: 8,
      printable: true,
    },
    {
      name: "beak",
      role: "beak",
      bounds: { minimum_mm: [-1, -1, -1], maximum_mm: [1, 1, 1] },
      volume_mm3: 8,
      printable: true,
    },
    {
      name: "keepout_front",
      role: "hardware_keepout",
      bounds: { minimum_mm: [-1, -1, -1], maximum_mm: [1, 1, 1] },
      volume_mm3: 8,
      printable: false,
    },
  ],
});

const canonicalCapture = (): StudioCanonicalCapture => ({
  previewUrl: `/api/studio/v1/artifacts/${"d".repeat(64)}`,
  views: CANONICAL_VIEWS.map((definition) => ({
    view: definition.view,
    label: definition.label,
    widthPx: VISUAL_RENDER_SIZE_PX,
    heightPx: VISUAL_RENDER_SIZE_PX,
    cameraDirection: [...definition.direction] as [number, number, number],
    dataUrl: pngDataUrl(),
  })),
  renderedNodeIds: ["beak", "body"],
  structurallyVisibleNodeIds: ["beak", "body"],
  faceDisplayAvailable: true,
});

describe("Studio canonical visual inspection", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("defines four stable, distinct canonical camera directions", () => {
    expect(CANONICAL_VIEWS.map((view) => view.view)).toEqual([
      "front",
      "three_quarter",
      "side",
      "back",
    ]);
    const center = new THREE.Vector3(1, 2, 3);
    const front = canonicalCameraPosition(center, 2, [0, 0, 1]);
    const back = canonicalCameraPosition(center, 2, [0, 0, -1]);
    expect(front.z).toBe(11);
    expect(back.z).toBe(-5);
    expect(front.x).toBe(center.x);
  });

  it("binds the compiler GLB and part roles to the exact requested target", () => {
    const input = { target: { kind: "draft", draft_hash: "a".repeat(64) } };
    const source = parseInspectVisualSource(input, inspectResult());

    expect(source.target).toEqual(input.target);
    expect(source.specHash).toBe("b".repeat(64));
    expect(source.geometrySha256).toBe("c".repeat(64));
    expect(source.glbSha256).toBe("d".repeat(64));
    expect(source.partRoles.get("keepout_front")).toBe("hardware_keepout");
    expect(source.spec.morphologyNodes.map((node) => [node.nodeId, node.visible])).toEqual([
      ["body", true],
      ["beak", true],
      ["internal_mount", false],
    ]);
  });

  it("rejects a response for a different draft", () => {
    const input = { target: { kind: "draft", draft_hash: "e".repeat(64) } };
    expect(() => parseInspectVisualSource(input, inspectResult())).toThrow(
      "returned a different design target",
    );
    expect(designTargetsEqual(
      { kind: "revision", revision_id: "r001" },
      { kind: "revision", revision_id: "r002" },
    )).toBe(false);
  });

  it("rejects visual inspection when compiled-part metadata is absent or ambiguous", () => {
    expect(() => parseInspectVisualSource(
      { target: { kind: "draft", draft_hash: "a".repeat(64) } },
      { ...inspectResult(), compiled_parts: [] },
    )).toThrow("must contain between 1 and 256 parts");
    const part = inspectResult().compiled_parts[0];
    expect(() => parseInspectVisualSource(
      { target: { kind: "draft", draft_hash: "a".repeat(64) } },
      { ...inspectResult(), compiled_parts: [part, { ...part }] },
    )).toThrow("contains duplicate names");
  });

  it("expects every visible Spec node and fails closed when body or beak is missing", () => {
    const source = parseInspectVisualSource(
      { target: { kind: "draft", draft_hash: "a".repeat(64) } },
      inspectResult(),
    );
    for (const missingNodeId of ["body", "beak"]) {
      const capture = canonicalCapture();
      const renderedNodeIds = ["body", "beak"].filter((nodeId) => nodeId !== missingNodeId);
      capture.renderedNodeIds = renderedNodeIds;
      capture.structurallyVisibleNodeIds = renderedNodeIds;
      const inspection = buildVisualInspection(source, capture);

      expect(inspection.nodes).toEqual(expect.arrayContaining([
        expect.objectContaining({ nodeId: missingNodeId, expectedInPreview: true, rendered: false }),
        expect.objectContaining({
          nodeId: missingNodeId === "body" ? "beak" : "body",
          expectedInPreview: true,
          rendered: true,
        }),
        expect.objectContaining({ nodeId: "internal_mount", expectedInPreview: false, rendered: false }),
      ]));
      expect(inspection.diagnostics).toEqual(expect.arrayContaining([
        expect.objectContaining({ code: "SEMANTIC_NODE_MISSING", nodeId: missingNodeId, severity: "error" }),
      ]));
      expect(inspection.diagnostics).not.toEqual(expect.arrayContaining([
        expect.objectContaining({ code: "SEMANTIC_NODE_MISSING", nodeId: "internal_mount" }),
      ]));
    }
  });

  it("decodes canonical PNG signatures and dimensions before returning images", () => {
    const source = parseInspectVisualSource(
      { target: { kind: "draft", draft_hash: "a".repeat(64) } },
      inspectResult(),
    );
    const inspection = buildVisualInspection(source, canonicalCapture());
    const result = visualInspectionToolResult(inspection);
    const content = result.content as Array<Record<string, unknown>>;

    expect(content).toHaveLength(5);
    expect(content[0]).toMatchObject({ type: "text" });
    expect(content.slice(1)).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ type: "image", mimeType: "image/png" }),
      ]),
    );
    expect(content.slice(1).every((block) => (
      typeof block.data === "string"
      && block.data.startsWith("iVBORw0KGgo")
      && !block.data.startsWith("data:")
    ))).toBe(true);
  });

  it("rejects malformed, wrong-sized, and oversized canonical PNG payloads", () => {
    const source = parseInspectVisualSource(
      { target: { kind: "draft", draft_hash: "a".repeat(64) } },
      inspectResult(),
    );
    const badSignature = canonicalCapture();
    badSignature.views[0].dataUrl = "data:image/png;base64,AA==";
    expect(() => buildVisualInspection(source, badSignature)).toThrow("not a decodable PNG");

    const wrongDimensions = canonicalCapture();
    wrongDimensions.views[0].dataUrl = pngDataUrl(VISUAL_RENDER_SIZE_PX - 1);
    expect(() => buildVisualInspection(source, wrongDimensions)).toThrow(
      "bounded canonical dimensions",
    );

    const oversized = canonicalCapture();
    const bytes = new Uint8Array(MAXIMUM_CANONICAL_IMAGE_BYTES + 1);
    bytes.set(PNG_SIGNATURE);
    bytes.set([0, 0, 0, 13, 73, 72, 68, 82, 0, 0, 1, 128, 0, 0, 1, 128], 8);
    oversized.views[0].dataUrl = `data:image/png;base64,${btoa(
      Array.from(bytes, (byte) => String.fromCharCode(byte)).join(""),
    )}`;
    expect(() => buildVisualInspection(source, oversized)).toThrow("image size limit");
  });

  it("uses the shared loaded capture once without fetching or parsing another GLB", async () => {
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    const capture = vi.fn().mockResolvedValue(canonicalCapture());
    const inspection = await inspectDesignVisuals(
      { target: { kind: "draft", draft_hash: "a".repeat(64) } },
      inspectResult(),
      capture,
    );

    expect(inspection.status).toBe("ready");
    expect(capture).toHaveBeenCalledOnce();
    expect(fetch).not.toHaveBeenCalled();
    expect(JSON.stringify(inspection)).not.toMatch(
      /foregroundSamples|faceDisplaySamples|sampledVisiblePixels|pngSha256|visibleViews|png_sha256/,
    );
  });

  it("keeps face diagnostics structural and never infers occlusion from pixel samples", () => {
    expect(faceDisplayInspectionDiagnostic(true)).toBeNull();
    expect(faceDisplayInspectionDiagnostic(false)).toMatchObject({
      code: "FACE_DISPLAY_MISSING",
      severity: "error",
    });
    expect(semanticInspectionDiagnostics([
      {
        nodeId: "body",
        role: "chassis_shell",
        expectedInPreview: true,
        rendered: true,
        structurallyVisible: true,
      },
    ])).toEqual([]);
  });

  it("keeps unavailable captures typed and free of image data", async () => {
    const capture = vi.fn().mockRejectedValue(new Error("loaded preview mismatch"));
    const inspection = await inspectDesignVisuals(
      { target: { kind: "draft", draft_hash: "a".repeat(64) } },
      inspectResult(),
      capture,
    );
    const result = visualInspectionToolResult(inspection as StudioVisualInspection);

    expect(result).toMatchObject({ status: "unavailable" });
    expect(result.content).toEqual([expect.objectContaining({ type: "text" })]);
    expect(JSON.stringify(result)).not.toContain("data:image/png");
  });
});
