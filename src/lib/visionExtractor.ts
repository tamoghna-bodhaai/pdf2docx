import { VisionExtractionResult, SheetType } from "./types";
import fs from "fs";
import path from "path";
import { getLLMConfig, getChatCompletionsUrl, getConfidenceThreshold } from "./llm";
import { sha256, hashToSeed } from "./imageHash";
import { preprocessForVision } from "./imagePreprocess";

/**
 * Abstraction: extractAnswerSheet(...)
 * Supports bubble OMR, handwritten list, auto.
 * Hardened: preprocessing, hash+seed determinism, strict json_schema, confidence gating, single retry.
 */

export async function extractAnswerSheet(
  imagePath: string,
  questionCount: number,
  sheetType: SheetType = "bubble"
): Promise<VisionExtractionResult> {
  const cfg = getLLMConfig();
  if (!cfg) {
    console.warn(`[vision] No LLM config — using MOCK extraction (${sheetType}) (demo data).`);
    return mockExtraction(questionCount, imagePath);
  }
  // Resolve file → buffer then delegate to buffer path
  const { buffer, mime, absolutePath } = resolveImageBuffer(imagePath);
  const hash = sha256(buffer);
  const seed = hashToSeed(hash);
  // Preprocess (sharp) — best effort, fallback to original
  const pre = await preprocessForVision(buffer, mime);
  const effectiveBuffer = pre.didPreprocess ? pre.buffer : buffer;
  const effectiveMime = pre.didPreprocess ? pre.mime : mime;

  console.log(
    `[vision] ${absolutePath} hash=${hash.slice(0,12)} seed=${seed} pre=${pre.didPreprocess ? `${pre.originalSize}->${pre.processedSize}` : "noop"} sheetType=${sheetType}`
  );

  return await visionLLMExtractionWithRetry(effectiveBuffer, effectiveMime, hash, seed, questionCount, cfg, sheetType, pre.didPreprocess);
}

// Buffer-native entry for direct uploads (hash already computed upstream)
export async function extractAnswerSheetFromBuffer(
  buffer: Buffer,
  mime: string,
  questionCount: number,
  sheetType: SheetType = "bubble",
  precomputedHash?: string
): Promise<VisionExtractionResult> {
  const cfg = getLLMConfig();
  if (!cfg) return mockExtraction(questionCount, precomputedHash || "mock-buffer");
  const hash = precomputedHash || sha256(buffer);
  const seed = hashToSeed(hash);
  const pre = await preprocessForVision(buffer, mime);
  const effectiveBuffer = pre.didPreprocess ? pre.buffer : buffer;
  const effectiveMime = pre.didPreprocess ? pre.mime : mime;
  return await visionLLMExtractionWithRetry(effectiveBuffer, effectiveMime, hash, seed, questionCount, cfg, sheetType, pre.didPreprocess);
}

