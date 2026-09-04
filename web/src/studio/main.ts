import {
  callStudioTool,
  getScenarioPreview,
  getStudioContext,
  importStudioProject,
  prepareStudioBuildPack,
  setStudioSelection,
  StudioApiError,
  StudioContractError,
  type JsonObject,
} from "./api";
import { installStudioStyles } from "./styles";
import type {
  CharacterSpecView,
  BuildPackResult,
  DesignTarget,
  EvidenceLevel,
  HardwareProfileSummary,
  ScenarioPlaybackFrame,
  ScenarioPreview,
  StudioContext,
  StudioWarning,
} from "./types";
import { createStudioViewer, type StudioViewer } from "./viewer";
import {
  designTargetsEqual,
  inspectDesignVisuals,
  VisualInspectionCaptureError,
  visualInspectionToolResult,
  type StudioVisualInspection,
} from "./visual-inspection";
import {
  registerStudioWebMcpTools,
  STUDIO_CHANGED_EVENT,
  type StudioChangedDetail,
  type StudioOperationIdentity,
  type StudioVisualInspectionRunner,
} from "./webmcp";

export type CharacterRobotStudio = {
  refresh(): Promise<void>;
  destroy(): void;
};

export type CharacterRobotStudioDependencies = {
  getContext?: () => Promise<StudioContext>;
  getScenario?: (input: JsonObject) => Promise<ScenarioPreview>;
  prepareBuildPack?: (input: JsonObject) => Promise<BuildPackResult>;
  importProject?: (file: File, expectedGeneration: number) => Promise<void>;
  confirmImport?: () => boolean;
  setSelection?: (input: { target: DesignTarget; node_id: string | null }) => Promise<string | null>;
  registerTools?: (
    document_: Document,
    getOperationIdentity: () => StudioOperationIdentity | null,
    inspectVisuals?: StudioVisualInspectionRunner,
  ) => Promise<boolean>;
  captureVisualInspection?: (
    input: JsonObject,
    result: JsonObject,
  ) => Promise<StudioVisualInspection>;
  inspectDesign?: (input: JsonObject) => Promise<JsonObject>;
  createViewer?: (
    container: HTMLElement,
    options: {
      onSelectionChange(nodeId: string | null): void;
      onLoadStateChange(state: "loading" | "ready" | "error", message?: string): void;
    },
  ) => StudioViewer;
};

const EVIDENCE_COPY: Record<EvidenceLevel, { label: string; detail: string }> = {
  concept_only: {
    label: "Concept only",
    detail: "Digital concept — not build or safety verified",
  },
  digital_checks_passed: {
    label: "Digital checks passed",
    detail: "Automated geometry checks passed; physical behavior is unverified",
  },
  within_qualified_profile: {
    label: "Within qualified profile",
    detail: "Inside a measured hardware profile; this exact build is unverified",
  },
  exact_build_verified: {
    label: "Exact build verified",
    detail: "This exact build subject has a recorded physical build",
  },
};

const create = <K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string,
): HTMLElementTagNameMap[K] => {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text !== undefined) element.textContent = text;
  return element;
};

const isJsonObject = (value: unknown): value is JsonObject => (
  typeof value === "object" && value !== null && !Array.isArray(value)
);

const shortHash = (value: string | null): string => (
  value ? `${value.slice(0, 10)}…${value.slice(-6)}` : "No committed hash"
);

const activeSpec = (context: StudioContext): CharacterSpecView | null => (
  context.draft?.spec ?? context.currentSpec
);

const activeProfile = (
  context: StudioContext,
  spec: CharacterSpecView | null,
): HardwareProfileSummary | null => (
  context.profiles.find((profile) => profile.profileId === spec?.hardwareProfileId) ?? null
);

const activeEvidence = (
  context: StudioContext,
): EvidenceLevel => context.latestValidation?.evidenceLevel ?? "concept_only";

const designTarget = (context: StudioContext): DesignTarget | null => (
  context.draft
    ? { kind: "draft", draft_hash: context.draft.draftHash }
    : context.headRevisionId
      ? { kind: "revision", revision_id: context.headRevisionId }
      : null
);

const activeSpecHash = (context: StudioContext): string | null => (
  context.draft?.specHash ?? context.headSpecSha256
);

const previewBindingKey = (context: StudioContext | null): string | null => (
  context?.preview.glbUrl
    ? JSON.stringify([context.preview.glbUrl, context.preview.glbSha256])
    : null
);

const designTargetKey = (context: StudioContext | null): string | null => {
  if (!context) return null;
  const target = designTarget(context);
  if (!target) return null;
  return JSON.stringify([
    context.projectId,
    target.kind,
    target.kind === "draft" ? target.draft_hash : target.revision_id,
    activeSpecHash(context),
  ]);
};

const buildPackDisplayTargetKey = (context: StudioContext): string | null => (
  context.headRevisionId && context.headSpecSha256
    ? JSON.stringify([context.projectId, context.headRevisionId, context.headSpecSha256])
    : null
);

