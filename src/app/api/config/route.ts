import { NextResponse } from "next/server";
import { getLLMConfig } from "@/lib/llm";

export async function GET() {
  const cfg = getLLMConfig();
  if (!cfg) {
    return NextResponse.json({
      mode: "mock",
      message: "MOCK_VISION=true or no API key — using demo data (names like Vikram Singh are random, not from image)",
      hasKey: false,
    });
  }
  return NextResponse.json({
    mode: "live",
    baseUrl: cfg.baseUrl,
    model: cfg.visionModel,
    hasKey: true,
    message: `Live Vision LLM: ${cfg.visionModel} @ ${cfg.baseUrl}`,
  });
}
