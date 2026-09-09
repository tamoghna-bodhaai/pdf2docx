import { VisionExtractionResult, SheetType } from "./types";
import fs from "fs";
import path from "path";
import { getLLMConfig, getChatCompletionsUrl } from "./llm";

/**
 * Abstraction: extractAnswerSheet(imagePath, questionCount, sheetType)
 * Supports both bubble OMR and handwritten list (e.g. "1.a 2.b ...").
 * sheetType: "bubble" | "handwritten" | "auto"  (auto = detect)
 * Uses same LLM backend; just switches system prompt.
 * If no API key or MOCK_VISION=true, uses deterministic mock for demo/testing.
 */

export async function extractAnswerSheet(
  imagePath: string,
  questionCount: number,
  sheetType: SheetType = "bubble"
): Promise<VisionExtractionResult> {
  const cfg = getLLMConfig();
  if (!cfg) {
    console.warn(`[vision] No LLM config — using MOCK extraction (${sheetType}) (demo data). Set OPENROUTER_API_KEY and MOCK_VISION=false for real sheets.`);
    return mockExtraction(questionCount, imagePath);
  }
  // Live mode: do NOT silently fall back to mock — surface error so teacher knows Vision LLM failed
  return await visionLLMExtraction(imagePath, questionCount, cfg, sheetType);
}

function buildSystemPrompt(questionCount: number, sheetType: SheetType): string {
  const base = `Extract:
1. Student name (handwritten at top, e.g. "Santosh Nag")
2. Roll number if visible
3. Marked answer for every question (1..${questionCount})`;

  const format = `Allowed answers: A, B, C, D, E, BLANK, MULTIPLE, UNCERTAIN
- E is valid if sheet has A-E options (otherwise ignore)
- Do not guess. If answer cannot be confidently determined, return UNCERTAIN.
- If no answer is visible, return BLANK.
Return only structured JSON with keys: student_name, roll_number, answers, uncertain_questions.
answers must have keys "1" to "${questionCount}".
uncertain_questions is array of question numbers where answer is UNCERTAIN or MULTIPLE.`;

  if (sheetType === "bubble") {
    return `You are reading a student's MCQ bubble answer sheet (OMR — circles/bubbles filled).
${base}
- If more than one bubble is filled for same question, return MULTIPLE.
- If no bubble is filled, return BLANK.
${format}`;
  }

  if (sheetType === "handwritten") {
    return `You are reading a student's handwritten MCQ answer list on plain paper.
Students write answers as a numbered list, e.g.:
  "1. a  2. b  3. c  4. d"
  "1:a 2:c 3:b ..."
  "1) A  2) B"
  "Q1 - A , Q2 - C"
  or two columns, one answer per line.

${base}
- Handwriting may be cursive/print, upper or lower case (a/b/c/d). Normalize to uppercase A/B/C/D.
- Separators vary: ".", ")", ":", "-", "," or space. Example "1.a" means Q1=A, "12 - c" means Q12=C.
- If a question number is missing/skipped, treat as BLANK.
- If handwriting is illegible/crossed-out/with two letters, return UNCERTAIN (use MULTIPLE only if two distinct options are clearly written).
- Be tolerant of numbering styles but strict about letter: only A-E are valid options.
${format}`;
  }

  // auto: detect
  return `You are reading a student's MCQ answer sheet. It may be EITHER:
(A) a bubble/OMR sheet (filled circles), OR
(B) a handwritten answer list on plain paper (e.g. "1. a  2. b  3. c ...", "1:a 2:c", "Q1 - A").

First determine the sheet type, then:
${base}

For BUBBLE sheets:
- If more than one bubble filled for same question, return MULTIPLE.
- If no bubble filled, return BLANK.

For HANDWRITTEN lists:
- Students write "1. a  2. b ..." with separators ".", ")", ":", "-", ",", space. Normalize a/b/c/d -> A/B/C/D.
- Missing number -> BLANK. Illegible/crossed-out/two letters -> UNCERTAIN.
- Only A-E are valid option letters.

${format}
Always return JSON with the same schema regardless of sheet type.`;
}

