import { callTool, type JsonObject } from "./api";

type ToolDefinition = {
  name: string;
  description: string;
  inputSchema: JsonObject;
  annotations?: { readOnlyHint?: boolean };
  execute: (input: JsonObject) => Promise<unknown>;
};

type ModelContext = { registerTool(tool: ToolDefinition): Promise<void> | void };

declare global {
  interface Document { modelContext?: ModelContext }
}

const objectSchema = (properties: JsonObject, required: string[] = []): JsonObject => ({
  type: "object",
  properties,
  required,
  additionalProperties: false,
});

const string = (description: string): JsonObject => ({ type: "string", description });

const elementReferenceSchema = objectSchema({
  kind: { type: "string", enum: ["joint", "actuator", "body", "site"] },
  name: { type: "string", minLength: 1, maxLength: 64 },
  attributes: {
    type: "array",
    minItems: 1,
    maxItems: 4,
    items: { type: "string", enum: ["axis", "damping", "armature", "frictionloss", "joint"] },
  },
}, ["kind", "name", "attributes"]);

const hypothesisSchema = objectSchema({
  claim: { type: "string", minLength: 1, maxLength: 2000 },
  suspected_elements: { type: "array", minItems: 1, maxItems: 8, items: elementReferenceSchema },
  competing_explanation: objectSchema({
    claim: { type: "string", minLength: 1, maxLength: 2000 },
    suspected_elements: { type: "array", minItems: 1, maxItems: 8, items: elementReferenceSchema },
    discriminating_reason: { type: "string", minLength: 1, maxLength: 2000 },
  }, ["claim", "suspected_elements", "discriminating_reason"]),
  prediction: { type: "string", minLength: 1, maxLength: 2000 },
  falsifier: { type: "string", minLength: 1, maxLength: 2000 },
}, ["claim", "suspected_elements", "competing_explanation", "prediction", "falsifier"]);

const segmentSchema = objectSchema({
  label: { type: "string", minLength: 1, maxLength: 64 },
  n_steps: { type: "integer", minimum: 1 },
  controls: {
    type: "array",
    minItems: 1,
    maxItems: 64,
    items: objectSchema({
      actuator_name: { type: "string", minLength: 1, maxLength: 64 },
      value: { type: "number" },
    }, ["actuator_name", "value"]),
  },
}, ["n_steps", "controls"]);

const observableSchema: JsonObject = {
  oneOf: [
    ...["qpos", "qvel", "energy", "contact_count"].map((kind) => objectSchema({ kind: { const: kind } }, ["kind"])),
    objectSchema({ kind: { const: "body_position" }, body_name: { type: "string", minLength: 1, maxLength: 64 } }, ["kind", "body_name"]),
  ],
};

const targetSchema = objectSchema({
  kind: { const: "joint" },
  name: { type: "string", minLength: 1, maxLength: 64 },
}, ["kind", "name"]);

const patchSchema: JsonObject = {
  oneOf: [
    objectSchema({
      target: targetSchema,
      attribute: { const: "axis" },
      expected_old_value: { type: "array", minItems: 3, maxItems: 3, items: { type: "number" } },
      new_value: { type: "array", minItems: 3, maxItems: 3, items: { type: "number" } },
    }, ["target", "attribute", "expected_old_value", "new_value"]),
    ...[
      ["damping", 100],
      ["armature", 10],
      ["frictionloss", 100],
    ].map(([attribute, maximum]) => objectSchema({
      target: targetSchema,
      attribute: { const: attribute },
      expected_old_value: { type: "number", minimum: 0, maximum },
      new_value: { type: "number", minimum: 0, maximum },
    }, ["target", "attribute", "expected_old_value", "new_value"])),
  ],
};

const expectedEffectSchema = objectSchema({
  scenario_id: { const: "public_center" },
  predicates: {
    type: "array",
    minItems: 1,
    maxItems: 16,
    items: objectSchema({
      metric: { type: "string", minLength: 1, maxLength: 96 },
      op: { type: "string", enum: ["lt", "lte", "eq", "gte", "gt"] },
      value: { type: "number" },
    }, ["metric", "op", "value"]),
  },
}, ["scenario_id", "predicates"]);

