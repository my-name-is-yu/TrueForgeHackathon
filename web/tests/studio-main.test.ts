import { afterEach, describe, expect, it, vi } from "vitest";

import {
  mountCharacterRobotStudio,
  type CharacterRobotStudioDependencies,
} from "../src/studio/main";
import { StudioApiError } from "../src/studio/api";
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
    specHash: "d".repeat(64),
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
    buildSubjectHash: "a".repeat(64),
    geometrySha256: "e".repeat(64),
    profileId: "m5-cores3-goplus2/v1",
    profileSha256: "9".repeat(64),
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
      specHash: "d".repeat(64),
      durationS: 0.8,
      evidenceLevel: "concept_only",
      keyframes: [],
    });
    const preparedPack = buildPack();
    const prepareBuildPack = vi.fn().mockResolvedValue(preparedPack);
    const importProject = vi.fn().mockResolvedValue(undefined);
    const setSelection = vi.fn().mockResolvedValue("beak");
    let getBuildPackResponseIdentity!: Parameters<NonNullable<
      CharacterRobotStudioDependencies["registerTools"]
    >>[1];

    const studio = await mountCharacterRobotStudio(document.querySelector("#app")!, {
      getContext: async () => context(),
      getScenario,
      prepareBuildPack,
      importProject,
      confirmImport: () => true,
      setSelection,
      registerTools: async (_document, getIdentity) => {
        getBuildPackResponseIdentity = getIdentity;
        return true;
      },
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
        buildPackTarget: {
          ...getBuildPackResponseIdentity()!,
          revisionId: "r003",
          specHash: "b".repeat(64),
        },
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
    expect(document.querySelector(".crs-manifest")?.textContent).toContain(
      `build subject sha256 ${"a".repeat(64)}`,
    );
    expect(document.querySelector(".crs-manifest")?.textContent).toContain(
      `profile sha256 ${"9".repeat(64)}`,
    );
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

  it.each(["within_qualified_profile", "exact_build_verified"] as const)(
    "keeps an unvalidated design at concept-only evidence for a %s profile",
    async (profileEvidenceLevel) => {
      document.body.innerHTML = '<main id="app"></main>';
      const current = context();
      current.profiles[0].evidenceLevel = profileEvidenceLevel;
      current.latestValidation = null;
      const studio = await mountCharacterRobotStudio(document.querySelector("#app")!, {
        getContext: async () => current,
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

      expect(document.querySelector("#crs-evidence")?.textContent).toBe("Concept only");
      expect(document.querySelector("#crs-evidence-detail")?.textContent).toBe(
        "Digital concept — not build or safety verified",
      );
      studio.destroy();
    },
  );

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

  it("reloads a reused GLB URL when the active spec changes", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let current = context();
    current.preview.glbUrl = "/api/studio/v1/artifacts/shared-preview";
    const loadPreview = vi.fn().mockResolvedValue(undefined);
    const studio = await mountCharacterRobotStudio(document.querySelector("#app")!, {
      getContext: async () => current,
      setSelection: async ({ node_id }) => node_id,
      registerTools: async () => false,
      createViewer: () => ({
        loadPreview,
        clearPreview: vi.fn(),
        selectNode: vi.fn(),
        playScenario: vi.fn(),
        stopScenario: vi.fn(),
        destroy: vi.fn(),
      }),
    });
    await vi.waitFor(() => expect(loadPreview).toHaveBeenCalledOnce());

    current = structuredClone(current);
    current.draft!.draftHash = "e".repeat(64);
    current.draft!.specHash = "f".repeat(64);
    current.draft!.spec.face = {
      defaultExpression: "thinking",
      supportedExpressions: ["thinking", "delighted"],
    };
    await studio.refresh();

    await vi.waitFor(() => expect(loadPreview).toHaveBeenCalledTimes(2));
    expect(loadPreview).toHaveBeenLastCalledWith(
      "/api/studio/v1/artifacts/shared-preview",
      expect.objectContaining({
        face: {
          defaultExpression: "thinking",
          supportedExpressions: ["thinking", "delighted"],
        },
      }),
    );
    studio.destroy();
  });

  it("clears committed geometry and selection when an active draft has no preview", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const committed = context();
    committed.draft = null;
    committed.currentSpec = context().draft!.spec;
    committed.preview.glbUrl = "/api/studio/v1/artifacts/committed-preview";
    committed.selectedNodeId = "beak";
    let current = committed;
    const loadPreview = vi.fn().mockResolvedValue(undefined);
    const clearPreview = vi.fn();
    const selectNode = vi.fn();
    const studio = await mountCharacterRobotStudio(document.querySelector("#app")!, {
      getContext: async () => current,
      setSelection: async ({ node_id }) => node_id,
      registerTools: async () => false,
      createViewer: () => ({
        loadPreview,
        clearPreview,
        selectNode,
        playScenario: vi.fn(),
        stopScenario: vi.fn(),
        destroy: vi.fn(),
      }),
    });
    await vi.waitFor(() => expect(loadPreview).toHaveBeenCalledOnce());

    current = context();
    current.selectedNodeId = "beak";
    await studio.refresh();

    expect(clearPreview).toHaveBeenCalledOnce();
    expect(selectNode).toHaveBeenLastCalledWith(null);
    expect(document.querySelector("#crs-view-status")?.textContent).toBe("Waiting for preview");
    expect(loadPreview).toHaveBeenCalledOnce();
    studio.destroy();
  });

  it("gets a fresh durable generation before importing and refreshes after the import", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const initial = context();
    const fresh = structuredClone(initial);
    fresh.projectGeneration = 9;
    const getContext = vi.fn()
      .mockResolvedValueOnce(initial)
      .mockResolvedValueOnce(fresh)
      .mockResolvedValue(fresh);
    const importProject = vi.fn().mockResolvedValue(undefined);
    const studio = await mountCharacterRobotStudio(document.querySelector("#app")!, {
      getContext,
      importProject,
      confirmImport: () => true,
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

    const input = document.querySelector<HTMLInputElement>("#crs-import-project")!;
    const file = new File(["{}"], "shared-project.json", { type: "application/json" });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    input.dispatchEvent(new Event("change", { bubbles: true }));

    await vi.waitFor(() => expect(importProject).toHaveBeenCalledWith(file, 9));
    expect(getContext).toHaveBeenCalledTimes(3);
    expect(document.querySelector("#crs-revision")?.textContent).toContain("saved g9");
    studio.destroy();
  });

  it("ignores an older refresh that resolves after import preflight", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const initial = context();
    const fresh = structuredClone(initial);
    fresh.projectGeneration = 9;
    let releaseOlderRefresh!: (value: StudioContext) => void;
    const olderRefresh = new Promise<StudioContext>((resolve) => {
      releaseOlderRefresh = resolve;
    });
    const getContext = vi.fn()
      .mockResolvedValueOnce(initial)
      .mockReturnValueOnce(olderRefresh)
      .mockResolvedValue(fresh);
    const importProject = vi.fn().mockResolvedValue(undefined);
    const studio = await mountCharacterRobotStudio(document.querySelector("#app")!, {
      getContext,
      importProject,
      confirmImport: () => true,
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

    const olderRefreshPromise = studio.refresh();
    const input = document.querySelector<HTMLInputElement>("#crs-import-project")!;
    const file = new File(["{}"], "shared-project.json", { type: "application/json" });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    input.dispatchEvent(new Event("change", { bubbles: true }));

    await vi.waitFor(() => expect(importProject).toHaveBeenCalledWith(file, 9));
    releaseOlderRefresh(initial);
    await olderRefreshPromise;
    expect(document.querySelector("#crs-revision")?.textContent).toContain("saved g9");
    studio.destroy();
  });

  it("refreshes after a stale import so the same file can be selected again", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const current = context();
    const getContext = vi.fn().mockResolvedValue(current);
    const importProject = vi.fn()
      .mockRejectedValueOnce(new StudioApiError(
        "STALE_PROJECT",
        "The project changed before import.",
        409,
        true,
        "Refresh and retry the import.",
      ))
      .mockResolvedValueOnce(undefined);
    const studio = await mountCharacterRobotStudio(document.querySelector("#app")!, {
      getContext,
      importProject,
      confirmImport: () => true,
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

    const input = document.querySelector<HTMLInputElement>("#crs-import-project")!;
    const file = new File(["{}"], "shared-project.json", { type: "application/json" });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await vi.waitFor(() => expect(importProject).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(getContext).toHaveBeenCalledTimes(3));

    input.dispatchEvent(new Event("change", { bubbles: true }));
    await vi.waitFor(() => expect(importProject).toHaveBeenCalledTimes(2));
    studio.destroy();
  });

  it("invalidates build packs by spec identity and ignores stale responses", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let current = context();
    let resolveBuildPack!: (result: BuildPackResult) => void;
    const prepareBuildPack = vi.fn(() => new Promise<BuildPackResult>((resolve) => {
      resolveBuildPack = resolve;
    }));
    let getBuildPackResponseIdentity!: Parameters<NonNullable<
      CharacterRobotStudioDependencies["registerTools"]
    >>[1];
    const studio = await mountCharacterRobotStudio(document.querySelector("#app")!, {
      getContext: async () => current,
      prepareBuildPack,
      setSelection: async ({ node_id }) => node_id,
      registerTools: async (_document, getIdentity) => {
        getBuildPackResponseIdentity = getIdentity;
        return false;
      },
      createViewer: () => ({
        loadPreview: vi.fn(),
        clearPreview: vi.fn(),
        selectNode: vi.fn(),
        playScenario: vi.fn(),
        stopScenario: vi.fn(),
        destroy: vi.fn(),
      }),
    });

    const initialResponseIdentity = getBuildPackResponseIdentity()!;
    document.dispatchEvent(new CustomEvent(STUDIO_CHANGED_EVENT, {
      detail: {
        tool: "prepare_build_pack",
        ok: true,
        buildPackTarget: {
          ...initialResponseIdentity,
          revisionId: "r003",
          specHash: "b".repeat(64),
        },
        buildPackResult: buildPack(),
      },
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
      detail: {
        tool: "prepare_build_pack",
        ok: true,
        buildPackTarget: {
          ...initialResponseIdentity,
          revisionId: "r003",
          specHash: "b".repeat(64),
        },
        buildPackResult: buildPack(),
      },
    }));
    await Promise.resolve();
    expect(document.querySelector(".crs-manifest")).toBeNull();

    document.dispatchEvent(new CustomEvent(STUDIO_CHANGED_EVENT, {
      detail: {
        tool: "prepare_build_pack",
        ok: true,
        buildPackTarget: {
          ...initialResponseIdentity,
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

  it("ignores delayed direct and WebMCP build packs across an identity-preserving import", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const initial = context();
    initial.draft = null;
    initial.currentSpec = context().draft!.spec;
    const imported = structuredClone(initial);
    imported.projectGeneration += 1;
    imported.currentSpec!.designBrief = "Imported history for the same r003 revision and Spec.";
    const afterPack = structuredClone(imported);
    afterPack.projectGeneration += 2;
    afterPack.artifactManifestCount += 1;
    let resolvePostImportContext!: (value: StudioContext) => void;
    const postImportContext = new Promise<StudioContext>((resolve) => {
      resolvePostImportContext = resolve;
    });
    const getContext = vi.fn()
      .mockResolvedValueOnce(initial)
      .mockResolvedValueOnce(initial)
      .mockReturnValueOnce(postImportContext)
      .mockResolvedValue(afterPack);
    let resolveStalePack!: (result: BuildPackResult) => void;
    const freshPack = structuredClone(buildPack());
    freshPack.manifest!.manifestHash = "1".repeat(64);
    freshPack.artifacts[0].fileName = "fresh-pico-body.stl";
    const prepareBuildPack = vi.fn()
      .mockImplementationOnce(() => new Promise<BuildPackResult>((resolve) => {
        resolveStalePack = resolve;
      }))
      .mockResolvedValueOnce(freshPack);
    const importProject = vi.fn().mockResolvedValue(undefined);
    let getBuildPackResponseIdentity!: Parameters<NonNullable<
      CharacterRobotStudioDependencies["registerTools"]
    >>[1];
    const studio = await mountCharacterRobotStudio(document.querySelector("#app")!, {
      getContext,
      prepareBuildPack,
      importProject,
      confirmImport: () => true,
      setSelection: async ({ node_id }) => node_id,
      registerTools: async (_document, getIdentity) => {
        getBuildPackResponseIdentity = getIdentity;
        return false;
      },
      createViewer: () => ({
        loadPreview: vi.fn(),
        clearPreview: vi.fn(),
        selectNode: vi.fn(),
        playScenario: vi.fn(),
        stopScenario: vi.fn(),
        destroy: vi.fn(),
      }),
    });

    const staleResponseIdentity = getBuildPackResponseIdentity()!;
    (document.querySelector("#crs-prepare-pack") as HTMLButtonElement).click();
    await vi.waitFor(() => expect(prepareBuildPack).toHaveBeenCalledOnce());

    const input = document.querySelector<HTMLInputElement>("#crs-import-project")!;
    const file = new File(["{}"], "shared-project.json", { type: "application/json" });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await vi.waitFor(() => expect(importProject).toHaveBeenCalledWith(file, 7));
    await vi.waitFor(() => expect(getContext).toHaveBeenCalledTimes(3));

    resolveStalePack(buildPack());
    await vi.waitFor(() => {
      expect(document.querySelector<HTMLButtonElement>("#crs-prepare-pack")?.textContent)
        .toBe("Prepare build pack");
    });
    expect(document.querySelector(".crs-manifest")).toBeNull();
    expect(document.querySelector(".crs-artifact")).toBeNull();

    resolvePostImportContext(imported);
    await vi.waitFor(() => {
      expect(document.querySelector("#crs-revision")?.textContent).toContain("saved g8");
    });
    expect(document.querySelector("#crs-brief")?.textContent).toContain("Imported history");
    expect(initial.headRevisionId).toBe(imported.headRevisionId);
    expect(initial.headSpecSha256).toBe(imported.headSpecSha256);
    document.dispatchEvent(new CustomEvent(STUDIO_CHANGED_EVENT, {
      detail: {
        tool: "prepare_build_pack",
        ok: true,
        buildPackTarget: {
          ...staleResponseIdentity,
          revisionId: "r003",
          specHash: "b".repeat(64),
        },
        buildPackResult: buildPack(),
      },
    }));
    await Promise.resolve();
    expect(document.querySelector(".crs-manifest")).toBeNull();

    (document.querySelector("#crs-prepare-pack") as HTMLButtonElement).click();
    await vi.waitFor(() => expect(prepareBuildPack).toHaveBeenCalledTimes(2));
    await vi.waitFor(() => {
      expect(document.querySelector(".crs-manifest")?.textContent).toContain("1".repeat(64));
    });
    await vi.waitFor(() => {
      expect(document.querySelector("#crs-revision")?.textContent).toContain("saved g10");
    });
    expect(document.querySelector(".crs-artifact")?.textContent).toContain("fresh-pico-body.stl");
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
    current.draft!.specHash = "f".repeat(64);
    await studio.refresh();
    resolveScenario({
      scenarioId: "greet",
      specHash: "d".repeat(64),
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

  it("keeps scenario playback running when its audit record advances generation", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const initial = context();
    const refreshed = structuredClone(initial);
    refreshed.projectGeneration += 1;
    const getContext = vi.fn()
      .mockResolvedValueOnce(initial)
      .mockResolvedValue(refreshed);
    const playScenario = vi.fn();
    const stopScenario = vi.fn();
    const studio = await mountCharacterRobotStudio(document.querySelector("#app")!, {
      getContext,
      getScenario: async () => ({
        scenarioId: "greet",
        specHash: "d".repeat(64),
        durationS: 1,
        evidenceLevel: "concept_only",
        keyframes: [{
          timeS: 0,
          wheels: { leftCommand: 0, rightCommand: 0 },
          neck: { panDeg: 0, tiltDeg: 0 },
          face: { expression: "neutral" },
          soundCue: null,
        }],
      }),
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
    const stopCountAfterMount = stopScenario.mock.calls.length;

    (document.querySelector(".crs-scenario-button") as HTMLButtonElement).click();
    await vi.waitFor(() => expect(playScenario).toHaveBeenCalledOnce());
    await vi.waitFor(() => {
      expect(document.querySelector("#crs-revision")?.textContent).toContain("saved g8");
    });

    expect(stopScenario).toHaveBeenCalledTimes(stopCountAfterMount);
    studio.destroy();
  });

  it("invalidates a scenario while an imported project context is still refreshing", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    let current = context();
    current.draft = null;
    current.currentSpec = context().draft!.spec;
    let resolvePostImportContext!: (value: StudioContext) => void;
    const postImportContext = new Promise<StudioContext>((resolve) => {
      resolvePostImportContext = resolve;
    });
    const getContext = vi.fn()
      .mockResolvedValueOnce(current)
      .mockResolvedValueOnce(current)
      .mockReturnValueOnce(postImportContext);
    let resolveScenario!: (scenario: ScenarioPreview) => void;
    const getScenario = vi.fn(() => new Promise<ScenarioPreview>((resolve) => {
      resolveScenario = resolve;
    }));
    const importProject = vi.fn().mockResolvedValue(undefined);
    const playScenario = vi.fn();
    const stopScenario = vi.fn();
    const studio = await mountCharacterRobotStudio(document.querySelector("#app")!, {
      getContext,
      getScenario,
      importProject,
      confirmImport: () => true,
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

    const input = document.querySelector<HTMLInputElement>("#crs-import-project")!;
    const file = new File(["{}"], "shared-project.json", { type: "application/json" });
    Object.defineProperty(input, "files", { value: [file], configurable: true });
    input.dispatchEvent(new Event("change", { bubbles: true }));
    await vi.waitFor(() => expect(importProject).toHaveBeenCalledWith(file, 7));
    await vi.waitFor(() => expect(getContext).toHaveBeenCalledTimes(3));

    resolveScenario({
      scenarioId: "greet",
      specHash: "b".repeat(64),
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

    current = structuredClone(current);
    current.projectGeneration += 1;
    current.currentSpec!.designBrief = "Imported behavior for the same r003 revision ID.";
    resolvePostImportContext(current);
    await vi.waitFor(() => {
      expect(document.querySelector("#crs-revision")?.textContent).toContain("saved g8");
    });
    expect(current.headRevisionId).toBe("r003");
    expect(stopScenario).toHaveBeenCalled();
    expect(playScenario).not.toHaveBeenCalled();
    studio.destroy();
  });

  it("rejects a scenario response for a different spec and restores its control", async () => {
    document.body.innerHTML = '<main id="app"></main>';
    const current = context();
    current.draft = null;
    current.currentSpec = context().draft!.spec;
    const playScenario = vi.fn();
    const studio = await mountCharacterRobotStudio(document.querySelector("#app")!, {
      getContext: async () => current,
      getScenario: async () => ({
        scenarioId: "greet",
        specHash: "e".repeat(64),
        durationS: 1,
        evidenceLevel: "concept_only",
        keyframes: [{
          timeS: 0,
          wheels: { leftCommand: 0, rightCommand: 0 },
          neck: { panDeg: 0, tiltDeg: 0 },
          face: { expression: "neutral" },
          soundCue: null,
        }],
      }),
      setSelection: async ({ node_id }) => node_id,
      registerTools: async () => false,
      createViewer: () => ({
        loadPreview: vi.fn(),
        clearPreview: vi.fn(),
        selectNode: vi.fn(),
        playScenario,
        stopScenario: vi.fn(),
        destroy: vi.fn(),
      }),
    });

    const button = document.querySelector<HTMLButtonElement>(".crs-scenario-button")!;
    button.click();
    await vi.waitFor(() => {
      expect(document.querySelector("#crs-motion")?.textContent).toContain(
        "Scenario preview spec_hash does not match the requested design.",
      );
    });

    expect(button.disabled).toBe(false);
    expect(button.classList.contains("active")).toBe(false);
    expect(playScenario).not.toHaveBeenCalled();
    studio.destroy();
  });
});
