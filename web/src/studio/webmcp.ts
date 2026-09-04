import {
  callStudioTool,
  parseBuildPackResult,
  StudioApiError,
  type JsonObject,
} from "./api";
import type { BuildPackResult } from "./types";

export const STUDIO_CHANGED_EVENT = "character-robot-studio:changed";

export const STUDIO_TOOL_NAMES = [
  "get_studio_context",
  "set_design_draft",
  "revise_design_draft",
  "inspect_design",
  "preview_scenario",
  "validate_design",
  "create_revision_from_draft",
  "prepare_build_pack",
] as const;

export type StudioToolName = (typeof STUDIO_TOOL_NAMES)[number];

export type StudioChangedDetail = {
  tool: StudioToolName;
  ok: boolean;
  buildPackResult?: BuildPackResult;
  buildPackTarget?: {
    projectId: string;
    projectGeneration: number;
    operationEpoch: number;
    revisionId: string;
    specHash: string;
  };
  error?: {
    code: string;
    message: string;
    nextAction: string | null;
  };
};

export type StudioOperationIdentity = {
  projectId: string;
  projectGeneration: number;
  operationEpoch: number;
};

export type StudioBuildPackResponseIdentity = StudioOperationIdentity;

export type StudioVisualInspectionRunner = (
  input: JsonObject,
  result: JsonObject,
  identity: StudioOperationIdentity | null,
) => Promise<JsonObject>;

export type StudioToolDefinition = {
  name: StudioToolName;
  description: string;
  inputSchema: JsonObject;
  annotations?: { readOnlyHint?: boolean };
};

type RegisteredStudioTool = StudioToolDefinition & {
  execute: (input: JsonObject) => Promise<unknown>;
};

const isContentBlock = (value: unknown): value is JsonObject => (
  typeof value === "object" && value !== null && !Array.isArray(value)
);

const splitVisualInspectionResult = (
  value: JsonObject,
): { content: JsonObject[]; metadata: JsonObject } => {
  const content = value.content;
  const isReady = value.status === "ready";
  const hasTextBlock = (
    block: unknown,
  ): block is JsonObject => (
    isContentBlock(block)
    && block.type === "text"
    && typeof block.text === "string"
    && block.text.length > 0
  );
  if (
    !Array.isArray(content)
    || !hasTextBlock(content[0])
    || (isReady && content.length !== 5)
    || (!isReady && content.length !== 1)
    || (isReady && content.slice(1).some((block) => (
      !isContentBlock(block)
      || block.type !== "image"
      || block.mimeType !== "image/png"
      || typeof block.data !== "string"
      || block.data.length === 0
    )))
  ) {
    throw new Error(
      "STUDIO_VISUAL_RESULT_INVALID: inspect_design visual content must contain one text block and four PNG images",
    );
  }
  const { content: _content, ...metadata } = value;
  return { content, metadata };
};

type ModelContext = {
  registerTool(tool: RegisteredStudioTool): Promise<void> | void;
};

type ModelContextDocument = Document & {
  modelContext?: ModelContext;
};

const isRecord = (value: unknown): value is JsonObject => (
  typeof value === "object" && value !== null && !Array.isArray(value)
);

const isToolName = (value: unknown): value is StudioToolName => (
  typeof value === "string" && (STUDIO_TOOL_NAMES as readonly string[]).includes(value)
);

