import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getStudioContext,
  parseBuildPackResult,
  parseScenarioPreview,
  parseStudioContext,
  setStudioSelection,
  StudioContractError,
} from "../src/studio/api";

const spec = {
  identity: {
    name: "Pico",
    role: "Quiet desk guide",
    motif: "shy duck",
    design_brief: "A compact duck that looks up carefully before greeting.",
  },
  hardware_profile_id: "m5-cores3-goplus2/v1",
  appearance: {
    primary_color: "#F4C542",
    secondary_color: "#FFF2B2",
    accent_color: "#EF7F1A",
    eye_color: "#111111",
  },
  morphology: {
    nodes: [
      { node_id: "body", role: "chassis_shell", label: "Soft body", kind: "rounded_solid" },
      {
        node_id: "beak",
        role: "beak",
        label: "Short beak",
        kind: "loft",
        attachment: { parent_node_id: "body", parent_anchor: "face" },
      },
    ],
  },
  personality: { curiosity: 0.8, energy: 0.25, voice_style: "shy", motion_style: "careful" },
  behavior: { scenarios: [{ scenario_id: "greet" }, { scenario_id: "listen" }] },
  face: { default_expression: "neutral", supported_expressions: ["neutral", "happy"] },
};

describe("Character Robot Studio API boundary", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("parses the domain context and derives its compiler GLB URL", () => {
    const sha = "a".repeat(64);
    const context = parseStudioContext({
      schema_version: "character-robot/v1",
      project_id: "project-pico",
      head_revision_id: "r003",
      head_spec_sha256: "b".repeat(64),
      current_spec: null,
      draft: {
        spec,
        draft_hash: "c".repeat(64),
        base_revision_id: "r003",
        preview_artifact: { kind: "glb", sha256: sha, file_name: "preview.glb" },
        warnings: ["Wheel cover clearance is still conceptual."],
      },
      hardware_profiles: [{
        profile_id: "m5-cores3-goplus2/v1",
        display_name: "CoreS3 + GoPlus2",
        qualification: "digital_only",
        controller: "M5Stack CoreS3",
        minimum_enclosure_mm: { x: 85, y: 72, z: 92 },
        component_count: 7,
        unknowns: ["Battery pack has not been measured."],
      }],
      selected_node_id: "beak",
      latest_validation: {
        evidence_level: "concept_only",
        passed: false,
        issues: [{
          code: "clearance_warning",
          severity: "warning",
          path: "morphology.nodes[1]",
          message: "Check the cable path.",
          measured_value: 0.2,
          limit_value: 0.3,
          suggestion: "Increase the cable clearance.",
        }],
      },
    });

    expect(context.preview.glbUrl).toBe(`/api/studio/v1/artifacts/${sha}`);
    expect(context.preview.partNames).toEqual(["body", "beak"]);
    expect(context.draft?.spec).toMatchObject({
      name: "Pico",
      hardwareProfileId: "m5-cores3-goplus2/v1",
      scenarioIds: ["greet", "listen"],
    });
    expect(context.profiles[0]).toMatchObject({
      label: "CoreS3 + GoPlus2",
      evidenceLevel: "concept_only",
      controller: "M5Stack CoreS3",
      components: ["M5Stack CoreS3", "7 catalog components"],
    });
    expect(context.latestValidation?.checks[0].status).toBe("warning");
    expect(context.latestValidation?.checks[0]).toMatchObject({
      path: "morphology.nodes[1]",
      measuredValue: 0.2,
      limitValue: 0.3,
      suggestion: "Increase the cable clearance.",
    });
    expect(context.draft?.spec.morphologyNodes[1]).toMatchObject({
      parentNodeId: "body",
      parentAnchor: "face",
    });
  });

  it("does not borrow the committed preview while a draft is active", () => {
    const context = parseStudioContext({
      schema_version: "character-robot/v1",
      head_revision_id: "r003",
      head_spec_sha256: "b".repeat(64),
      current_spec: spec,
      current_preview_artifact: {
        kind: "glb",
        sha256: "a".repeat(64),
        file_name: "committed.glb",
      },
      preview: {
        glb_url: "/api/studio/v1/artifacts/legacy-preview",
        preview_artifact: {
          kind: "glb",
          sha256: "d".repeat(64),
          file_name: "stale-preview.glb",
        },
      },
      draft: {
        spec,
        draft_hash: "c".repeat(64),
        base_revision_id: "r003",
        preview_artifact: null,
      },
      hardware_profiles: [],
    });

    expect(context.preview.glbUrl).toBeNull();
  });

  it("normalizes flat wheel, neck and face keyframes for playback", () => {
    const scenario = parseScenarioPreview({
      schema_version: "character-robot/v1",
      request_id: "req_123",
      scenario_id: "greet",
      duration_ms: 1000,
      evidence_level: "concept_only",
      keyframes: [
        { at_ms: 0, wheel_left: 0.2, wheel_right: 0.6, head_pan_deg: -12, head_tilt_deg: 4, face_expression: "happy" },
        { at_ms: 1000, wheel_left: 0, wheel_right: 0, head_pan_deg: 0, head_tilt_deg: 0, face_expression: "neutral" },
      ],
    });

    expect(scenario.durationS).toBe(1);
    expect(scenario.keyframes[0]).toMatchObject({
      timeS: 0,
      wheels: { leftCommand: 0.2, rightCommand: 0.6 },
      neck: { panDeg: -12, tiltDeg: 4 },
      face: { expression: "happy" },
      soundCue: null,
    });
  });

  it("posts an exact design target when sharing a human selection", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ selected_node_id: "beak" }),
    });
    vi.stubGlobal("fetch", fetch);

    await expect(setStudioSelection({
      target: { kind: "draft", draft_hash: "c".repeat(64) },
      node_id: "beak",
    })).resolves.toBe("beak");
    expect(fetch).toHaveBeenCalledWith(
      "/api/studio/v1/selection",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          target: { kind: "draft", draft_hash: "c".repeat(64) },
          node_id: "beak",
        }),
      }),
    );
  });

  it("preserves build manifest identities and artifact digests", () => {
    const digest = (character: string): string => character.repeat(64);
    const result = parseBuildPackResult({
      status: "experimental_ready",
      human_action_required: true,
      next_action: "Review each artifact.",
      blockers: [],
      manifest: {
        revision_id: "r003",
        spec_hash: digest("a"),
        build_subject_hash: digest("e"),
        geometry_sha256: digest("b"),
        profile_id: "m5-cores3-goplus2/v1",
        profile_sha256: digest("f"),
        catalog_version: "hardware-catalog-v1",
        compiler_version: "character-cad-v1",
        cad_engine_version: "0.11.1",
        firmware_runtime_version: "character-runtime-v1",
        evidence_level: "digital_checks_passed",
        manifest_hash: digest("c"),
        download_requires_human_action: true,
        artifacts: [{
          kind: "stl",
          file_name: "printable-parts.stl",
          media_type: "model/stl",
          sha256: digest("d"),
          byte_size: 2048,
          experimental: true,
        }],
      },
    });

    expect(result.manifest).toMatchObject({
      manifestHash: digest("c"),
      buildSubjectHash: digest("e"),
      specHash: digest("a"),
      geometrySha256: digest("b"),
      profileSha256: digest("f"),
      compilerVersion: "character-cad-v1",
      cadEngineVersion: "0.11.1",
    });
    expect(result.artifacts[0].sha256).toBe(digest("d"));
  });

  it("rejects invalid evidence at the network boundary", () => {
    expect(() => parseStudioContext({
      schema_version: "character-robot/v1",
      project_id: "bad",
      current_spec: null,
      hardware_profiles: [{
        profile_id: "m5-cores3-goplus2/v1",
        display_name: "CoreS3",
        qualification: "totally_safe",
      }],
    })).toThrow(StudioContractError);
  });

  it("establishes UI context through the session endpoint", async () => {
    const fetch = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        schema_version: "character-robot/v1",
        project_id: "project-empty",
        head_revision_id: null,
        current_spec: null,
        hardware_profiles: [],
      }),
    });
    vi.stubGlobal("fetch", fetch);

    await expect(getStudioContext()).resolves.toMatchObject({ projectId: "project-empty" });
    expect(fetch).toHaveBeenCalledWith("/api/studio/v1/context", {
      credentials: "same-origin",
    });
  });
});
