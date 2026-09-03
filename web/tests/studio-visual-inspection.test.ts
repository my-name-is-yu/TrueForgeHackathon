import * as THREE from "three";
import { describe, expect, it } from "vitest";

import {
  CANONICAL_VIEWS,
  canonicalCameraPosition,
  designTargetsEqual,
  faceDisplayInspectionDiagnostic,
  parseInspectVisualSource,
  readBoundedArtifactBytes,
  semanticInspectionDiagnostics,
  visualInspectionToolResult,
  VISUAL_RENDER_SIZE_PX,
  type StudioVisualInspection,
} from "../src/studio/visual-inspection";

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
    nodes: [{
      kind: "rounded_solid",
      node_id: "body",
      role: "chassis_shell",
      label: "Body",
      visible: true,
      size_mm: { x: 100, y: 80, z: 70 },
      corner_radius_mm: 20,
    }],
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
  compiled_parts: [{
    name: "keepout_front",
    role: "hardware_keepout",
    bounds: {
      minimum_mm: [-1, -1, -1],
      maximum_mm: [1, 1, 1],
    },
    volume_mm3: 8,
    printable: false,
  }],
});

describe("Studio canonical visual inspection", () => {
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
    expect(source.spec.motif).toBe("penguin");
    expect(source.spec.morphologyNodes[0].visible).toBe(true);
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

  it("rejects visual inspection when compiled-part metadata is absent", () => {
    const result = { ...inspectResult(), compiled_parts: [] };
    expect(() => parseInspectVisualSource(
      { target: { kind: "draft", draft_hash: "a".repeat(64) } },
      result,
    )).toThrow("must contain between 1 and 256 parts");
  });

  it("rejects ambiguous duplicate compiled-part names at the visual boundary", () => {
    const part = inspectResult().compiled_parts[0];
    const result = { ...inspectResult(), compiled_parts: [part, { ...part }] };
    expect(() => parseInspectVisualSource(
      { target: { kind: "draft", draft_hash: "a".repeat(64) } },
      result,
    )).toThrow("contains duplicate names");
  });

  it("returns compact JSON metadata without embedding the PNG payload", () => {
    const inspection: StudioVisualInspection = {
      status: "ready",
      renderContractVersion: "studio-render-v1",
      source: {
        target: { kind: "revision", revision_id: "r001" },
        specHash: "a".repeat(64),
        geometrySha256: "b".repeat(64),
        glbSha256: "c".repeat(64),
      },
      views: [{
        view: "front",
        label: "Front",
        widthPx: VISUAL_RENDER_SIZE_PX,
        heightPx: VISUAL_RENDER_SIZE_PX,
        cameraDirection: [0, 0, 1],
        pngSha256: "d".repeat(64),
        dataUrl: "data:image/png;base64,secret-pixels",
        foregroundSamples: 100,
        faceDisplaySamples: 12,
        visibleNodeIds: ["body"],
      }],
      nodes: [{
        nodeId: "body",
        role: "chassis_shell",
        expectedInPreview: true,
        rendered: true,
        visibleViews: ["front"],
        sampledVisiblePixels: 100,
      }],
      diagnostics: [],
    };

    const result = visualInspectionToolResult(inspection);

    expect(JSON.stringify(result)).not.toContain("secret-pixels");
    expect(result).toMatchObject({
      status: "ready",
      requires_visual_judgment: true,
      affects_manufacturing_evidence: false,
      views: [{ view: "front", png_sha256: "d".repeat(64) }],
    });
  });

  it("does not report intentionally unexported hidden or CSG operand nodes as missing", () => {
    const diagnostics = semanticInspectionDiagnostics([
      {
        nodeId: "hidden_ornament",
        role: "ornament",
        expectedInPreview: false,
        rendered: false,
        visibleViews: [],
        sampledVisiblePixels: 0,
      },
      {
        nodeId: "consumed_operand",
        role: "chassis_shell",
        expectedInPreview: false,
        rendered: false,
        visibleViews: [],
        sampledVisiblePixels: 0,
      },
      {
        nodeId: "compiled_beak",
        role: "beak",
        expectedInPreview: true,
        rendered: false,
        visibleViews: [],
        sampledVisiblePixels: 0,
      },
    ]);

    expect(diagnostics).toHaveLength(1);
    expect(diagnostics[0]).toMatchObject({
      code: "SEMANTIC_NODE_MISSING",
      nodeId: "compiled_beak",
    });
  });

  it("treats an unsampled face as inconclusive instead of declaring it occluded", () => {
    expect(faceDisplayInspectionDiagnostic(true, 0)).toMatchObject({
      code: "FACE_DISPLAY_NOT_SAMPLED",
      severity: "warning",
      view: "front",
    });
    expect(faceDisplayInspectionDiagnostic(true, 1)).toBeNull();
    expect(faceDisplayInspectionDiagnostic(false, 0)).toMatchObject({
      code: "FACE_DISPLAY_MISSING",
      severity: "error",
    });
  });

  it("aborts an artifact stream when it exceeds the declared byte size", async () => {
    let cancelled = false;
    const response = {
      headers: new Headers(),
      body: new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new Uint8Array([1, 2, 3]));
          controller.enqueue(new Uint8Array([4, 5, 6]));
        },
        cancel() {
          cancelled = true;
        },
      }),
    } as Response;

    await expect(readBoundedArtifactBytes(response, 4)).rejects.toThrow(
      "exceeded its declared byte size",
    );
    expect(cancelled).toBe(true);
  });
});
