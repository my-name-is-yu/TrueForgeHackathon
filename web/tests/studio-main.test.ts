import { afterEach, describe, expect, it, vi } from "vitest";

import { mountCharacterRobotStudio } from "../src/studio/main";
import type { BuildPackResult, ScenarioPreview, StudioContext } from "../src/studio/types";
import { STUDIO_CHANGED_EVENT } from "../src/studio/webmcp";

const context = (): StudioContext => ({
  schemaVersion: "character-robot/v1",
  projectId: "project-pico",
  projectGeneration: 7,
  storageMode: "durable",
  artifactManifestCount: 1,
  headRevisionId: "r003",
  headSpecSha256: "b".repeat(64),
  draft: {
    draftHash: "c".repeat(64),
    baseRevisionId: "r003",
    spec: {
      name: "Pico",
      role: "Quiet desk guide",
      motif: "shy duck",
      designBrief: "A compact duck that looks up carefully before greeting.",
      hardwareProfileId: "m5-cores3-goplus2/v1",
      morphologyNodes: [{
        nodeId: "beak",
        role: "beak",
        label: "Short beak",
        parentNodeId: null,
        parentAnchor: null,
      }],
      scenarioIds: ["greet"],
      personalityTraits: ["careful"],
      appearance: {
        primaryColor: "#F4C542",
        secondaryColor: "#FFF2B2",
        accentColor: "#EF7F1A",
        eyeColor: "#111111",
      },
      face: { defaultExpression: "neutral", supportedExpressions: ["neutral", "happy"] },
    },
  },
  currentSpec: null,
  profiles: [{
    profileId: "m5-cores3-goplus2/v1",
    label: "CoreS3 + GoPlus2",
    evidenceLevel: "concept_only",
    controller: "M5Stack CoreS3",
    minimumEnclosureMm: { x: 85, y: 72, z: 92 },
    components: ["M5Stack CoreS3"],
    capabilities: ["two-wheel drive"],
    unknowns: ["Battery pack has not been measured."],
  }],
  preview: {
    glbUrl: null,
    partNames: ["beak"],
    compiledAt: null,
    warnings: [{ code: "DIGITAL_ONLY", message: "Physical clearances are unverified.", severity: "warning" }],
  },
  selectedNodeId: null,
  latestValidation: {
    evidenceLevel: "concept_only",
    warnings: [],
    checks: [{
      code: "minimum_clearance",
      label: "Minimum clearance",
      status: "warning",
      message: "The cable path is narrow.",
      path: "morphology.nodes[0]",
      measuredValue: 0.2,
      limitValue: 0.3,
      suggestion: "Increase clearance by 0.1 mm.",
    }],
  },
});

const buildPack = (specHash = "b".repeat(64)): BuildPackResult => ({
  status: "experimental_ready",
  manifest: {
    revisionId: "r003",
    specHash,
    geometrySha256: "e".repeat(64),
    profileId: "m5-cores3-goplus2/v1",
    catalogVersion: "hardware-catalog-v1",
    compilerVersion: "character-cad-v1",
    cadEngineVersion: "0.11.1",
    simulationEngineVersion: "3.5.0",
    firmwareRuntimeVersion: "character-runtime-v1",
    evidenceLevel: "digital_checks_passed",
    manifestHash: "f".repeat(64),
    downloadRequiresHumanAction: true,
  },
  artifacts: [{
    kind: "stl",
    fileName: "pico-body.stl",
    mediaType: "model/stl",
    sha256: "d".repeat(64),
    byteSize: 4096,
    experimental: true,
    downloadUrl: `/api/studio/v1/artifacts/${"d".repeat(64)}`,
  }],
  blockers: [{
    code: "runtime_release_not_published",
    severity: "warning",
    path: "build_pack",
    message: "The pinned runtime binary has not been published.",
    measuredValue: null,
    limitValue: null,
    suggestion: "Publish and digest the fixed runtime release.",
  }],
  nextAction: "Review and download each artifact.",
  humanActionRequired: true,
});