function resolveImageBuffer(imagePath: string): { buffer: Buffer; mime: string; absolutePath: string } {
  const roots: string[] = [];
  if (process.env.RAILWAY_VOLUME_MOUNT_PATH) roots.push(path.join(process.env.RAILWAY_VOLUME_MOUNT_PATH, imagePath.replace(/^\//, "")));
  roots.push(path.join(process.cwd(), "public", imagePath.replace(/^\//, "")));
  roots.push(path.join("/tmp", imagePath.replace(/^\//, "")));
  roots.push(path.join(process.cwd(), imagePath.replace(/^\//, "")));

  let buffer: Buffer | null = null;
  let absolutePath = roots[0];
  for (const p of roots) {
    try {
      buffer = fs.readFileSync(p);
      absolutePath = p;
      break;
    } catch {}
  }
  if (!buffer) throw new Error(`Image file not found: ${imagePath} (tried ${roots.join(", ")})`);
  const ext = absolutePath.split(".").pop()?.toLowerCase() || "jpeg";
  const mime =
    ext === "png"
      ? "image/png"
      : ext === "pdf"
        ? "application/pdf"
        : ext === "jpg"
          ? "image/jpeg"
          : ext === "jpeg"
            ? "image/jpeg"
            : ext === "heic"
              ? "image/heic"
              : ext === "heif"
                ? "image/heif"
                : ext === "webp"
                  ? "image/webp"
                  : "image/jpeg";
  return { buffer, mime, absolutePath };
}

function buildSystemPrompt(questionCount: number, sheetType: SheetType): string {
  const base = `Extract:
1. Student name (handwritten at top, e.g. "Santosh Nag")
2. Marked answer for every question (1..${questionCount})`;

  const confidenceInstruction = `Also return per-question confidence 0.0-1.0 in "confidences" object (keys "1".."${questionCount}"). High = certain, low = ambiguous. If confidence <0.6 you MUST return UNCERTAIN (do not guess).`;

  const format = `STRICT RULES:
- Allowed answers: A, B, C, D, E, BLANK, MULTIPLE, UNCERTAIN
- E is valid only if sheet visibly has A-E options; otherwise ignore.
- If no bubble/answer is visibly filled/written → return BLANK.
- If answer cannot be confidently determined (faint, partial, smudged, crossed-out, two marks without clear fill) → return UNCERTAIN. Do NOT guess.
- For bubble sheets: fill must be >50% dark, circular, centered. Light tick or dot alone is NOT a fill → UNCERTAIN/BLANK. More than one filled bubble for same question → MULTIPLE.
- ${confidenceInstruction}
- Return ONLY JSON with keys: student_name (string|null), roll_number (string|null), answers (object with keys "1" to "${questionCount}"), uncertain_questions (array of question numbers where answer is UNCERTAIN or MULTIPLE), confidences (object), detected_sheetType (only if sheetType hint is "auto": "bubble"|"handwritten").
- answers must have EXACTLY ${questionCount} keys, from "1" to "${questionCount}".
- uncertain_questions must list every question where answer is UNCERTAIN or MULTIPLE.`;

  if (sheetType === "bubble") {
    return `You are reading a student's MCQ bubble answer sheet (OMR — filled circles). Precision is critical: faint marks are NOT fills.

${base}
- If more than one bubble is clearly filled for same question → MULTIPLE.
- If no bubble meets fill threshold → BLANK.
${format}

FEW-SHOT calibration:
- Q1 row shows A lightly dotted, B 90% filled → Q1=B confidence 0.97
- Q2 row shows no bubble filled → Q2=BLANK confidence 0.99
- Q3 row shows A and C both half-filled → Q3=MULTIPLE confidence 0.45, in uncertain_questions`;
  }

  if (sheetType === "handwritten") {
    return `You are reading a student's handwritten MCQ answer list on plain paper. Normalize handwriting to A-E.

Students write e.g.:
  "1. a  2. b  3. c  4. d"
  "1:a 2:c 3:b ..."
  "1) A  2) B"
  "Q1 - A , Q2 - C" or two columns, one per line.

${base}
- Handwriting may be cursive/print, upper or lower case (a/b/c/d). Normalize to uppercase A/B/C/D.
- Separators: ".", ")", ":", "-", "," or space. "1.a" means Q1=A, "12 - c" means Q12=C.
- Missing question number → BLANK.
- Illegible/crossed-out/with two letters → UNCERTAIN (use MULTIPLE only if two distinct options clearly written).
- Only A-E are valid option letters.
${format}`;
  }

  // auto
  return `You are reading a student's MCQ answer sheet. It may be EITHER:
(A) a bubble/OMR sheet (filled circles), OR
(B) a handwritten answer list on plain paper (e.g. "1. a  2. b ...", "1:a 2:c", "Q1 - A").

First determine detected_sheetType ("bubble" or "handwritten"), then:

${base}

For BUBBLE sheets:
- Fill must be >50% dark, circular. Light tick → UNCERTAIN/BLANK. Multiple fills → MULTIPLE. Empty → BLANK.

For HANDWRITTEN lists:
- "1. a  2. b ..." separators ".", ")", ":", "-", ",", space. Normalize a/b/c/d -> A/B/C/D.
- Missing number -> BLANK. Illegible/crossed-out/two letters -> UNCERTAIN.
- Only A-E are valid letters.

${format}
Always return JSON with the same schema regardless of sheet type, including detected_sheetType.`;
}

function buildJsonSchema(questionCount: number, sheetType: SheetType): any {
  const answerEnum = ["A", "B", "C", "D", "E", "BLANK", "MULTIPLE", "UNCERTAIN"];
  const props: Record<string, any> = {};
  const required: string[] = [];
  for (let i = 1; i <= questionCount; i++) {
    props[String(i)] = { type: "string", enum: answerEnum };
    required.push(String(i));
  }
  const schema: any = {
    type: "object",
    properties: {
      student_name: { type: ["string", "null"], description: "Handwritten name at top or null" },
      roll_number: { type: ["string", "null"] },
      answers: {
        type: "object",
        properties: props,
        required,
        additionalProperties: false,
      },
      uncertain_questions: { type: "array", items: { type: "string" } },
      confidences: {
        type: "object",
        description: "Per-question confidence 0-1",
        additionalProperties: { type: "number", minimum: 0, maximum: 1 },
      },
      ...(sheetType === "auto" ? { detected_sheetType: { type: "string", enum: ["bubble", "handwritten"] } } : {}),
    },
    required: ["student_name", "answers", "uncertain_questions", "confidences"],
    additionalProperties: false,
  };
  return schema;
}

async function visionLLMExtractionWithRetry(
  buffer: Buffer,
  mime: string,
  hash: string,
  seed: number,
  questionCount: number,
  cfg: import("./llm").LLMConfig,
  sheetType: SheetType,
  didPreprocess: boolean
): Promise<VisionExtractionResult> {
  const started = Date.now();
  const threshold = getConfidenceThreshold();
  let lastError: any = null;
  for (let attempt = 0; attempt < 2; attempt++) {
    const isRetry = attempt === 1;
    const systemPrompt = buildSystemPrompt(questionCount, sheetType) + (isRetry ? "\n\nRETRY: Previous output was invalid or failed validation. Return STRICT JSON conforming exactly to json_schema. Do NOT wrap in markdown." : "");
    try {
      const raw = await callVisionLLM(buffer, mime, systemPrompt, questionCount, sheetType, cfg, seed + attempt, hash);
      const gated = applyConfidenceGate(raw, threshold);
      const validation = validateVisionResult(gated, questionCount);
      if (!validation) throw new Error(`Validation failed on attempt ${attempt + 1}`);
      const latencyMs = Date.now() - started;
      return {
        ...gated,
        visionMeta: {
          model: cfg.visionModel,
          imageHash: hash,
          latencyMs,
          confidences: gated.confidences || null,
          detectedSheetType: gated.detectedSheetType || null,
          retryCount: attempt,
          cached: false,
        },
      };
    } catch (e: any) {
      lastError = e;
      console.warn(`[vision] attempt ${attempt + 1} failed hash=${hash.slice(0, 12)}:`, e.message);
      if (attempt === 1) break;
      // brief backoff before retry
      await new Promise((r) => setTimeout(r, 400));
    }
  }
  throw lastError || new Error("Vision extraction failed after retry");
}

async function callVisionLLM(
  buffer: Buffer,
  mime: string,
  systemPrompt: string,
  questionCount: number,
  sheetType: SheetType,
  cfg: import("./llm").LLMConfig,
  seed: number,
  hash: string
): Promise<VisionExtractionResult> {
  const base64 = buffer.toString("base64");
  const url = getChatCompletionsUrl(cfg.baseUrl);
  const schema = buildJsonSchema(questionCount, sheetType);

  // Prefer json_schema if supported; fallback to json_object via response_format
  const useJsonSchema = true;
  const body: any = {
    model: cfg.visionModel,
    messages: [
      { role: "system", content: systemPrompt },
      {
        role: "user",
        content: [
          { type: "text", text: `Expected number of questions: ${questionCount}. Sheet type hint: ${sheetType} (if auto, detect bubble vs handwritten). Image SHA256 prefix: ${hash.slice(0, 12)}. Extract answers with per-question confidences.` },
          { type: "image_url", image_url: { url: `data:${mime};base64,${base64}` } },
        ],
      },
    ],
    temperature: 0,
    top_p: 1,
    max_tokens: 4000,
    seed,
  };
  if (useJsonSchema) {
    body.response_format = { type: "json_schema", json_schema: { name: "omr_extraction", strict: true, schema } };
  } else {
    body.response_format = { type: "json_object" };
  }

  const res = await fetch(url, {
    method: "POST",
    headers: cfg.headers,
    body: JSON.stringify(body),
  });

  if (!res.ok) {
    const err = await res.text();
    throw new Error(`Vision LLM error ${res.status}: ${err.slice(0, 800)}`);
  }

  const data = await res.json();
  let content = data.choices?.[0]?.message?.content;
  if (!content) throw new Error("Empty vision response");
  if (Array.isArray(content)) content = content.map((c: any) => c.text || c).join("\n");
  if (typeof content !== "string") content = JSON.stringify(content);
  content = content.trim().replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/,"").trim();
  let parsed: any;
  try {
    parsed = JSON.parse(content);
  } catch {
    const m = content.match(/\{[\s\S]*\}/);
    if (!m) throw new Error("Vision LLM did not return JSON: " + content.slice(0, 500));
    parsed = JSON.parse(m[0]);
  }

  const answers: Record<string, string> = {};
  const rawAnswers = parsed.answers || parsed.answer_key || parsed;
  for (let i = 1; i <= questionCount; i++) {
    const v = rawAnswers[String(i)];
    const norm = typeof v === "string" ? v.trim().toUpperCase() : "";
    if (["A", "B", "C", "D", "E", "BLANK", "MULTIPLE", "UNCERTAIN"].includes(norm)) {
      answers[String(i)] = norm;
    } else if (norm && /^[A-E]$/.test(norm)) {
      answers[String(i)] = norm;
    } else if (norm) {
      answers[String(i)] = norm; // will be caught by validation
    } else {
      answers[String(i)] = "UNCERTAIN";
    }
  }

  const confidences: Record<string, number> = {};
  const rawConf = parsed.confidences || {};
  for (let i = 1; i <= questionCount; i++) {
    const c = rawConf[String(i)];
    if (typeof c === "number" && !isNaN(c)) confidences[String(i)] = Math.max(0, Math.min(1, c));
    else if (typeof c === "string" && !isNaN(parseFloat(c))) confidences[String(i)] = Math.max(0, Math.min(1, parseFloat(c)));
    else {
      // Infer: certain answers get 0.95, uncertain 0.4
      const a = answers[String(i)];
      confidences[String(i)] = a === "UNCERTAIN" || a === "MULTIPLE" ? 0.4 : 0.92;
    }
  }

  const uncertainQs: string[] = Array.isArray(parsed.uncertain_questions) ? parsed.uncertain_questions.map(String) : [];

  return {
    student_name: parsed.student_name || null,
    roll_number: parsed.roll_number ? String(parsed.roll_number) : null,
    answers,
    uncertain_questions: uncertainQs,
    confidences,
    detectedSheetType: parsed.detected_sheetType || null,
  };
}

function applyConfidenceGate(result: VisionExtractionResult, threshold: number): VisionExtractionResult {
  const answers = { ...result.answers };
  const confidences = result.confidences || {};
  const uncertainSet = new Set(result.uncertain_questions.map(String));

  for (let i = 1; i <= Object.keys(answers).length; i++) {
    const k = String(i);
    const conf = confidences[k];
    if (typeof conf === "number" && conf < threshold) {
      // Demote low-confidence to UNCERTAIN
      if (answers[k] !== "BLANK" && answers[k] !== "UNCERTAIN" && answers[k] !== "MULTIPLE") {
        answers[k] = "UNCERTAIN";
      } else if (answers[k] === "BLANK" && conf < 0.4) {
        // Very low confidence blank stays blank
      }
      uncertainSet.add(k);
    }
    // Ensure MULTIPLE/UNCERTAIN are in uncertain list
    if (answers[k] === "MULTIPLE" || answers[k] === "UNCERTAIN") uncertainSet.add(k);
  }

  return { ...result, answers, uncertain_questions: Array.from(uncertainSet).sort((a, b) => parseInt(a) - parseInt(b)) };
}

// Mock for local testing without API key
const MOCK_NAMES = ["Rahul Sharma", "Anjali Gupta", "Rohan Das", "Priya Patel", "Aman Verma", "Sneha Reddy", "Vikram Singh", "Neha Kapoor"];

function seededRandom(seed: number) {
  const x = Math.sin(seed) * 10000;
  return x - Math.floor(x);
}

function mockExtraction(questionCount: number, imagePath: string): VisionExtractionResult {
  let hash = 0;
  for (let i = 0; i < imagePath.length; i++) hash = (hash * 31 + imagePath.charCodeAt(i)) >>> 0;
  const seed = hash % 100000;

  const nameIndex = Math.floor(seededRandom(seed) * MOCK_NAMES.length);
  const student_name = MOCK_NAMES[nameIndex];
  const roll_number = String(Math.floor(seededRandom(seed + 1) * 50) + 1);

  const answers: Record<string, string> = {};
  const uncertain_questions: string[] = [];
  const confidences: Record<string, number> = {};

  for (let i = 1; i <= questionCount; i++) {
    const r = seededRandom(seed + i * 997);
    let ans: string;
    if (r < 0.02) {
      ans = "BLANK";
      confidences[String(i)] = 0.99;
    } else if (r < 0.04) {
      ans = "UNCERTAIN";
      uncertain_questions.push(String(i));
      confidences[String(i)] = 0.35;
    } else if (r < 0.05) {
      ans = "MULTIPLE";
      uncertain_questions.push(String(i));
      confidences[String(i)] = 0.3;
    } else {
      const opts = ["A", "B", "C", "D"];
      ans = opts[Math.floor(seededRandom(seed + i * 13) * 4)];
      confidences[String(i)] = 0.92 + seededRandom(seed + i * 7) * 0.06;
    }
    answers[String(i)] = ans;
  }

  return { student_name, roll_number, answers, uncertain_questions, confidences };
}

export function validateVisionResult(result: VisionExtractionResult, questionCount: number): boolean {
  if (!result.answers || typeof result.answers !== "object") return false;
  const keys = Object.keys(result.answers);
  if (keys.length !== questionCount) return false;
  for (const v of Object.values(result.answers)) {
    if (!["A", "B", "C", "D", "E", "BLANK", "MULTIPLE", "UNCERTAIN"].includes(v)) return false;
  }
  // confidences optional but if present should have questionCount entries 0-1
  if (result.confidences) {
    for (const c of Object.values(result.confidences)) {
      if (typeof c !== "number" || c < 0 || c > 1) return false;
    }
  }
  return true;
}
