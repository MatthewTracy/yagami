import type {
  EmbeddingModelV4,
  EmbeddingModelV4CallOptions,
  EmbeddingModelV4Result,
  LanguageModelV4,
  LanguageModelV4CallOptions,
  LanguageModelV4Content,
  LanguageModelV4FinishReason,
  LanguageModelV4GenerateResult,
  LanguageModelV4Message,
  LanguageModelV4StreamPart,
  LanguageModelV4StreamResult,
  LanguageModelV4Usage,
  SharedV4ProviderMetadata,
} from "@ai-sdk/provider";

export interface YagamiProviderSettings {
  baseURL?: string;
  apiKey?: string;
  headers?: Record<string, string>;
  fetch?: typeof globalThis.fetch;
  metadata?: Record<string, unknown>;
}

export interface YagamiProvider {
  (modelId?: string): LanguageModelV4;
  languageModel(modelId?: string): LanguageModelV4;
  embedding(modelId?: string): EmbeddingModelV4;
  embeddingModel(modelId?: string): EmbeddingModelV4;
}

type OpenAIMessage = {
  role: "system" | "user" | "assistant" | "tool";
  content: string | Array<Record<string, unknown>> | null;
  tool_call_id?: string;
  tool_calls?: Array<{
    id: string;
    type: "function";
    function: { name: string; arguments: string };
  }>;
};

type OpenAIResponse = {
  id: string;
  model: string;
  choices: Array<{
    finish_reason?: string | null;
    message: {
      content?: string | null;
      tool_calls?: Array<{
        id: string;
        type: "function";
        function: { name: string; arguments: string };
      }>;
    };
  }>;
  usage?: {
    prompt_tokens?: number;
    completion_tokens?: number;
    total_tokens?: number;
  };
};

const defaultBaseURL = "http://127.0.0.1:8000/v1";

function combineHeaders(
  settings: YagamiProviderSettings,
  callHeaders?: Record<string, string | undefined>,
): Record<string, string> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    "user-agent": "@yagami/ai-sdk-provider",
    ...settings.headers,
  };
  const environment = (
    globalThis as typeof globalThis & {
      process?: { env?: Record<string, string | undefined> };
    }
  ).process?.env;
  const apiKey = settings.apiKey ?? environment?.YAGAMI_API_KEY;
  if (apiKey) {
    headers.authorization = `Bearer ${apiKey}`;
  }
  for (const [key, value] of Object.entries(callHeaders ?? {})) {
    if (value !== undefined) {
      headers[key] = value;
    }
  }
  return headers;
}

function evidence(headers: Headers): SharedV4ProviderMetadata {
  const value: Record<string, string> = {};
  const names: Record<string, string> = {
    requestId: "x-yagami-request-id",
    decisionId: "x-yagami-decision-id",
    backend: "x-yagami-backend",
    policyHash: "x-yagami-policy-hash",
    trustZone: "x-yagami-trust-zone",
  };
  for (const [key, name] of Object.entries(names)) {
    const item = headers.get(name);
    if (item) value[key] = item;
  }
  return { yagami: value };
}

function finishReason(raw?: string | null): LanguageModelV4FinishReason {
  const unified: LanguageModelV4FinishReason["unified"] =
    raw === "stop"
      ? "stop"
      : raw === "length"
        ? "length"
        : raw === "tool_calls"
          ? "tool-calls"
          : raw === "content_filter"
            ? "content-filter"
            : "other";
  return { unified, raw: raw ?? undefined };
}

function usage(value?: OpenAIResponse["usage"]): LanguageModelV4Usage {
  return {
    inputTokens: {
      total: value?.prompt_tokens,
      noCache: value?.prompt_tokens,
      cacheRead: undefined,
      cacheWrite: undefined,
    },
    outputTokens: {
      total: value?.completion_tokens,
      text: value?.completion_tokens,
      reasoning: undefined,
    },
    ...(value ? { raw: value } : {}),
  };
}

function bytesToBase64(data: Uint8Array): string {
  let binary = "";
  for (const value of data) binary += String.fromCharCode(value);
  return btoa(binary);
}