async function visionLLMExtraction(
  imagePath: string,
  questionCount: number,
  cfg: import("./llm").LLMConfig,
  sheetType: SheetType = "bubble"
): Promise<VisionExtractionResult> {
  // imagePath is like /uploads/student-sheets/xxx.jpeg -> on disk at public/uploads/... or /tmp/uploads/... on Vercel
  const candidates = [
    path.join(process.cwd(), "public", imagePath.replace(/^\//, "")),
    path.join("/tmp", imagePath.replace(/^\//, "")),
    path.join(process.cwd(), imagePath.replace(/^\//, "")),
  ];
  let buffer: Buffer | null = null;
  let absolutePath = candidates[0];
  for (const p of candidates) {
    try {
      buffer = fs.readFileSync(p);
      absolutePath = p;
      break;
    } catch {}
  }
  if (!buffer) {
    throw new Error(`Image file not found: ${imagePath} (tried ${candidates.join(", ")})`);
  }
  const base64 = buffer.toString("base64");
  const ext = absolutePath.split(".").pop()?.toLowerCase() || "jpeg";
  const mime = ext === "png" ? "image/png" : ext === "pdf" ? "application/pdf" : ext === "jpg" ? "image/jpeg" : ext === "jpeg" ? "image/jpeg" : ext === "heic" ? "image/heic" : ext === "heif" ? "image/heif" : ext === "webp" ? "image/webp" : "image/jpeg";

  // For PDF, we'd need to convert to image; for MVP send as image or skip
  // Simplified: assume image

  const systemPrompt = buildSystemPrompt(questionCount, sheetType);

  const url = getChatCompletionsUrl(cfg.baseUrl);
  const res = await fetch(url, {
    method: "POST",
    headers: cfg.headers,
    body: JSON.stringify({
      model: cfg.visionModel,
      messages: [
        { role: "system", content: systemPrompt },
        {
          role: "user",
          content: [
            { type: "text", text: `Expected number of questions: ${questionCount}. Sheet type hint: ${sheetType} (if auto, detect whether bubble OMR or handwritten list). Extract the answers.` },
            { type: "image_url", image_url: { url: `data:${mime};base64,${base64}` } },
          ],
        },
      ],
      temperature: 0,
      max_tokens: 4000,
      response_format: { type: "json_object" },
    }),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Vision LLM error ${res.status}: ${err}`);
  }

  const data = await res.json();
  let content = data.choices?.[0]?.message?.content;
  if (!content) throw new Error("Empty vision response");
  // Handle content being array or string, strip markdown fences
  if (Array.isArray(content)) content = content.map((c:any)=>c.text||c).join("\n");
  if (typeof content !== "string") content = JSON.stringify(content);
  content = content.trim().replace(/^```(?:json)?\s*/i,"").replace(/```\s*$/,"").trim();
  let parsed: any;
  try { parsed = JSON.parse(content); }
  catch {
    // Try extract JSON substring
    const m = content.match(/\{[\s\S]*\}/);
    if (!m) throw new Error("Vision LLM did not return JSON: " + content.slice(0,300));
    parsed = JSON.parse(m[0]);
  }

  // Validate & normalize
  const answers: Record<string, string> = {};
  const rawAnswers = parsed.answers || parsed.answer_key || parsed;
  for (let i = 1; i <= questionCount; i++) {
    const v = rawAnswers[String(i)];
    const norm = typeof v === "string" ? v.trim().toUpperCase() : "";
    if (["A","B","C","D","E","BLANK","MULTIPLE","UNCERTAIN"].includes(norm)) {
      answers[String(i)] = norm;
    } else if (norm) {
      // Single letter fallback
      answers[String(i)] = norm;
    } else {
      answers[String(i)] = "UNCERTAIN";
    }
  }

  return {
    student_name: parsed.student_name || null,
    roll_number: parsed.roll_number ? String(parsed.roll_number) : null,
    answers,
    uncertain_questions: Array.isArray(parsed.uncertain_questions) ? parsed.uncertain_questions.map(String) : [],
  };
}

// Mock for local testing without API key: deterministic but varied
const MOCK_NAMES = ["Rahul Sharma", "Anjali Gupta", "Rohan Das", "Priya Patel", "Aman Verma", "Sneha Reddy", "Vikram Singh", "Neha Kapoor"];

function seededRandom(seed: number) {
  const x = Math.sin(seed) * 10000;
  return x - Math.floor(x);
}

function mockExtraction(questionCount: number, imagePath: string): VisionExtractionResult {
  // Seed from imagePath hash + questionCount
  let hash = 0;
  for (let i = 0; i < imagePath.length; i++) hash = (hash * 31 + imagePath.charCodeAt(i)) >>> 0;
  const seed = hash % 100000;

  const nameIndex = Math.floor(seededRandom(seed) * MOCK_NAMES.length);
  const student_name = MOCK_NAMES[nameIndex];
  const roll_number = String((Math.floor(seededRandom(seed + 1) * 50) + 1));

  const answers: Record<string, string> = {};
  const uncertain_questions: string[] = [];

  for (let i = 1; i <= questionCount; i++) {
    const r = seededRandom(seed + i * 997);
    let ans: string;
    if (r < 0.02) { ans = "BLANK"; }
    else if (r < 0.04) { ans = "UNCERTAIN"; uncertain_questions.push(String(i)); }
    else if (r < 0.05) { ans = "MULTIPLE"; uncertain_questions.push(String(i)); }
    else {
      const opts = ["A","B","C","D"];
      ans = opts[Math.floor(seededRandom(seed + i * 13) * 4)];
    }
    answers[String(i)] = ans;
  }

  return { student_name, roll_number, answers, uncertain_questions };
}

export function validateVisionResult(result: VisionExtractionResult, questionCount: number): boolean {
  if (!result.answers || typeof result.answers !== "object") return false;
  const keys = Object.keys(result.answers);
  if (keys.length !== questionCount) return false;
  for (const v of Object.values(result.answers)) {
    if (!["A","B","C","D","E","BLANK","MULTIPLE","UNCERTAIN"].includes(v)) return false;
  }
  return true;
}