const template = `
  <div class="crs-shell">
    <header class="crs-header">
      <div>
        <p class="crs-kicker">Natural language → build evidence</p>
        <h1>Character Robot <em>Studio</em></h1>
      </div>
      <div class="crs-top-status">
        <span id="crs-evidence" class="crs-evidence-badge concept_only">Concept only</span>
        <span id="crs-webmcp" class="crs-pill">Checking site tools</span>
      </div>
    </header>

    <main class="crs-layout">
      <section class="crs-panel" aria-label="Live robot design">
        <div class="crs-panel-head">
          <div><p class="crs-section-label">Live design</p><strong id="crs-revision">No revision</strong></div>
          <span id="crs-view-status" class="crs-pill">Waiting for preview</span>
        </div>
        <div id="crs-stage" class="crs-stage">
          <div id="crs-selection" class="crs-selection" role="status" aria-live="polite">Select a visible part to inspect it</div>
          <div id="crs-view-state" class="crs-view-state">
            <strong>No compiled preview yet</strong>
            Ask Codex to create a typed design draft. The page only displays compiler-provided GLB geometry.
          </div>
        </div>
        <div class="crs-stage-footer">
          <div id="crs-scenarios" class="crs-scenario-buttons" aria-label="Behavior scenarios"></div>
          <div class="crs-timeline">
            <div id="crs-timeline-track" class="crs-timeline-track" role="progressbar" aria-label="Scenario playback" aria-valuemin="0" aria-valuemax="100" aria-valuenow="0"></div>
            <div class="crs-timeline-meta"><span id="crs-motion" aria-live="polite">No scenario selected</span><span id="crs-time">0.0s</span></div>
          </div>
          <button id="crs-inspect-views" class="crs-inspect-button" type="button" disabled>Inspect 4 views</button>
        </div>
        <section id="crs-visual-inspection" class="crs-visual-inspection" aria-live="polite" hidden>
          <div class="crs-inspection-head">
            <div><p class="crs-section-label">Codex visual evidence</p><strong id="crs-inspection-source">No inspection</strong></div>
            <span id="crs-inspection-status" class="crs-pill">Not inspected</span>
          </div>
          <div id="crs-inspection-views" class="crs-inspection-views"></div>
          <div id="crs-inspection-diagnostics" class="crs-inspection-diagnostics"></div>
        </section>
      </section>

      <aside class="crs-sidebar">
        <section class="crs-panel">
          <div class="crs-section">
            <div class="crs-section-title"><h2>Design draft</h2><span id="crs-draft-state" class="crs-pill">Empty</span></div>
            <h3 id="crs-name" class="crs-draft-name">Waiting for a character</h3>
            <p id="crs-role" class="crs-draft-role">No design brief yet</p>
            <p id="crs-brief" class="crs-draft-brief">Describe a character to Codex to begin from zero.</p>
            <span id="crs-hash" class="crs-hash">—</span>
          </div>
          <div class="crs-section">
            <div class="crs-section-title"><h2>Hardware profile</h2></div>
            <div id="crs-profile"></div>
          </div>
          <div class="crs-section">
            <div class="crs-section-title"><h2>Semantic parts</h2><span id="crs-part-count" class="crs-section-label">0 parts</span></div>
            <div id="crs-parts" class="crs-part-list"></div>
          </div>
          <div class="crs-section">
            <div class="crs-section-title"><h2>Evidence & warnings</h2></div>
            <div id="crs-warnings" class="crs-warning-list" role="status" aria-live="polite"></div>
          </div>
          <div class="crs-section">
            <div class="crs-section-title"><h2>Evidence-gated build pack</h2></div>
            <button id="crs-prepare-pack" class="crs-build-button" type="button" disabled>Prepare build pack</button>
            <label class="crs-import-button">Replace with shared project…<input id="crs-import-project" type="file" accept="application/json,.json" /></label>
            <p id="crs-build-copy" class="crs-build-copy">Commit a revision before preparing artifacts. Nothing downloads automatically.</p>
            <div id="crs-artifacts" class="crs-artifact-list"></div>
          </div>
        </section>
      </aside>

      <footer class="crs-footnote">
        <span id="crs-evidence-detail">Digital concept — not build or safety verified</span>
        <span>Download, fabrication and hardware writes require a human action.</span>
      </footer>
    </main>
  </div>
`;