describe("mountCharacterRobotStudio", () => {
  afterEach(() => { document.body.replaceChildren(); });

  it("renders shared design evidence and plays backend-provided scenarios", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const selectNode = vi.fn();
    const playScenario = vi.fn((_scenario, onFrame, onComplete) => {
      onFrame({
        timeS: 0.4,
        progress: 0.5,
        wheels: { leftCommand: 0.2, rightCommand: 0.1 },
        neck: { panDeg: 10, tiltDeg: -2 },
        face: { expression: "happy" },
        soundCue: "soft_chirp",
      });
      onComplete?.();
    });
    const getScenario = vi.fn().mockResolvedValue({
      scenarioId: "greet",
      durationS: 0.8,
      evidenceLevel: "concept_only",
      keyframes: [],
    });
    const preparedPack = buildPack();
    const prepareBuildPack = vi.fn().mockResolvedValue(preparedPack);
    const importProject = vi.fn().mockResolvedValue(undefined);
    const setSelection = vi.fn().mockResolvedValue("beak");

    const studio = await mountCharacterRobotStudio(document.querySelector("#app")!, {
      getContext: async () => context(),
      getScenario,
      prepareBuildPack,
      importProject,
      confirmImport: () => true,
      setSelection,
      registerTools: async () => true,
      createViewer: () => ({
        loadPreview: vi.fn(),
        clearPreview: vi.fn(),
        selectNode,
        playScenario,
        stopScenario: vi.fn(),
        destroy: vi.fn(),
      }),
    });

    expect(document.querySelector("#crs-name")?.textContent).toBe("Pico");
    expect(document.querySelector("#crs-evidence")?.textContent).toBe("Concept only");
    expect(document.querySelector("#crs-profile")?.textContent).toContain("CoreS3 + GoPlus2");
    expect(document.querySelector("#crs-warnings")?.textContent).toContain("Physical clearances are unverified.");
    expect(document.querySelector("#crs-warnings")?.textContent).toContain("Measured: 0.2");
    expect(document.querySelector("#crs-warnings")?.textContent).toContain("Increase clearance by 0.1 mm.");

    (document.querySelector(".crs-part-button") as HTMLButtonElement).click();
    expect(selectNode).toHaveBeenLastCalledWith("beak");
    await vi.waitFor(() => expect(setSelection).toHaveBeenCalledWith({
      target: { kind: "draft", draft_hash: "c".repeat(64) },
      node_id: "beak",
    }));
    expect(document.querySelector("#crs-selection")?.textContent).toContain("Short beak");

    (document.querySelector(".crs-scenario-button") as HTMLButtonElement).click();
    await vi.waitFor(() => expect(getScenario).toHaveBeenCalledOnce());
    expect(getScenario).toHaveBeenCalledWith({
      target: { kind: "draft", draft_hash: "c".repeat(64) },
      scenario_id: "greet",
    });
    expect(playScenario).toHaveBeenCalledOnce();
    expect(document.querySelector("#crs-motion")?.textContent).toContain("happy");
    expect(document.querySelector("#crs-webmcp")?.textContent).toBe("8 site tools ready");

    document.dispatchEvent(new CustomEvent(STUDIO_CHANGED_EVENT, {
      detail: {
        tool: "prepare_build_pack",
        ok: true,
        buildPackResult: preparedPack,
      },
    }));
    await vi.waitFor(() => {
      expect(document.querySelector(".crs-manifest")?.textContent).toContain("Manifest r003");
    });
    expect(prepareBuildPack).not.toHaveBeenCalled();

    (document.querySelector("#crs-prepare-pack") as HTMLButtonElement).click();
    await vi.waitFor(() => expect(prepareBuildPack).toHaveBeenCalledOnce());
    expect(prepareBuildPack).toHaveBeenCalledWith({
      revision_id: "r003",
      expected_spec_hash: "b".repeat(64),
    });
    const artifact = document.querySelector<HTMLAnchorElement>(".crs-artifact")!;
    expect(artifact.download).toBe("pico-body.stl");
    expect(artifact.getAttribute("href")).toBe(`/api/studio/v1/artifacts/${"d".repeat(64)}`);
    expect(document.querySelector(".crs-manifest")?.textContent).toContain("character-cad-v1");
    expect(document.querySelector(".crs-manifest")?.textContent).toContain("CAD 0.11.1");
    expect(document.querySelector("#crs-artifacts")?.textContent).toContain("runtime_release_not_published");
    expect(document.querySelector("#crs-artifacts")?.textContent).toContain("pinned runtime binary has not been published");
    expect(artifact.textContent).toContain("d".repeat(64));

    const importInput = document.querySelector<HTMLInputElement>("#crs-import-project")!;
    const sharedProject = new File(["{}"], "shared-project.json", { type: "application/json" });
    Object.defineProperty(importInput, "files", { value: [sharedProject] });
    importInput.dispatchEvent(new Event("change", { bubbles: true }));
    await vi.waitFor(() => expect(importProject).toHaveBeenCalledWith(sharedProject, 7));
    await vi.waitFor(() => expect(document.querySelector(".crs-manifest")).toBeNull());

    document.dispatchEvent(new CustomEvent(STUDIO_CHANGED_EVENT, {
      detail: {
        tool: "revise_design_draft",
        ok: false,
        error: {
          code: "MAXIMUM_DIMENSIONS_EXCEEDED",
          message: "The draft needs 8.0 mm more width.",
          nextAction: "Reduce the head width.",
        },
      },
    }));
    await vi.waitFor(() => {
      expect(document.querySelector("#crs-warnings")?.textContent).toContain("8.0 mm more width");
    });

    studio.destroy();
  });

  it("clears a failed replacement GLB and retries the current URL", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let current = context();
    current.preview.glbUrl = "/api/studio/v1/artifacts/old-preview";
    const loadPreview = vi.fn()
      .mockResolvedValueOnce(undefined)
      .mockRejectedValueOnce(new Error("Replacement GLB is unavailable"))
      .mockResolvedValueOnce(undefined);
    const clearPreview = vi.fn();
    const studio = await mountCharacterRobotStudio(document.querySelector("#app")!, {
      getContext: async () => current,
      setSelection: async ({ node_id }) => node_id,
      registerTools: async () => false,
      createViewer: () => ({
        loadPreview,
        clearPreview,
        selectNode: vi.fn(),
        playScenario: vi.fn(),
        stopScenario: vi.fn(),
        destroy: vi.fn(),
      }),
    });
    await vi.waitFor(() => expect(loadPreview).toHaveBeenCalledTimes(1));

    current = structuredClone(current);
    current.preview.glbUrl = "/api/studio/v1/artifacts/new-preview";
    await studio.refresh();
    await vi.waitFor(() => expect(clearPreview).toHaveBeenCalledOnce());
    expect(document.querySelector("#crs-view-status")?.textContent).toBe("Preview unavailable");
    expect(document.querySelector("#crs-view-state")?.textContent).toContain(
      "Replacement GLB is unavailable",
    );

    await studio.refresh();
    await vi.waitFor(() => expect(loadPreview).toHaveBeenCalledTimes(3));
    expect(loadPreview).toHaveBeenLastCalledWith(
      "/api/studio/v1/artifacts/new-preview",
      current.draft!.spec,
    );
    studio.destroy();
  });

  it("invalidates build packs by spec identity and ignores stale responses", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let current = context();
    let resolveBuildPack!: (result: BuildPackResult) => void;
    const prepareBuildPack = vi.fn(() => new Promise<BuildPackResult>((resolve) => {
      resolveBuildPack = resolve;
    }));
    const studio = await mountCharacterRobotStudio(document.querySelector("#app")!, {
      getContext: async () => current,
      prepareBuildPack,
      setSelection: async ({ node_id }) => node_id,
      registerTools: async () => false,
      createViewer: () => ({
        loadPreview: vi.fn(),
        clearPreview: vi.fn(),
        selectNode: vi.fn(),
        playScenario: vi.fn(),
        stopScenario: vi.fn(),
        destroy: vi.fn(),
      }),
    });

    document.dispatchEvent(new CustomEvent(STUDIO_CHANGED_EVENT, {
      detail: { tool: "prepare_build_pack", ok: true, buildPackResult: buildPack() },
    }));
    await vi.waitFor(() => expect(document.querySelector(".crs-manifest")).not.toBeNull());

    current = structuredClone(current);
    current.headSpecSha256 = "a".repeat(64);
    await studio.refresh();
    expect(document.querySelector(".crs-manifest")).toBeNull();
    expect(document.querySelector(".crs-artifact")).toBeNull();

    current = context();
    await studio.refresh();
    (document.querySelector("#crs-prepare-pack") as HTMLButtonElement).click();
    await vi.waitFor(() => expect(prepareBuildPack).toHaveBeenCalledOnce());
    current = structuredClone(current);
    current.headSpecSha256 = "a".repeat(64);
    await studio.refresh();
    resolveBuildPack(buildPack());
    await vi.waitFor(() => {
      expect(document.querySelector<HTMLButtonElement>("#crs-prepare-pack")?.textContent)
        .toBe("Prepare build pack");
    });
    expect(document.querySelector(".crs-manifest")).toBeNull();

    document.dispatchEvent(new CustomEvent(STUDIO_CHANGED_EVENT, {
      detail: { tool: "prepare_build_pack", ok: true, buildPackResult: buildPack() },
    }));
    await Promise.resolve();
    expect(document.querySelector(".crs-manifest")).toBeNull();

    document.dispatchEvent(new CustomEvent(STUDIO_CHANGED_EVENT, {
      detail: {
        tool: "prepare_build_pack",
        ok: true,
        buildPackTarget: {
          revisionId: "r003",
          specHash: "b".repeat(64),
        },
        buildPackResult: {
          status: "blocked",
          manifest: null,
          artifacts: [],
          blockers: [{
            code: "stale_blocker",
            severity: "error",
            path: "simulation",
            message: "This blocker belongs to the previous spec.",
            measuredValue: null,
            limitValue: null,
            suggestion: null,
          }],
          nextAction: "Revise the previous spec.",
          humanActionRequired: true,
        },
      },
    }));
    await Promise.resolve();
    expect(document.querySelector("#crs-artifacts")?.textContent).not.toContain(
      "This blocker belongs to the previous spec.",
    );
    studio.destroy();
  });

  it("does not play a scenario response from a replaced draft", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let current = context();
    let resolveScenario!: (scenario: ScenarioPreview) => void;
    const getScenario = vi.fn(() => new Promise<ScenarioPreview>((resolve) => {
      resolveScenario = resolve;
    }));
    const playScenario = vi.fn();
    const stopScenario = vi.fn();
    const studio = await mountCharacterRobotStudio(document.querySelector("#app")!, {
      getContext: async () => current,
      getScenario,
      setSelection: async ({ node_id }) => node_id,
      registerTools: async () => false,
      createViewer: () => ({
        loadPreview: vi.fn(),
        clearPreview: vi.fn(),
        selectNode: vi.fn(),
        playScenario,
        stopScenario,
        destroy: vi.fn(),
      }),
    });

    (document.querySelector(".crs-scenario-button") as HTMLButtonElement).click();
    await vi.waitFor(() => expect(getScenario).toHaveBeenCalledOnce());
    current = structuredClone(current);
    current.draft!.draftHash = "e".repeat(64);
    await studio.refresh();
    resolveScenario({
      scenarioId: "greet",
      durationS: 1,
      evidenceLevel: "concept_only",
      keyframes: [{
        timeS: 0,
        wheels: { leftCommand: 0, rightCommand: 0 },
        neck: { panDeg: 0, tiltDeg: 0 },
        face: { expression: "neutral" },
        soundCue: null,
      }],
    });
    await Promise.resolve();

    expect(stopScenario).toHaveBeenCalled();
    expect(playScenario).not.toHaveBeenCalled();
    studio.destroy();
  });
});