function toolResultText(output: unknown): string {
  if (!output || typeof output !== "object") return String(output ?? "");
  const result = output as Record<string, unknown>;
  if ("value" in result) {
    return typeof result.value === "string"
      ? result.value
      : JSON.stringify(result.value);
  }
  if (result.type === "execution-denied") {
    return String(result.reason ?? "Tool execution was denied.");
  }
  return JSON.stringify(result);
}

function toOpenAIMessages(prompt: LanguageModelV4Message[]): OpenAIMessage[] {
  return prompt.map((message): OpenAIMessage => {
    if (message.role === "system") {
      return { role: "system", content: message.content };
    }
    if (message.role === "user") {
      const content = message.content.map((part) => {
        if (part.type === "text") return { type: "text", text: part.text };
        if (part.mediaType.startsWith("image/")) {
          if (part.data.type === "url") {
            return { type: "image_url", image_url: { url: part.data.url } };
          }
          if (part.data.type === "data") {
            const raw =
              typeof part.data.data === "string"
                ? part.data.data
                : bytesToBase64(part.data.data);
            return {
              type: "image_url",
              image_url: { url: `data:${part.mediaType};base64,${raw}` },
            };
          }
        }
        throw new Error(`Yagami does not support ${part.mediaType} prompt files`);
      });
      return { role: "user", content };
    }
    if (message.role === "tool") {
      const first = message.content.find((part) => part.type === "tool-result");
      if (!first || first.type !== "tool-result") {
        return { role: "tool", content: "" };
      }
      return {
        role: "tool",
        tool_call_id: first.toolCallId,
        content: toolResultText(first.output),
      };
    }
    const text = message.content
      .filter((part) => part.type === "text")
      .map((part) => (part.type === "text" ? part.text : ""))
      .join("");
    const calls = message.content
      .filter((part) => part.type === "tool-call")
      .map((part) => {
        if (part.type !== "tool-call") throw new Error("unreachable");
        return {
          id: part.toolCallId,
          type: "function" as const,
          function: {
            name: part.toolName,
            arguments:
              typeof part.input === "string" ? part.input : JSON.stringify(part.input),
          },
        };
      });
    return {
      role: "assistant",
      content: text || null,
      ...(calls.length ? { tool_calls: calls } : {}),
    };
  });
}

function toolPayload(options: LanguageModelV4CallOptions) {
  const tools = (options.tools ?? [])
    .filter((tool) => tool.type === "function")
    .map((tool) => {
      if (tool.type !== "function") throw new Error("unreachable");
      return {
        type: "function",
        function: {
          name: tool.name,
          description: tool.description,
          parameters: tool.inputSchema,
          strict: tool.strict,
        },
      };
    });
  let toolChoice: unknown;
  if (options.toolChoice?.type === "tool") {
    toolChoice = {
      type: "function",
      function: { name: options.toolChoice.toolName },
    };
  } else if (options.toolChoice) {
    toolChoice = options.toolChoice.type;
  }
  return {
    ...(tools.length ? { tools } : {}),
    ...(toolChoice ? { tool_choice: toolChoice } : {}),
  };
}

function requestBody(
  modelId: string,
  options: LanguageModelV4CallOptions,
  settings: YagamiProviderSettings,
  stream: boolean,
) {
  const providerMetadata = options.providerOptions?.yagami as
    | Record<string, unknown>
    | undefined;
  return {
    model: modelId,
    messages: toOpenAIMessages(options.prompt),
    stream,
    temperature: options.temperature,
    max_tokens: options.maxOutputTokens,
    stop: options.stopSequences,
    top_p: options.topP,
    presence_penalty: options.presencePenalty,
    frequency_penalty: options.frequencyPenalty,
    response_format:
      options.responseFormat?.type === "json"
        ? options.responseFormat.schema
          ? {
              type: "json_schema",
              json_schema: {
                name: options.responseFormat.name ?? "response",
                schema: options.responseFormat.schema,
              },
            }
          : { type: "json_object" }
        : undefined,
    metadata: { ...settings.metadata, ...providerMetadata },
    ...toolPayload(options),
  };
}

