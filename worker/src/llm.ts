/** Minimal client for any OpenAI-compatible chat completions endpoint. */

export class LlmError extends Error {}

function stripFences(text: string): string {
  const trimmed = text.trim();
  if (!trimmed.startsWith("```")) return trimmed;
  const lines = trimmed.split("\n").slice(1);
  if (lines.length && lines[lines.length - 1].trim().startsWith("```")) lines.pop();
  return lines.join("\n").trim();
}

export function parseJsonText(text: string): any {
  const cleaned = stripFences(text || "");
  try {
    return JSON.parse(cleaned);
  } catch {
    const start = cleaned.indexOf("{");
    const end = cleaned.lastIndexOf("}");
    if (start === -1 || end <= start) throw new LlmError(`model did not return JSON: ${cleaned.slice(0, 200)}`);
    try {
      return JSON.parse(cleaned.slice(start, end + 1));
    } catch (exc) {
      throw new LlmError(`model returned malformed JSON: ${String(exc)}`);
    }
  }
}

export interface LlmClient {
  available: boolean;
  chatJson(system: string, user: string): Promise<any>;
}

export function makeClient(env: Env): LlmClient {
  const base = (env.LLM_BASE_URL || "").replace(/\/$/, "");
  const model = env.LLM_MODEL || "";
  const key = (env.LLM_API_KEY || "").trim();
  const available = Boolean(base && model);

  return {
    available,
    async chatJson(system: string, user: string): Promise<any> {
      if (!available) throw new LlmError("LLM is not configured");
      const response = await fetch(`${base}/chat/completions`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(key ? { Authorization: `Bearer ${key}` } : {}),
        },
        body: JSON.stringify({
          model,
          messages: [
            { role: "system", content: system },
            { role: "user", content: user },
          ],
          temperature: 0.2,
          response_format: { type: "json_object" },
        }),
      });
      if (!response.ok) throw new LlmError(`HTTP ${response.status} from LLM endpoint`);
      const payload = await response.json();
      const content = payload?.choices?.[0]?.message?.content;
      if (typeof content !== "string") throw new LlmError(`unexpected completion shape: ${JSON.stringify(payload).slice(0, 200)}`);
      return parseJsonText(content);
    },
  };
}
