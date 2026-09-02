export type JsonObject = Record<string, unknown>;

export async function callTool(name: string, arguments_: JsonObject): Promise<unknown> {
  const response = await fetch(`/api/tools/${name}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(arguments_),
  });
  const body = await response.json();
  if (!response.ok || !body.ok) {
    const error = body.error ?? { code: "REQUEST_FAILED", message: response.statusText };
    throw new Error(`${error.code}: ${error.message}`);
  }
  return body.result;
}

export async function getContext(): Promise<JsonObject> {
  const response = await fetch("/api/context", { credentials: "same-origin" });
  if (!response.ok) throw new Error(`Context request failed: ${response.status}`);
  return response.json();
}

export async function getTrace(runId: string): Promise<JsonObject> {
  const response = await fetch(`/api/traces/${encodeURIComponent(runId)}`, {
    credentials: "same-origin",
  });
  const body = await response.json();
  if (!response.ok) {
    const error = body.error ?? { code: "TRACE_REQUEST_FAILED", message: response.statusText };
    throw new Error(`${error.code}: ${error.message}`);
  }
  return body;
}

export async function resetSession(): Promise<void> {
  const response = await fetch("/api/reset", { method: "POST", credentials: "same-origin" });
  if (!response.ok) throw new Error(`Reset failed: ${response.status}`);
}
