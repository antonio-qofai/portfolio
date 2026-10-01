// The one place that talks to Claude. Every call is a structured-output request
// validated against a Zod schema, so callers get typed data or an error, never
// free text to parse. Tests swap in a scripted implementation of `Llm`.

import Anthropic from "@anthropic-ai/sdk";
import { betaZodOutputFormat } from "@anthropic-ai/sdk/helpers/beta/zod";
import type { z } from "zod/v4";

export const MODEL = "claude-opus-5-5";

// USD per million tokens for MODEL, used for the cost line in the agent trace.
const PRICE = { input: 4, output: 20, cache_read: 0.2 };

export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

export interface StructuredRequest<S extends z.ZodType> {
  system: string;
  messages: Anthropic.Beta.BetaMessageParam[];
  schema: S;
  effort: "low" | "medium" | "high";
  maxTokens?: number;
}

export interface StructuredResponse<T> {
  data: T;
  /** The assistant turn as returned, for appending to the conversation on a retry. */
  assistant: Anthropic.Beta.BetaMessageParam;
  usage: Usage;
}

export interface Llm {
  structured<S extends z.ZodType>(req: StructuredRequest<S>): Promise<StructuredResponse<z.infer<S>>>;
}

export class LlmError extends Error {}

export function addUsage(a: Usage, b: Usage): Usage {
  return {
    input_tokens: a.input_tokens + b.input_tokens,
    output_tokens: a.output_tokens + b.output_tokens,
    cost_usd: Math.round((a.cost_usd + b.cost_usd) * 10000) / 10000,
  };
}

export const NO_USAGE: Usage = { input_tokens: 0, output_tokens: 0, cost_usd: 0 };

export class ClaudeLlm implements Llm {
  private client: Anthropic;

  constructor(apiKey: string) {
    this.client = new Anthropic({ apiKey, timeout: 90_000, maxRetries: 2 });
  }

  async structured<S extends z.ZodType>(req: StructuredRequest<S>): Promise<StructuredResponse<z.infer<S>>> {
    const response = await this.client.beta.messages.parse({
      model: MODEL,
      max_tokens: req.maxTokens ?? 16000,
      // If a safety classifier declines, the API reruns the request on a
      // recommended fallback model instead of returning a refusal.
      betas: ["server-side-fallback-2026-07-01"],
      fallbacks: "default",
      system: [{ type: "text", text: req.system, cache_control: { type: "ephemeral" } }],
      messages: req.messages,
      output_config: { effort: req.effort, format: betaZodOutputFormat(req.schema) },
    });

    const u = response.usage;
    const cacheRead = u.cache_read_input_tokens ?? 0;
    const cacheWrite = u.cache_creation_input_tokens ?? 0;
    const usage: Usage = {
      input_tokens: u.input_tokens + cacheWrite + cacheRead,
      output_tokens: u.output_tokens,
      cost_usd:
        (u.input_tokens * PRICE.input + cacheWrite * PRICE.input * 1.25 + cacheRead * PRICE.cache_read + u.output_tokens * PRICE.output) /
        1_000_000,
    };

    if (response.stop_reason === "refusal") throw new LlmError(`Claude declined (${response.stop_details?.category ?? "no category"})`);
    if (response.stop_reason === "max_tokens") throw new LlmError("Claude ran out of output tokens");
    if (response.parsed_output == null) throw new LlmError("Claude's output did not match the schema");

    return {
      data: response.parsed_output as z.infer<S>,
      assistant: { role: "assistant", content: response.content },
      usage,
    };
  }
}