export async function getStudioToolDefinitions(): Promise<StudioToolDefinition[]> {
  const response = await fetch("/api/studio/v1/tool-definitions", {
    credentials: "same-origin",
  });
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new Error("STUDIO_TOOL_DEFINITIONS_INVALID: response was not JSON");
  }
  if (!response.ok || !Array.isArray(body)) {
    throw new Error(`STUDIO_TOOL_DEFINITIONS_FAILED: ${response.status}`);
  }

  const byName = new Map<StudioToolName, StudioToolDefinition>();
  for (const raw of body) {
    if (!isRecord(raw) || !isToolName(raw.name)) {
      throw new Error("STUDIO_TOOL_DEFINITIONS_INVALID: unsupported tool name");
    }
    if (byName.has(raw.name)) {
      throw new Error(`STUDIO_TOOL_DEFINITIONS_INVALID: duplicate ${raw.name}`);
    }
    if (typeof raw.description !== "string" || raw.description.length === 0) {
      throw new Error(`STUDIO_TOOL_DEFINITIONS_INVALID: ${raw.name} has no description`);
    }
    if (!isRecord(raw.inputSchema)) {
      throw new Error(`STUDIO_TOOL_DEFINITIONS_INVALID: ${raw.name} has no input schema`);
    }
    let annotations: StudioToolDefinition["annotations"];
    if (raw.annotations !== undefined) {
      if (!isRecord(raw.annotations) || (
        raw.annotations.readOnlyHint !== undefined
        && typeof raw.annotations.readOnlyHint !== "boolean"
      )) {
        throw new Error(`STUDIO_TOOL_DEFINITIONS_INVALID: ${raw.name} has invalid annotations`);
      }
      annotations = raw.annotations.readOnlyHint === undefined
        ? undefined
        : { readOnlyHint: raw.annotations.readOnlyHint };
    }
    byName.set(raw.name, {
      name: raw.name,
      description: raw.description,
      inputSchema: raw.inputSchema,
      ...(annotations ? { annotations } : {}),
    });
  }

  if (byName.size !== STUDIO_TOOL_NAMES.length) {
    const missing = STUDIO_TOOL_NAMES.filter((name) => !byName.has(name));
    throw new Error(`STUDIO_TOOL_DEFINITIONS_INVALID: missing ${missing.join(", ")}`);
  }
  return STUDIO_TOOL_NAMES.map((name) => byName.get(name)!);
}

export async function registerStudioWebMcpTools(
  document_: Document = document,
  getOperationIdentity: () => StudioOperationIdentity | null = () => null,
  inspectVisuals?: StudioVisualInspectionRunner,
): Promise<boolean> {
  const modelContext = (document_ as ModelContextDocument).modelContext;
  if (typeof modelContext?.registerTool !== "function") return false;

  const definitions = await getStudioToolDefinitions();
  for (const definition of definitions) {
    await modelContext.registerTool({
      ...definition,
      execute: async (input: JsonObject) => {
        try {
          const operationIdentity = definition.name === "prepare_build_pack"
            || definition.name === "inspect_design"
            ? getOperationIdentity()
            : null;
          const result = await callStudioTool(definition.name, input);
          let toolResult = result;
          if (definition.name === "inspect_design" && inspectVisuals) {
            if (!isRecord(result)) {
              throw new Error("STUDIO_TOOL_RESULT_INVALID: inspect_design result was not an object");
            }
            const visualResult = await inspectVisuals(input, result, operationIdentity);
            if (!isRecord(visualResult)) {
              throw new Error("STUDIO_VISUAL_RESULT_INVALID: visual inspection result was not an object");
            }
            const visualContent = splitVisualInspectionResult(visualResult);
            toolResult = {
              ...result,
              content: visualContent.content,
              visual_inspection: visualContent.metadata,
            };
          }
          const buildPackResult = definition.name === "prepare_build_pack"
            ? parseBuildPackResult(result)
            : undefined;
          const buildPackTarget = definition.name === "prepare_build_pack"
            && typeof input.revision_id === "string"
            && typeof input.expected_spec_hash === "string"
            && operationIdentity
            ? {
                ...operationIdentity,
                revisionId: input.revision_id,
                specHash: input.expected_spec_hash,
              }
            : undefined;
          document_.dispatchEvent(new CustomEvent<StudioChangedDetail>(STUDIO_CHANGED_EVENT, {
            detail: {
              tool: definition.name,
              ok: true,
              ...(buildPackResult ? { buildPackResult } : {}),
              ...(buildPackTarget ? { buildPackTarget } : {}),
            },
          }));
          return toolResult;
        } catch (error) {
          const apiError = error instanceof StudioApiError ? error : null;
          document_.dispatchEvent(new CustomEvent<StudioChangedDetail>(STUDIO_CHANGED_EVENT, {
            detail: {
              tool: definition.name,
              ok: false,
              error: {
                code: apiError?.code ?? "STUDIO_TOOL_FAILED",
                message: error instanceof Error ? error.message : "The Studio tool failed.",
                nextAction: apiError?.nextAction ?? null,
              },
            },
          }));
          throw error;
        }
      },
    });
  }
  return true;
}