export async function mountCharacterRobotStudio(
  root: HTMLElement = document.querySelector<HTMLElement>("#app")!,
  dependencies: CharacterRobotStudioDependencies = {},
): Promise<CharacterRobotStudio> {
  if (!root) throw new Error("Character Robot Studio requires a mount element");
  root.dataset.characterRobotStudio = "";
  root.innerHTML = template;
  installStudioStyles(root);

  const getContext = dependencies.getContext ?? getStudioContext;
  const getScenario = dependencies.getScenario ?? getScenarioPreview;
  const prepareBuildPack = dependencies.prepareBuildPack ?? prepareStudioBuildPack;
  const importProject = dependencies.importProject ?? importStudioProject;
  const confirmImport = dependencies.confirmImport ?? (() => window.confirm(
    "Replace this Studio project with the selected shared revision history? The current draft will be removed.",
  ));
  const setSelection = dependencies.setSelection ?? setStudioSelection;
  const registerTools = dependencies.registerTools ?? registerStudioWebMcpTools;
  const inspectDesign = dependencies.inspectDesign ?? (async (input: JsonObject) => {
    const result = await callStudioTool("inspect_design", input);
    if (typeof result !== "object" || result === null || Array.isArray(result)) {
      throw new StudioContractError("inspect_design result must be an object");
    }
    return result as JsonObject;
  });

  const query = <T extends HTMLElement>(selector: string): T => {
    const element = root.querySelector<T>(selector);
    if (!element) throw new Error(`Missing Character Robot Studio element: ${selector}`);
    return element;
  };

  const stage = query<HTMLElement>("#crs-stage");
  const viewState = query<HTMLElement>("#crs-view-state");
  const viewStatus = query<HTMLElement>("#crs-view-status");
  const selection = query<HTMLElement>("#crs-selection");
  let context: StudioContext | null = null;
  let selectedNodeId: string | null = null;
  let loadedGlbUrl: string | null = null;
  let loadedPreviewKey: string | null = null;
  let refreshSequence = 0;
  let selectionSequence = 0;
  let scenarioSequence = 0;
  let inspectionSequence = 0;
  let manualInspectionSequence = 0;
  let manualInspectionPending = false;
  let designOperationEpoch = 0;
  let destroyed = false;
  let preparedForBuildPackTarget: string | null = null;
  let renderedTargetKey: string | null = null;
  let transientToolWarning: StudioWarning | null = null;

  const setViewState = (
    state: "loading" | "ready" | "error",
    message?: string,
  ): void => {
    viewStatus.className = `crs-pill ${state === "ready" ? "ready" : state === "error" ? "error" : ""}`;
    viewStatus.textContent = state === "loading"
      ? "Compiling preview"
      : state === "ready" ? "GLB preview ready" : "Preview unavailable";
    if (state === "error") {
      viewState.hidden = false;
      viewState.innerHTML = "";
      viewState.append(
        create("strong", undefined, "Preview could not be displayed"),
        document.createTextNode(message ?? "The compiler did not provide a usable GLB."),
      );
    } else if (state === "ready" && loadedGlbUrl) {
      viewState.hidden = true;
    }
  };

  const renderSelection = (): void => {
    root.querySelectorAll<HTMLButtonElement>(".crs-part-button").forEach((button) => {
      const active = selectedNodeId !== null && button.dataset.nodeId === selectedNodeId;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", String(active));
    });
    if (!context || !selectedNodeId) {
      selection.textContent = "Select a visible part to inspect it";
      return;
    }
    const node = activeSpec(context)?.morphologyNodes.find((item) => item.nodeId === selectedNodeId);
    selection.textContent = node
      ? `${node.label} · ${node.role.replaceAll("_", " ")} · ${node.nodeId}`
      : selectedNodeId;
  };

  const viewerFactory = dependencies.createViewer ?? createStudioViewer;
  const viewer = viewerFactory(stage, {
    onSelectionChange(nodeId) {
      selectedNodeId = nodeId;
      renderSelection();
      void persistSelection(nodeId);
    },
    onLoadStateChange: setViewState,
  });

  const visualInspectionPanel = query<HTMLElement>("#crs-visual-inspection");

  const updateInspectionButton = (): void => {
    const button = query<HTMLButtonElement>("#crs-inspect-views");
    button.disabled = manualInspectionPending || !context || designTarget(context) === null;
    button.textContent = manualInspectionPending ? "Capturing views…" : "Inspect 4 views";
  };

  const clearVisualInspection = (): void => {
    inspectionSequence += 1;
    visualInspectionPanel.hidden = true;
    query<HTMLElement>("#crs-inspection-views").replaceChildren();
    query<HTMLElement>("#crs-inspection-diagnostics").replaceChildren();
  };

  const renderVisualInspection = (inspection: StudioVisualInspection): void => {
    visualInspectionPanel.hidden = false;
    const status = query<HTMLElement>("#crs-inspection-status");
    const source = query<HTMLElement>("#crs-inspection-source");
    const views = query<HTMLElement>("#crs-inspection-views");
    const diagnostics = query<HTMLElement>("#crs-inspection-diagnostics");
    views.replaceChildren();
    diagnostics.replaceChildren();
    status.className = `crs-pill ${inspection.status === "ready" ? "ready" : "error"}`;
    status.textContent = inspection.status === "ready" ? "4 views captured" : "Unavailable";
    if (inspection.source) {
      const target = inspection.source.target.kind === "draft"
        ? `draft ${shortHash(inspection.source.target.draft_hash)}`
        : inspection.source.target.revision_id;
      source.textContent = `${target} · GLB ${shortHash(inspection.source.glbSha256)}`;
    } else {
      source.textContent = inspection.message ?? "Canonical renders are unavailable";
    }
    inspection.views.forEach((view) => {
      const figure = create("figure", "crs-inspection-view");
      const image = create("img") as HTMLImageElement;
      image.src = view.dataUrl;
      image.alt = `${view.label} canonical render of the inspected character robot`;
      image.width = view.widthPx;
      image.height = view.heightPx;
      const caption = create("figcaption");
      caption.append(
        create("strong", undefined, view.label),
        create("span", undefined, "Fixed camera view for visual comparison"),
      );
      figure.append(image, caption);
      views.append(figure);
    });
    const relevantDiagnostics = inspection.diagnostics.filter((item) => item.severity !== "info");
    if (relevantDiagnostics.length === 0) {
      diagnostics.append(create(
        "p",
        "crs-inspection-note",
        "No structural render diagnostics. Codex must still compare these views with the requested motif and design brief.",
      ));
    } else {
      relevantDiagnostics.forEach((diagnostic) => {
        const item = create("p", `crs-inspection-note ${diagnostic.severity}`);
        item.append(
          create("strong", undefined, diagnostic.code),
          document.createTextNode(diagnostic.message),
        );
        diagnostics.append(item);
      });
    }
  };

  const unavailableVisualInspection = (code: string, message: string): StudioVisualInspection => ({
    status: "unavailable",
    code,
    message,
    renderContractVersion: "studio-render-v1",
    views: [],
    nodes: [],
    diagnostics: [{ code, severity: "warning", message }],
  });

  const captureVisualInspection = dependencies.captureVisualInspection ?? (
    async (input: JsonObject, result: JsonObject): Promise<StudioVisualInspection> => {
      const expectedPreviewUrl = context?.preview.glbUrl ?? null;
      const expectedGlbSha256 = context?.preview.glbSha256 ?? null;
      if (!expectedPreviewUrl || !expectedGlbSha256) {
        return unavailableVisualInspection(
          "VISUAL_PREVIEW_UNAVAILABLE",
          "Canonical views require the exact compiler GLB currently loaded in Studio.",
        );
      }
      return inspectDesignVisuals(input, result, async (source) => {
        if (source.glbSha256 !== expectedGlbSha256) {
          throw new VisualInspectionCaptureError(
            "VISUAL_GLB_MISMATCH",
            "The inspect_design GLB digest does not match the preview loaded in Studio.",
          );
        }
        if (!viewer.captureCanonicalViews) {
          throw new VisualInspectionCaptureError(
            "VISUAL_CAPTURE_UNAVAILABLE",
            "The shared Studio viewer does not support canonical capture.",
          );
        }
        try {
          return await viewer.captureCanonicalViews(expectedPreviewUrl);
        } catch (error) {
          throw new VisualInspectionCaptureError(
            "VISUAL_PREVIEW_MISMATCH",
            error instanceof Error ? error.message : "The loaded compiler GLB could not be captured.",
          );
        }
      });
    }
  );

  const runVisualInspection: StudioVisualInspectionRunner = async (input, result, identity) => {
    const operationKey = designOperationKey();
    const expectedSpecHash = context ? activeSpecHash(context) : null;
    const currentTarget = context ? designTarget(context) : null;
    const requested = input.target;
    const requestedTarget = isJsonObject(requested)
      && requested.kind === "draft"
      && typeof requested.draft_hash === "string"
      ? { kind: "draft" as const, draft_hash: requested.draft_hash }
      : isJsonObject(requested)
        && requested.kind === "revision"
        && typeof requested.revision_id === "string"
        ? { kind: "revision" as const, revision_id: requested.revision_id }
        : null;
    if (
      !identity
      || !matchesVisualOperationIdentity(identity)
      || !currentTarget
      || !requestedTarget
      || !designTargetsEqual(currentTarget, requestedTarget)
    ) {
      const unavailable = unavailableVisualInspection(
        "VISUAL_TARGET_MISMATCH",
        "Canonical views are only captured for the design currently shown in Studio.",
      );
      return visualInspectionToolResult(unavailable);
    }
    const sequence = ++inspectionSequence;
    const inspection = await captureVisualInspection(input, result);
    if (
      destroyed
      || !matchesVisualOperationIdentity(identity)
      || operationKey !== designOperationKey()
    ) {
      return visualInspectionToolResult(unavailableVisualInspection(
        "VISUAL_TARGET_CHANGED",
        "The active design changed while canonical views were rendering.",
      ));
    }
    if (
      inspection.status === "ready"
      && (!inspection.source || !designTargetsEqual(inspection.source.target, currentTarget))
    ) {
      const unavailable = unavailableVisualInspection(
        "VISUAL_TARGET_MISMATCH",
        "Canonical views were rendered for a different design target.",
      );
      if (sequence === inspectionSequence) renderVisualInspection(unavailable);
      return visualInspectionToolResult(unavailable);
    }
    if (
      inspection.status === "ready"
      && (!inspection.source || inspection.source.specHash !== expectedSpecHash)
    ) {
      const unavailable = unavailableVisualInspection(
        "VISUAL_SPEC_MISMATCH",
        "Canonical views were rendered for a different design specification.",
      );
      if (sequence === inspectionSequence) renderVisualInspection(unavailable);
      return visualInspectionToolResult(unavailable);
    }
    if (
      inspection.status === "ready"
      && (!inspection.source || inspection.source.glbSha256 !== context?.preview.glbSha256)
    ) {
      const unavailable = unavailableVisualInspection(
        "VISUAL_GLB_MISMATCH",
        "Canonical views were rendered from a different compiler GLB.",
      );
      if (sequence === inspectionSequence) renderVisualInspection(unavailable);
      return visualInspectionToolResult(unavailable);
    }
    if (sequence === inspectionSequence) renderVisualInspection(inspection);
    return visualInspectionToolResult(inspection);
  };

  const restoreScenarioButtons = (): void => {
    root.querySelectorAll<HTMLButtonElement>(".crs-scenario-button").forEach((item) => {
      item.disabled = false;
      item.classList.remove("active");
    });
  };

  const resetScenarioPlayback = (): void => {
    scenarioSequence += 1;
    viewer.stopScenario();
    restoreScenarioButtons();
    const track = query<HTMLElement>("#crs-timeline-track");
    track.style.setProperty("--progress", "0%");
    track.setAttribute("aria-valuenow", "0");
    query<HTMLElement>("#crs-motion").textContent = "No scenario selected";
    query<HTMLElement>("#crs-time").textContent = "0.0s";
  };

  const designOperationKey = (): string | null => {
    const targetKey = designTargetKey(context);
    return targetKey === null ? null : JSON.stringify([designOperationEpoch, targetKey]);
  };

  const studioOperationIdentity = (): StudioOperationIdentity | null => (
    context
      ? {
          projectId: context.projectId,
          projectGeneration: context.projectGeneration,
          operationEpoch: designOperationEpoch,
        }
      : null
  );

  const matchesBuildPackResponseIdentity = (
    identity: StudioOperationIdentity,
  ): boolean => {
    const currentIdentity = studioOperationIdentity();
    return currentIdentity !== null
      && identity.projectId === currentIdentity.projectId
      && identity.projectGeneration === currentIdentity.projectGeneration
      && identity.operationEpoch === currentIdentity.operationEpoch;
  };

  const matchesVisualOperationIdentity = (
    identity: StudioOperationIdentity,
  ): boolean => {
    const currentIdentity = studioOperationIdentity();
    return currentIdentity !== null
      && identity.projectId === currentIdentity.projectId
      && identity.projectGeneration === currentIdentity.projectGeneration
      && identity.operationEpoch === currentIdentity.operationEpoch;
  };

  query<HTMLButtonElement>("#crs-inspect-views").addEventListener("click", () => {
    const button = query<HTMLButtonElement>("#crs-inspect-views");
    const target = context ? designTarget(context) : null;
    const identity = studioOperationIdentity();
    const operationKey = designOperationKey();
    if (!target || !identity || button.disabled || manualInspectionPending) return;
    const sequence = ++manualInspectionSequence;
    manualInspectionPending = true;
    updateInspectionButton();
    void (async () => {
      try {
        const input = { target };
        const result = await inspectDesign(input);
        if (
          destroyed
          || sequence !== manualInspectionSequence
          || !matchesVisualOperationIdentity(identity)
          || operationKey !== designOperationKey()
        ) return;
        await runVisualInspection(input, result, identity);
      } catch (error) {
        if (
          destroyed
          || sequence !== manualInspectionSequence
          || !matchesVisualOperationIdentity(identity)
          || operationKey !== designOperationKey()
        ) return;
        renderVisualInspection({
          status: "unavailable",
          code: "VISUAL_INSPECTION_FAILED",
          message: error instanceof Error ? error.message : "Canonical views could not be captured.",
          renderContractVersion: "studio-render-v1",
          views: [],
          nodes: [],
          diagnostics: [{
            code: "VISUAL_INSPECTION_FAILED",
            severity: "error",
            message: error instanceof Error ? error.message : "Canonical views could not be captured.",
          }],
        });
      } finally {
        if (destroyed || sequence !== manualInspectionSequence) return;
        await refresh();
        if (destroyed || sequence !== manualInspectionSequence) return;
        manualInspectionPending = false;
        updateInspectionButton();
      }
    })();
  });

  async function persistSelection(nodeId: string | null): Promise<void> {
    if (!context) return;
    const target = designTarget(context);
    if (!target) return;
    const targetKey = designOperationKey();
    const sequence = ++selectionSequence;
    try {
      const persistedNodeId = await setSelection({ target, node_id: nodeId });
      if (
        destroyed
        || sequence !== selectionSequence
        || targetKey !== designOperationKey()
      ) {
        return;
      }
      const validIds = new Set(activeSpec(context)?.morphologyNodes.map((node) => node.nodeId) ?? []);
      selectedNodeId = persistedNodeId && validIds.has(persistedNodeId) ? persistedNodeId : null;
      viewer.selectNode(selectedNodeId);
      renderSelection();
    } catch (error) {
      if (destroyed || sequence !== selectionSequence) return;
      const stale = error instanceof StudioApiError
        && (error.status === 409 || error.code.startsWith("STALE_"));
      transientToolWarning = {
        code: error instanceof StudioApiError ? error.code : "SELECTION_NOT_SHARED",
        message: stale
          ? "The selected design changed before the selection could be shared. The Studio refreshed its current target."
          : error instanceof Error ? error.message : "The selected part could not be shared.",
        severity: stale ? "warning" : "error",
        suggestion: error instanceof StudioApiError ? error.nextAction : null,
      };
      selectedNodeId = null;
      viewer.selectNode(null);
      renderSelection();
      await refresh();
    }
  }

  const renderProfile = (profile: HardwareProfileSummary | null): void => {
    const holder = query<HTMLElement>("#crs-profile");
    holder.replaceChildren();
    if (!profile) {
      holder.append(create("p", "crs-empty", "No hardware profile is attached to this design."));
      return;
    }
    const card = create("article", "crs-profile-card");
    card.append(
      create("strong", undefined, profile.label),
      create("span", "crs-profile-id", profile.profileId),
    );
    const chips = create("div", "crs-chip-list");
    [...profile.components, ...profile.capabilities].slice(0, 8).forEach((item) => {
      chips.append(create("span", "crs-chip", item));
    });
    if (chips.childElementCount > 0) card.append(chips);
    if (profile.minimumEnclosureMm) {
      const { x, y, z } = profile.minimumEnclosureMm;
      card.append(create("div", "crs-measure", `Digital component envelope ${x} × ${y} × ${z} mm`));
    }
    holder.append(card);
  };

  const renderParts = (spec: CharacterSpecView | null): void => {
    const holder = query<HTMLElement>("#crs-parts");
    holder.replaceChildren();
    const nodes = spec?.morphologyNodes ?? [];
    query<HTMLElement>("#crs-part-count").textContent = `${nodes.length} ${nodes.length === 1 ? "part" : "parts"}`;
    if (nodes.length === 0) {
      holder.append(create("p", "crs-empty", "No compiled semantic parts yet."));
      return;
    }
    nodes.forEach((node) => {
      const button = create("button", "crs-part-button") as HTMLButtonElement;
      button.type = "button";
      button.dataset.nodeId = node.nodeId;
      button.setAttribute("aria-pressed", "false");
      button.append(
        create("strong", undefined, node.label),
        create("span", undefined, node.role.replaceAll("_", " ")),
      );
      button.addEventListener("click", () => {
        selectedNodeId = node.nodeId;
        viewer.selectNode(node.nodeId);
        renderSelection();
        void persistSelection(node.nodeId);
      });
      holder.append(button);
    });
  };

  const renderWarnings = (
    studioContext: StudioContext,
    profile: HardwareProfileSummary | null,
  ): void => {
    const warnings: StudioWarning[] = [
      ...(transientToolWarning ? [transientToolWarning] : []),
      ...studioContext.preview.warnings,
      ...(studioContext.latestValidation?.warnings ?? []),
      ...(studioContext.latestValidation?.checks ?? [])
        .filter((check) => (
          check.status === "info" || check.status === "warning" || check.status === "failed"
        ))
        .map((check) => ({
          code: check.code,
          message: check.message ?? check.label,
          severity: check.status === "failed"
            ? "error" as const
            : check.status === "info" ? "info" as const : "warning" as const,
          path: check.path,
          measuredValue: check.measuredValue,
          limitValue: check.limitValue,
          suggestion: check.suggestion,
        })),
      ...(profile?.unknowns ?? []).map((message, index) => ({
        code: `PROFILE_UNKNOWN_${index + 1}`,
        message,
        severity: "info" as const,
      })),
    ];
    const unique = warnings.filter((warning, index) => (
      warnings.findIndex((item) => item.code === warning.code && item.message === warning.message) === index
    ));
    const holder = query<HTMLElement>("#crs-warnings");
    holder.replaceChildren();
    if (unique.length === 0) {
      holder.append(create("p", "crs-empty", "No reported digital warnings. This is not a physical safety claim."));
      return;
    }
    unique.forEach((warning) => {
      const item = create("article", `crs-warning ${warning.severity}`);
      const copy = create("div");
      copy.append(
        create("strong", undefined, warning.code),
        create("p", undefined, warning.message),
      );
      const evidence = [
        warning.path ? `Path: ${warning.path}` : null,
        warning.measuredValue !== null && warning.measuredValue !== undefined
          ? `Measured: ${warning.measuredValue}`
          : null,
        warning.limitValue !== null && warning.limitValue !== undefined
          ? `Limit: ${warning.limitValue}`
          : null,
      ].filter((item): item is string => item !== null);
      if (evidence.length > 0) copy.append(create("p", "crs-warning-evidence", evidence.join(" · ")));
      if (warning.suggestion) {
        copy.append(create("p", "crs-warning-suggestion", `Suggested repair: ${warning.suggestion}`));
      }
      item.append(copy);
      holder.append(item);
    });
  };

  const renderPlaybackFrame = (frame: ScenarioPlaybackFrame): void => {
    const percent = Math.round(frame.progress * 100);
    const track = query<HTMLElement>("#crs-timeline-track");
    track.style.setProperty("--progress", `${percent}%`);
    track.setAttribute("aria-valuenow", String(percent));
    query<HTMLElement>("#crs-motion").textContent = [
      frame.face.expression,
      `wheels L ${frame.wheels.leftCommand.toFixed(2)} / R ${frame.wheels.rightCommand.toFixed(2)}`,
      `neck ${frame.neck.panDeg.toFixed(0)}° ${frame.neck.tiltDeg.toFixed(0)}°`,
      ...(frame.soundCue ? [`sound ${frame.soundCue}`] : []),
    ].join(" · ");
    query<HTMLElement>("#crs-time").textContent = `${frame.timeS.toFixed(1)}s`;
  };

  const runScenario = async (scenarioId: string, button: HTMLButtonElement): Promise<void> => {
    if (!context) return;
    const target = designTarget(context);
    const expectedSpecHash = activeSpecHash(context);
    if (!target || !expectedSpecHash) return;
    const targetKey = designOperationKey();
    const sequence = ++scenarioSequence;
    root.querySelectorAll<HTMLButtonElement>(".crs-scenario-button").forEach((item) => {
      item.disabled = true;
      item.classList.toggle("active", item === button);
    });
    try {
      const scenario = await getScenario({ target, scenario_id: scenarioId });
      if (
        destroyed
        || sequence !== scenarioSequence
        || targetKey !== designOperationKey()
      ) {
        return;
      }
      if (scenario.specHash !== expectedSpecHash) {
        throw new StudioContractError(
          "Scenario preview spec_hash does not match the requested design.",
        );
      }
      viewer.playScenario(scenario, renderPlaybackFrame, () => {
        if (sequence !== scenarioSequence) return;
        restoreScenarioButtons();
      });
      void refresh();
    } catch (error) {
      if (destroyed || sequence !== scenarioSequence) return;
      restoreScenarioButtons();
      query<HTMLElement>("#crs-motion").textContent = error instanceof Error ? error.message : "Scenario preview failed";
    }
  };

  const renderScenarios = (spec: CharacterSpecView | null): void => {
    const holder = query<HTMLElement>("#crs-scenarios");
    holder.replaceChildren();
    if (!spec || spec.scenarioIds.length === 0) {
      holder.append(create("span", "crs-empty", "No behavior timelines"));
      return;
    }
    spec.scenarioIds.forEach((scenarioId) => {
      const button = create("button", "crs-scenario-button", scenarioId) as HTMLButtonElement;
      button.type = "button";
      button.addEventListener("click", () => { void runScenario(scenarioId, button); });
      holder.append(button);
    });
  };

  const renderBuildPack = (result: BuildPackResult): void => {
    const holder = query<HTMLElement>("#crs-artifacts");
    holder.replaceChildren();
    const copy = query<HTMLElement>("#crs-build-copy");
    copy.textContent = result.status === "blocked"
      ? result.nextAction
      : `${result.nextAction} Choose each file explicitly; no hardware write is performed.`;
    result.blockers.forEach((blocker) => {
      const item = create("article", `crs-warning ${blocker.severity}`);
      const body = create("div");
      body.append(create("strong", undefined, blocker.code), create("p", undefined, blocker.message));
      const evidence = [
        blocker.path ? `Path: ${blocker.path}` : null,
        blocker.measuredValue !== null && blocker.measuredValue !== undefined
          ? `Measured: ${blocker.measuredValue}`
          : null,
        blocker.limitValue !== null && blocker.limitValue !== undefined
          ? `Limit: ${blocker.limitValue}`
          : null,
      ].filter((entry): entry is string => entry !== null);
      if (evidence.length > 0) body.append(create("p", "crs-warning-evidence", evidence.join(" · ")));
      if (blocker.suggestion) {
        body.append(create("p", "crs-warning-suggestion", `Suggested repair: ${blocker.suggestion}`));
      }
      item.append(body);
      holder.append(item);
    });
    if (result.status === "blocked") return;
    if (result.manifest) {
      const manifest = create("article", "crs-manifest");
      manifest.append(
        create("strong", undefined, `Manifest ${result.manifest.revisionId}`),
        create(
          "span",
          undefined,
          `${result.manifest.profileId} · ${result.manifest.evidenceLevel.replaceAll("_", " ")}`,
        ),
        create(
          "span",
          undefined,
          [
            `catalog ${result.manifest.catalogVersion}`,
            `compiler ${result.manifest.compilerVersion}`,
            `CAD ${result.manifest.cadEngineVersion}`,
            ...(result.manifest.simulationEngineVersion
              ? [`MuJoCo ${result.manifest.simulationEngineVersion}`]
              : []),
            `runtime ${result.manifest.firmwareRuntimeVersion}`,
          ].join(" · "),
        ),
        create("code", undefined, `manifest sha256 ${result.manifest.manifestHash}`),
        create("code", undefined, `build subject sha256 ${result.manifest.buildSubjectHash}`),
        create("code", undefined, `profile sha256 ${result.manifest.profileSha256}`),
        create("code", undefined, `spec sha256 ${result.manifest.specHash}`),
        create("code", undefined, `geometry sha256 ${result.manifest.geometrySha256}`),
      );
      holder.append(manifest);
    }
    result.artifacts.forEach((artifact) => {
      const link = create("a", "crs-artifact") as HTMLAnchorElement;
      link.href = artifact.downloadUrl;
      link.download = artifact.fileName;
      const description = create("span", "crs-artifact-description");
      description.append(
        create("strong", undefined, artifact.fileName),
        create(
          "span",
          undefined,
          `${artifact.kind} · ${(artifact.byteSize / 1024).toFixed(1)} KB · ${artifact.experimental ? "experimental" : "versioned"}`,
        ),
        create("code", undefined, `sha256 ${artifact.sha256}`),
      );
      link.append(description, create("span", "crs-download-action", "Download"));
      holder.append(link);
    });
  };

  const onPrepareBuildPack = async (): Promise<void> => {
    if (!context?.headRevisionId || !context.headSpecSha256) return;
    const revisionId = context.headRevisionId;
    const specHash = context.headSpecSha256;
    const requestedTarget = buildPackDisplayTargetKey(context);
    const responseIdentity = studioOperationIdentity();
    if (!responseIdentity) return;
    const button = query<HTMLButtonElement>("#crs-prepare-pack");
    button.disabled = true;
    button.textContent = "Preparing manifest…";
    query<HTMLElement>("#crs-artifacts").replaceChildren();
    try {
      const result = await prepareBuildPack({
        revision_id: revisionId,
        expected_spec_hash: specHash,
      });
      if (
        destroyed
        || !context
        || !matchesBuildPackResponseIdentity(responseIdentity)
        || buildPackDisplayTargetKey(context) !== requestedTarget
        || (result.manifest !== null && (
          result.manifest.revisionId !== revisionId || result.manifest.specHash !== specHash
        ))
      ) return;
      preparedForBuildPackTarget = requestedTarget;
      renderBuildPack(result);
      void refresh();
    } catch (error) {
      query<HTMLElement>("#crs-build-copy").textContent = error instanceof Error
        ? error.message
        : "The experimental build pack could not be prepared.";
    } finally {
      if (!destroyed) {
        button.disabled = !context?.headRevisionId || !context.headSpecSha256;
        button.textContent = "Prepare build pack";
      }
    }
  };

  query<HTMLButtonElement>("#crs-prepare-pack").addEventListener("click", () => {
    void onPrepareBuildPack();
  });
  query<HTMLInputElement>("#crs-import-project").addEventListener("change", (event) => {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    if (!file) return;
    if (!confirmImport()) {
      input.value = "";
      return;
    }
    designOperationEpoch += 1;
    resetScenarioPlayback();
    const label = input.closest<HTMLLabelElement>(".crs-import-button");
    if (label) label.classList.add("busy");
    void (async () => {
      try {
        const preflightSequence = ++refreshSequence;
        const freshContext = await getContext();
        if (destroyed || preflightSequence !== refreshSequence) return;
        renderContext(freshContext);
        await importProject(file, freshContext.projectGeneration);
        preparedForBuildPackTarget = null;
        query<HTMLElement>("#crs-artifacts").replaceChildren();
        await refresh();
      } catch (error: unknown) {
        const staleProject = error instanceof StudioApiError && error.code === "STALE_PROJECT";
        if (staleProject) {
          transientToolWarning = {
            code: error.code,
            message: error.message,
            severity: "warning",
            suggestion: error.nextAction,
          };
          await refresh();
        }
        query<HTMLElement>("#crs-build-copy").textContent = error instanceof Error
          ? error.message
          : "The shared project could not be imported.";
      } finally {
        input.value = "";
        if (label) label.classList.remove("busy");
      }
    })();
  });

  const renderContext = (nextContext: StudioContext): void => {
    const nextTargetKey = designTargetKey(nextContext);
    const targetChanged = renderedTargetKey !== nextTargetKey;
    const nextPreviewKey = previewBindingKey(nextContext);
    const previewChanged = loadedPreviewKey !== nextPreviewKey;
    if (targetChanged || previewChanged) {
      designOperationEpoch += 1;
      resetScenarioPlayback();
      clearVisualInspection();
    }
    renderedTargetKey = nextTargetKey;
    context = nextContext;
    const spec = activeSpec(nextContext);
    const profile = activeProfile(nextContext, spec);
    const evidence = activeEvidence(nextContext);
    const evidenceCopy = EVIDENCE_COPY[evidence];

    const evidenceBadge = query<HTMLElement>("#crs-evidence");
    evidenceBadge.className = `crs-evidence-badge ${evidence}`;
    evidenceBadge.textContent = evidenceCopy.label;
    query<HTMLElement>("#crs-evidence-detail").textContent = evidenceCopy.detail;
    const revisionLabel = nextContext.draft
      ? `${nextContext.headRevisionId ?? "new project"} · shared draft`
      : nextContext.headRevisionId ?? "No committed revision";
    query<HTMLElement>("#crs-revision").textContent = [
      revisionLabel,
      nextContext.storageMode === "durable"
        ? `saved g${nextContext.projectGeneration}`
        : "temporary",
    ].join(" · ");
    const draftState = query<HTMLElement>("#crs-draft-state");
    draftState.className = `crs-pill ${nextContext.draft ? "ready" : ""}`;
    draftState.textContent = nextContext.draft ? "Shared draft" : nextContext.currentSpec ? "Committed" : "Empty";
    query<HTMLElement>("#crs-name").textContent = spec?.name ?? "Waiting for a character";
    query<HTMLElement>("#crs-role").textContent = spec ? `${spec.motif} · ${spec.role}` : "No design brief yet";
    query<HTMLElement>("#crs-brief").textContent = spec?.designBrief ?? "Describe a character to Codex to begin from zero.";
    query<HTMLElement>("#crs-hash").textContent = nextContext.draft
      ? `draft ${shortHash(nextContext.draft.draftHash)} · base ${nextContext.draft.baseRevisionId ?? "none"}`
      : `spec ${shortHash(nextContext.headSpecSha256)}`;

    renderProfile(profile);
    renderParts(spec);
    renderWarnings(nextContext, profile);
    renderScenarios(spec);

    const buildButton = query<HTMLButtonElement>("#crs-prepare-pack");
    buildButton.disabled = !nextContext.headRevisionId || !nextContext.headSpecSha256;
    updateInspectionButton();
    if (targetChanged || preparedForBuildPackTarget !== buildPackDisplayTargetKey(nextContext)) {
      preparedForBuildPackTarget = null;
      query<HTMLElement>("#crs-artifacts").replaceChildren();
      query<HTMLElement>("#crs-build-copy").textContent = nextContext.headRevisionId
        ? nextContext.draft
          ? `Prepares committed ${nextContext.headRevisionId}; the current shared draft is not included. Nothing downloads automatically.`
          : `Prepares committed ${nextContext.headRevisionId}. Nothing downloads automatically.`
        : "Commit a revision before preparing artifacts. Nothing downloads automatically.";
    }

    const validIds = new Set(spec?.morphologyNodes.map((node) => node.nodeId) ?? []);
    selectedNodeId = nextContext.preview.glbUrl
      && nextContext.selectedNodeId
      && validIds.has(nextContext.selectedNodeId)
      ? nextContext.selectedNodeId
      : null;
    viewer.selectNode(selectedNodeId);
    renderSelection();

    if (previewChanged || targetChanged) {
      loadedGlbUrl = nextContext.preview.glbUrl;
      loadedPreviewKey = nextPreviewKey;
      if (!loadedGlbUrl) {
        viewer.clearPreview();
        viewState.hidden = false;
        viewState.innerHTML = "";
        viewState.append(
          create("strong", undefined, "No compiled preview yet"),
          document.createTextNode("Ask Codex to create a typed design draft. The page only displays compiler-provided GLB geometry."),
        );
        viewStatus.className = "crs-pill";
        viewStatus.textContent = "Waiting for preview";
      } else {
        const requestedGlbUrl = loadedGlbUrl;
        const requestedPreviewKey = loadedPreviewKey;
        if (spec) {
          void viewer.loadPreview(requestedGlbUrl, spec).catch((error: unknown) => {
            if (destroyed || loadedGlbUrl !== requestedGlbUrl || loadedPreviewKey !== requestedPreviewKey) return;
            loadedGlbUrl = null;
            loadedPreviewKey = null;
            viewer.clearPreview();
            setViewState(
              "error",
              error instanceof Error ? error.message : "GLB preview could not be loaded",
            );
          });
        }
      }
    }
  };

  const refresh = async (): Promise<void> => {
    const sequence = ++refreshSequence;
    try {
      const nextContext = await getContext();
      if (destroyed || sequence !== refreshSequence) return;
      renderContext(nextContext);
    } catch (error) {
      if (destroyed || sequence !== refreshSequence) return;
      viewState.hidden = false;
      viewState.innerHTML = "";
      viewState.append(
        create("strong", undefined, "Studio context is unavailable"),
        document.createTextNode(error instanceof Error ? error.message : "The Studio API could not be read."),
      );
      viewStatus.className = "crs-pill error";
      viewStatus.textContent = "Context error";
    }
  };

  const onStudioChanged = (event: Event): void => {
    const detail = (event as CustomEvent<StudioChangedDetail>).detail;
    transientToolWarning = detail?.ok === false && detail.error
      ? {
        code: detail.error.code,
        message: detail.error.message,
        severity: "error",
        suggestion: detail.error.nextAction,
      }
      : null;
    if (detail?.ok && detail.tool === "prepare_build_pack" && detail.buildPackResult) {
      const manifest = detail.buildPackResult.manifest;
      const currentTarget = context ? buildPackDisplayTargetKey(context) : null;
      const responseTarget = detail.buildPackTarget;
      const responseTargetMatches = context && responseTarget
        && matchesBuildPackResponseIdentity(responseTarget)
        && responseTarget.revisionId === context.headRevisionId
        && responseTarget.specHash === context.headSpecSha256;
      if (
        context
        && currentTarget
        && responseTargetMatches
        && (manifest === null || (
          manifest.revisionId === context.headRevisionId
          && manifest.specHash === context.headSpecSha256
        ))
      ) {
        preparedForBuildPackTarget = currentTarget;
        renderBuildPack(detail.buildPackResult);
      }
    }
    void refresh();
  };
  document.addEventListener(STUDIO_CHANGED_EVENT, onStudioChanged);

  await refresh();
  if (context) {
    const webmcpStatus = query<HTMLElement>("#crs-webmcp");
    try {
      const registered = await registerTools(
        document,
        studioOperationIdentity,
        runVisualInspection,
      );
      webmcpStatus.className = registered ? "crs-pill ready" : "crs-pill";
      webmcpStatus.textContent = registered ? "8 site tools ready" : "Site tools unavailable";
    } catch (error) {
      webmcpStatus.className = "crs-pill error";
      webmcpStatus.textContent = "Tool contract unavailable";
      renderWarnings(context, activeProfile(context, activeSpec(context)));
      console.error(error);
    }
  }

  return {
    refresh,
    destroy() {
      destroyed = true;
      refreshSequence += 1;
      selectionSequence += 1;
      scenarioSequence += 1;
      inspectionSequence += 1;
      manualInspectionSequence += 1;
      manualInspectionPending = false;
      document.removeEventListener(STUDIO_CHANGED_EVENT, onStudioChanged);
      viewer.destroy();
      delete root.dataset.characterRobotStudio;
      root.replaceChildren();
    },
  };
}
