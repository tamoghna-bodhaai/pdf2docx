// Unified LLM config — supports OpenAI and OpenRouter (and any OpenAI-compatible endpoint).
// Priority:
// 1. If LLM_BASE_URL is set, use it + LLM_API_KEY (or OPENROUTER_API_KEY / OPENAI_API_KEY)
// 2. If OPENROUTER_API_KEY is set, use https://openrouter.ai/api/v1
// 3. Otherwise use https://api.openai.com/v1 with OPENAI_API_KEY
//
// Models: LLM_MODEL / LLM_VISION_MODEL take precedence, then OPENROUTER_* , then OPENAI_* , then defaults.

export interface LLMConfig {
  baseUrl: string;
  apiKey: string;
  model: string;
  visionModel: string;
  headers: Record<string, string>;
}

export function getLLMConfig(): LLMConfig | null {
  // Allow forcing mock
  if (process.env.MOCK_VISION === "true") return null;

  const openRouterKey = process.env.OPENROUTER_API_KEY?.trim();
  const openAiKey = process.env.OPENAI_API_KEY?.trim();
  const genericKey = process.env.LLM_API_KEY?.trim();

  const apiKey = genericKey || openRouterKey || openAiKey || "";
  if (!apiKey) return null;

  // Determine base URL
  let baseUrl = process.env.LLM_BASE_URL?.trim();
  if (!baseUrl) {
    if (openRouterKey) baseUrl = "https://openrouter.ai/api/v1";
    else baseUrl = "https://api.openai.com/v1";
  }
  // Normalize: remove trailing slash
  baseUrl = baseUrl.replace(/\/$/, "");

  const model =
    process.env.LLM_MODEL?.trim() ||
    process.env.OPENROUTER_MODEL?.trim() ||
    process.env.OPENAI_MODEL?.trim() ||
    "gpt-4o-mini";

  const visionModel =
    process.env.LLM_VISION_MODEL?.trim() ||
    process.env.OPENROUTER_VISION_MODEL?.trim() ||
    process.env.OPENAI_VISION_MODEL?.trim() ||
    model;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${apiKey}`,
  };

  // OpenRouter recommended headers (optional but helps with ranking/analytics)
  if (baseUrl.includes("openrouter.ai")) {
    if (process.env.OPENROUTER_SITE_URL) headers["HTTP-Referer"] = process.env.OPENROUTER_SITE_URL;
    else if (process.env.NEXT_PUBLIC_SITE_URL) headers["HTTP-Referer"] = process.env.NEXT_PUBLIC_SITE_URL;
    // X-Title is optional
    headers["X-Title"] = process.env.OPENROUTER_APP_NAME || "OmiCheckr";
  }

  return { baseUrl, apiKey, model, visionModel, headers };
}

export function getChatCompletionsUrl(baseUrl: string): string {
  // baseUrl is like https://openrouter.ai/api/v1  -> append /chat/completions
  if (baseUrl.endsWith("/chat/completions")) return baseUrl;
  return `${baseUrl}/chat/completions`;
}