async function safeResponse(response: Response): Promise<Response> {
  if (response.ok) return response;
  let code = "provider_error";
  try {
    const body = (await response.clone().json()) as {
      error?: { code?: string };
    };
    code = body.error?.code ?? code;
  } catch {
    // The client receives a safe code, never an upstream response body.
  }
  throw new Error(`Yagami request failed (${response.status}, ${code})`);
}

class YagamiLanguageModel implements LanguageModelV4 {
  readonly specificationVersion = "v4" as const;
  readonly provider = "yagami";
  readonly supportedUrls: Record<string, RegExp[]> = {};

  constructor(
    readonly modelId: string,
    private readonly settings: YagamiProviderSettings,
  ) {}

  async doGenerate(
    options: LanguageModelV4CallOptions,
  ): Promise<LanguageModelV4GenerateResult> {
    const fetcher = this.settings.fetch ?? globalThis.fetch;
    const body = requestBody(this.modelId, options, this.settings, false);
    const response = await safeResponse(
      await fetcher(
        `${(this.settings.baseURL ?? defaultBaseURL).replace(/\/$/, "")}/chat/completions`,
        {
          method: "POST",
          headers: combineHeaders(this.settings, options.headers),
          body: JSON.stringify(body),
          ...(options.abortSignal ? { signal: options.abortSignal } : {}),
        },
      ),
    );
    const result = (await response.json()) as OpenAIResponse;
    const choice = result.choices[0];
    if (!choice) throw new Error("Yagami returned no completion choice");
    const content: LanguageModelV4Content[] = [];
    if (choice.message.content) {
      content.push({
        type: "text",
        text: choice.message.content,
        providerMetadata: evidence(response.headers),
      });
    }
    for (const call of choice.message.tool_calls ?? []) {
      content.push({
        type: "tool-call",
        toolCallId: call.id,
        toolName: call.function.name,
        input: call.function.arguments,
        providerMetadata: evidence(response.headers),
      });
    }
    return {
      content,
      finishReason: finishReason(choice.finish_reason),
      usage: usage(result.usage),
      providerMetadata: evidence(response.headers),
      response: {
        id: result.id,
        modelId: result.model,
        headers: Object.fromEntries(response.headers.entries()),
        body: result,
      },
      warnings: [],
    };
  }

  async doStream(
    options: LanguageModelV4CallOptions,
  ): Promise<LanguageModelV4StreamResult> {
    const fetcher = this.settings.fetch ?? globalThis.fetch;
    const body = requestBody(this.modelId, options, this.settings, true);
    const response = await safeResponse(
      await fetcher(
        `${(this.settings.baseURL ?? defaultBaseURL).replace(/\/$/, "")}/chat/completions`,
        {
          method: "POST",
          headers: combineHeaders(this.settings, options.headers),
          body: JSON.stringify(body),
          ...(options.abortSignal ? { signal: options.abortSignal } : {}),
        },
      ),
    );
    if (!response.body) throw new Error("Yagami returned an empty stream");
    const metadata = evidence(response.headers);
    const source = response.body;
    const stream = new ReadableStream<LanguageModelV4StreamPart>({
      async start(controller) {
        controller.enqueue({ type: "stream-start", warnings: [] });
        const responseId = response.headers.get("x-yagami-request-id");
        const backend = response.headers.get("x-yagami-backend");
        controller.enqueue({
          type: "response-metadata",
          ...(responseId ? { id: responseId } : {}),
          ...(backend ? { modelId: backend } : {}),
        });
        const reader = source.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        let textStarted = false;
        let rawFinish: string | null | undefined;
        const tools = new Map<number, { id: string; name: string; started: boolean }>();
        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split(/\r?\n/);
            buffer = lines.pop() ?? "";
            for (const line of lines) {
              if (!line.startsWith("data: ")) continue;
              const data = line.slice(6);
              if (data === "[DONE]") continue;
              const chunk = JSON.parse(data) as {
                choices?: Array<{
                  finish_reason?: string | null;
                  delta?: {
                    content?: string;
                    tool_calls?: Array<{
                      index: number;
                      id?: string;
                      function?: { name?: string; arguments?: string };
                    }>;
                  };
                }>;
              };
              if (options.includeRawChunks) {
                controller.enqueue({ type: "raw", rawValue: chunk });
              }
              const choice = chunk.choices?.[0];
              if (!choice) continue;
              rawFinish = choice.finish_reason ?? rawFinish;
              const delta = choice.delta;
              if (delta?.content) {
                if (!textStarted) {
                  controller.enqueue({
                    type: "text-start",
                    id: "text-0",
                    providerMetadata: metadata,
                  });
                  textStarted = true;
                }
                controller.enqueue({
                  type: "text-delta",
                  id: "text-0",
                  delta: delta.content,
                  providerMetadata: metadata,
                });
              }
              for (const call of delta?.tool_calls ?? []) {
                const state = tools.get(call.index) ?? {
                  id: call.id ?? `call-${call.index}`,
                  name: call.function?.name ?? "",
                  started: false,
                };
                if (call.id) state.id = call.id;
                if (call.function?.name) state.name = call.function.name;
                if (!state.started && state.name) {
                  controller.enqueue({
                    type: "tool-input-start",
                    id: state.id,
                    toolName: state.name,
                    providerMetadata: metadata,
                  });
                  state.started = true;
                }
                if (state.started && call.function?.arguments) {
                  controller.enqueue({
                    type: "tool-input-delta",
                    id: state.id,
                    delta: call.function.arguments,
                    providerMetadata: metadata,
                  });
                }
                tools.set(call.index, state);
              }
            }
          }
          if (textStarted) controller.enqueue({ type: "text-end", id: "text-0" });
          for (const tool of tools.values()) {
            if (tool.started) controller.enqueue({ type: "tool-input-end", id: tool.id });
          }
          controller.enqueue({
            type: "finish",
            finishReason: finishReason(rawFinish),
            usage: usage(),
            providerMetadata: metadata,
          });
          controller.close();
        } catch (error) {
          controller.enqueue({ type: "error", error });
          controller.close();
        } finally {
          reader.releaseLock();
        }
      },
    });
    return {
      stream,
      response: { headers: Object.fromEntries(response.headers.entries()) },
    };
  }
}