export const webmcpTools: Omit<ToolDefinition, "execute">[] = [
  {
    name: "get_design_context",
    description: "Read the current robot head, parent and canonical diff, editable policy, shared draft, recent trace identities, latest task evidence, budgets, and qualification state.",
    inputSchema: objectSchema({}),
    annotations: { readOnlyHint: true },
  },
  {
    name: "inspect_design",
    description: "Inspect authored and compiled joint, body, actuator, and dimension data for one revision.",
    inputSchema: objectSchema({
      revision_id: string("Revision ID such as r000. Omit to inspect the current head."),
      view: { type: "string", enum: ["authored", "compiled", "both"], default: "both" },
    }),
    annotations: { readOnlyHint: true },
  },
  {
    name: "run_task",
    description: "Run the fixed public task against an exact immutable revision and return hard requirements plus BehaviorDiff.",
    inputSchema: objectSchema({
      case_id: string("Case ID from design context."),
      revision_id: string("Exact immutable revision ID."),
      scenario_id: { type: "string", enum: ["public_center"] },
      capture: { type: "string", enum: ["metrics", "metrics_and_filmstrip"] },
    }, ["case_id", "revision_id", "scenario_id", "capture"]),
  },
  {
    name: "run_experiment",
    description: "Run a bounded hypothesis-driven experiment against an exact immutable revision. The agent chooses controls and observables.",
    inputSchema: objectSchema({
      case_id: string("Case ID from design context."),
      revision_id: string("Exact immutable revision ID."),
      hypothesis: hypothesisSchema,
      initial_joint_positions: { type: "array", items: objectSchema({ joint_name: { type: "string" }, position_rad: { type: "number" } }, ["joint_name", "position_rad"]), minItems: 1 },
      segments: { type: "array", items: segmentSchema, minItems: 1, maxItems: 16, description: "Constant-control segments totaling 256 to 100000 steps." },
      observables: { type: "array", items: observableSchema, minItems: 1, maxItems: 8 },
      capture_final_snapshot: { type: "boolean", default: false },
    }, ["case_id", "revision_id", "hypothesis", "initial_joint_positions", "segments", "observables"]),
  },
  {
    name: "query_trace",
    description: "Query a session experiment trace compactly using sample, min_max, delta, sum, or settling instead of returning the full trace.",
    inputSchema: objectSchema({
      run_id: string("Experiment run ID."),
      operation: { type: "string", enum: ["sample", "min_max", "delta", "sum", "settling"] },
      signal: string("Named trace signal; optional for sample."),
      start: { type: "integer", minimum: 0, default: 0 },
      end: { type: "integer", minimum: 1 },
      count: { type: "integer", minimum: 1, maximum: 64, default: 12 },
      target: { type: "number" },
      tolerance: { type: "number", exclusiveMinimum: 0 },
    }, ["run_id", "operation"]),
    annotations: { readOnlyHint: true },
  },
  {
    name: "set_draft_patch",
    description: "Place one axis, damping, armature, or frictionloss change in the shared preview. This does not create evidence or mutate the revision ledger.",
    inputSchema: objectSchema({
      base_revision_id: string("Current head revision."),
      expected_base_sha256: string("Current head asset SHA-256."),
      patch: patchSchema,
    }, ["base_revision_id", "expected_base_sha256", "patch"]),
  },
  {
    name: "create_revision_from_draft",
    description: "Validate the shared draft using cited experiment evidence and create one immutable child revision.",
    inputSchema: objectSchema({
      basis_hypothesis_id: string("Hypothesis ID returned by run_experiment."),
      basis_experiment_run_id: string("Completed experiment run ID for the draft base."),
      rationale: string("Why the evidence supports this patch."),
      expected_effect: expectedEffectSchema,
    }, ["basis_hypothesis_id", "basis_experiment_run_id", "rationale", "expected_effect"]),
  },
  {
    name: "verify_revision",
    description: "Consume the one hidden qualification attempt for a public-passing current revision. A completed pass or fail locks editing; only a pass marks the revision qualified, and the human resets the session for another attempt.",
    inputSchema: objectSchema({
      case_id: string("Case ID from design context."),
      revision_id: string("Current child head revision."),
      expected_asset_sha256: string("Exact current asset SHA-256."),
    }, ["case_id", "revision_id", "expected_asset_sha256"]),
  },
];

export async function registerWebMcpTools(document_: Document = document): Promise<boolean> {
  const register = document_.modelContext?.registerTool;
  if (typeof register !== "function") return false;
  for (const definition of webmcpTools) {
    await register.call(document_.modelContext, {
      ...definition,
      execute: async (input: JsonObject) => {
        try {
          return await callTool(definition.name, input);
        } finally {
          document_.dispatchEvent(new CustomEvent("asset-autopsy:changed", { detail: { tool: definition.name } }));
        }
      },
    });
  }
  return true;
}