class YagamiEmbeddingModel implements EmbeddingModelV4 {
  readonly specificationVersion = "v4" as const;
  readonly provider = "yagami";
  readonly maxEmbeddingsPerCall = 2048;
  readonly supportsParallelCalls = true;

  constructor(
    readonly modelId: string,
    private readonly settings: YagamiProviderSettings,
  ) {}

  async doEmbed(options: EmbeddingModelV4CallOptions): Promise<EmbeddingModelV4Result> {
    const fetcher = this.settings.fetch ?? globalThis.fetch;
    const response = await safeResponse(
      await fetcher(
        `${(this.settings.baseURL ?? defaultBaseURL).replace(/\/$/, "")}/embeddings`,
        {
          method: "POST",
          headers: combineHeaders(this.settings, options.headers),
          body: JSON.stringify({
            model: this.modelId,
            input: options.values,
            encoding_format: "float",
            metadata: {
              ...this.settings.metadata,
              ...(options.providerOptions?.yagami as Record<string, unknown> | undefined),
            },
          }),
          ...(options.abortSignal ? { signal: options.abortSignal } : {}),
        },
      ),
    );
    const result = (await response.json()) as {
      data: Array<{ embedding: number[]; index: number }>;
      usage?: { total_tokens?: number };
    };
    const tokenUsage = result.usage?.total_tokens;
    return {
      embeddings: result.data
        .sort((left, right) => left.index - right.index)
        .map((item) => item.embedding),
      ...(tokenUsage ? { usage: { tokens: tokenUsage } } : {}),
      providerMetadata: evidence(response.headers),
      response: {
        headers: Object.fromEntries(response.headers.entries()),
        body: result,
      },
      warnings: [],
    };
  }
}

export function createYagami(settings: YagamiProviderSettings = {}): YagamiProvider {
  const languageModel = (modelId = "yagami-auto") =>
    new YagamiLanguageModel(modelId, settings);
  const embeddingModel = (modelId = "yagami-embedding") =>
    new YagamiEmbeddingModel(modelId, settings);
  const provider = Object.assign(languageModel, {
    languageModel,
    embedding: embeddingModel,
    embeddingModel,
  });
  return provider;
}

export const yagami = createYagami();
