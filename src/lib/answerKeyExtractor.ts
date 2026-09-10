// Answer key extraction: converts PDF/DOCX/text/image to structured JSON { "1":"B", ...}
// Uses LLM (OpenAI or OpenRouter) if API key present, otherwise regex heuristic fallback.
// Unified: supports doc (pdf/docx/txt) via text extraction + image (jpg/png/webp/heic) via Vision LLM (VLM).

const IMAGE_EXTS = new Set(["jpg", "jpeg", "png", "webp", "heic", "heif", "gif", "bmp"]);

export function isAnswerKeyImageFile(filename: string): boolean {
  const ext = filename.toLowerCase().split(".").pop() || "";
  return IMAGE_EXTS.has(ext);
}

export function mimeFromFilename(filename: string): string {
  const ext = filename.toLowerCase().split(".").pop() || "";
  if (ext === "png") return "image/png";
  if (ext === "webp") return "image/webp";
  if (ext === "gif") return "image/gif";
  if (ext === "bmp") return "image/bmp";
  if (ext === "heic") return "image/heic";
  if (ext === "heif") return "image/heif";
  return "image/jpeg";
}

export async function extractAnswerKey(buffer: Buffer, filename: string, questionCount: number): Promise<Record<string, string>> {
  // Route image files to Vision LLM path
  if (isAnswerKeyImageFile(filename)) {
    return extractAnswerKeyFromImage(buffer, filename, questionCount);
  }

  let text = "";

  const ext = filename.toLowerCase().split(".").pop();

  try {
    if (ext === "pdf") {
      // pdf-parse - handle both CJS and ESM exports
      const mod: any = await import("pdf-parse");
      const pdfParse = mod.default || mod;
      const data = await pdfParse(buffer);
      text = data.text;
    } else if (ext === "docx") {
      const mammoth = await import("mammoth");
      const result = await mammoth.extractRawText({ buffer });
      text = result.value;
    } else if (ext === "txt" || ext === "csv") {
      text = buffer.toString("utf-8");
    } else {
      // Try as text anyway
      text = buffer.toString("utf-8");
    }
  } catch (e) {
    console.error("Failed to extract text from answer key", e);
    text = buffer.toString("utf-8");
  }

  if (!text.trim()) {
    throw new Error("Could not extract text from answer key file");
  }

  // Try LLM if key available (OpenAI or OpenRouter)
  const { getLLMConfig } = await import("./llm");
  if (getLLMConfig()) {
    try {
      const llmResult = await extractWithLLM(text, questionCount);
      if (llmResult && Object.keys(llmResult).length > 0) return llmResult;
    } catch (e) {
      console.warn("LLM answer key extraction failed, falling back to regex", e);
    }
  }

  // Fallback regex heuristic
  return parseAnswerKeyHeuristic(text, questionCount);
}

function parseAnswerKeyHeuristic(text: string, questionCount: number): Record<string, string> {
  const result: Record<string, string> = {};

  // Normalize
  const lines = text.split(/\r?\n/);

  // Try patterns:
  // "1. B" , "1) B", "1: B", "1 - B", "1 B", "Q1 B", "1. B", "1,B"
  const regexes = [
    /^\s*(?:Q\.?\s*)?(\d{1,3})\s*[\.\)\:\-\,\s]+\s*([A-D])\s*$/i,
    /(\d{1,3})\s*[\.\)\:\-\,]\s*([A-D])/i,
  ];

  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;

    // Try to find all matches in line (handles comma-separated like "1 B, 2 D")
    // Split by comma or semicolon first
    const parts = trimmed.split(/[,;]+/);
    for (const part of parts) {
      for (const re of regexes) {
        const m = part.match(re);
        if (m) {
          const q = parseInt(m[1], 10);
          const ans = m[2].toUpperCase();
          if (q >= 1 && q <= questionCount && ["A","B","C","D"].includes(ans)) {
            result[String(q)] = ans;
            break;
          }
        }
      }
    }

    // Also try space-separated tokens: "B D A C ..." sequential
    // Only if we haven't filled many yet
  }

  // If still sparse, try sequential single letters
  if (Object.keys(result).length < questionCount * 0.5) {
    // Remove all non A-D letters and try sequential
    const tokens = text.match(/\b[A-D]\b/gi);
    if (tokens && tokens.length >= questionCount) {
      // Assume tokens are in order 1..n
      const seq: Record<string,string> = {};
      for (let i=0;i<questionCount;i++) {
        seq[String(i+1)] = tokens[i].toUpperCase();
      }
      // Prefer seq if heuristic found very few
      if (Object.keys(result).length < questionCount * 0.3) {
        return seq;
      }
    }
  }

  // Fill missing with UNCERTAIN placeholder? For MVP require all.
  // But if incomplete, try to use whatever we have; UI allows manual edit
  if (Object.keys(result).length === 0) {
    throw new Error("Could not parse answer key. Please enter manually as e.g. B,D,A,C");
  }

  return result;
}

export async function extractAnswerKeyFromImage(buffer: Buffer, filename: string, questionCount: number): Promise<Record<string, string>> {
  const { getLLMConfig } = await import("./llm");
  const cfg = getLLMConfig();
  // MOCK / no-key fallback: deterministic mock so flow still works in demo mode
  if (!cfg) {
    console.warn(`[answerKey] No LLM config — using MOCK image extraction for ${filename} (${questionCount} Q). Set OPENROUTER_API_KEY + MOCK_VISION=false for real VLM.`);
    return mockAnswerKeyFromImage(questionCount, filename);
  }
  const mime = mimeFromFilename(filename);
  const base64 = buffer.toString("base64");
  return extractWithVisionLLM(base64, mime, questionCount, cfg);
}

async function extractWithVisionLLM(base64: string, mime: string, questionCount: number, cfg: import("./llm").LLMConfig): Promise<Record<string, string>> {
  const { getChatCompletionsUrl } = await import("./llm");
  const url = getChatCompletionsUrl(cfg.baseUrl);
  const systemPrompt = `You extract a MCQ answer key from a photo. The photo may be:
- a printed answer key (table/list: "1. B  2. A ...", "Q1 - B", "1) C")
- a handwritten list on plain paper: "1.a 2.b 3.c ..." (upper/lower case, separators ".", ")", ":", "-", ",")
- a two-column or grid layout.

Expected ${questionCount} questions. Options are A/B/C/D (or A-E if present; otherwise A-D only). One answer per question.
Return ONLY valid JSON object mapping question number string to answer letter, e.g. {"1":"B","2":"D","3":"A"}.
If fewer than ${questionCount} answers are clearly visible, return only those found (do not guess).
Normalize a/b/c/d/e → A/B/C/D/E. Ignore non-option text.
Do not wrap in markdown fences.`;

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
            { type: "text", text: `Extract the answer key. Expected ${questionCount} questions. Return JSON {"1":"A",...}.` },
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
    const errText = await res.text();
    throw new Error(`Vision LLM error ${res.status}: ${errText}`);
  }
  const data = await res.json();
  let content = data.choices?.[0]?.message?.content;
  if (!content) throw new Error("No Vision LLM output");
  if (Array.isArray(content)) content = content.map((c: any) => c.text || c).join("\n");
  if (typeof content !== "string") content = JSON.stringify(content);
  content = content.trim().replace(/^```(?:json)?\s*/i, "").replace(/```\s*$/,"").trim();
  let parsed: any;
  try { parsed = JSON.parse(content); } catch {
    const m = content.match(/\{[\s\S]*\}/);
    if (!m) throw new Error("Vision LLM did not return JSON: " + content.slice(0, 500));
    parsed = JSON.parse(m[0]);
  }
  const source = parsed.answers || parsed.answer_key || parsed.answerKey || parsed;
  const out: Record<string,string> = {};
  for (const [k,v] of Object.entries(source)) {
    const num = parseInt(k,10);
    if (isNaN(num) || num < 1 || num > questionCount) continue;
    if (typeof v !== "string") continue;
    const val = v.trim().toUpperCase();
    if (/^[A-E]$/.test(val)) out[String(num)] = val;
  }
  if (Object.keys(out).length === 0) throw new Error("Vision LLM returned no valid answers");
  return out;
}

function mockAnswerKeyFromImage(questionCount: number, filename: string): Record<string,string> {
  // Deterministic mock: seeded by filename so same image gives same key in demo
  let hash = 0;
  for (let i=0;i<filename.length;i++) hash = (hash*31 + filename.charCodeAt(i)) >>> 0;
  const seed = hash % 100000;
  const seededRandom = (s:number)=> { const x=Math.sin(s)*10000; return x-Math.floor(x); };
  const opts = ["A","B","C","D"];
  const out: Record<string,string> = {};
  for (let i=1;i<=questionCount;i++) out[String(i)] = opts[Math.floor(seededRandom(seed + i*13)*4)];
  return out;
}

async function extractWithLLM(text: string, questionCount: number): Promise<Record<string,string>> {
  const { getLLMConfig, getChatCompletionsUrl } = await import("./llm");
  const cfg = getLLMConfig();
  if (!cfg) throw new Error("No LLM config");

  const prompt = `Extract answer key from this document. Expected ${questionCount} questions, options A/B/C/D, one answer per question.
Return ONLY JSON object mapping question number string to answer letter, e.g. {"1":"B","2":"D"}.
If fewer answers found, return only those found.

Document:
${text.slice(0, 8000)}`;

  const url = getChatCompletionsUrl(cfg.baseUrl);
  const res = await fetch(url, {
    method: "POST",
    headers: cfg.headers,
    body: JSON.stringify({
      model: cfg.model,
      messages: [
        { role: "system", content: "You extract MCQ answer keys. Return valid JSON only." },
        { role: "user", content: prompt },
      ],
      temperature: 0,
      response_format: { type: "json_object" },
    }),
  });

  if (!res.ok) {
    const errText = await res.text();
    throw new Error(`LLM error ${res.status}: ${errText}`);
  }
  const data = await res.json();
  const content = data.choices?.[0]?.message?.content;
  if (!content) throw new Error("No LLM output");
  const parsed = typeof content === "string" ? JSON.parse(content) : content;
  // Normalize — handle if LLM returns { "1": "B" } directly or nested
  const source = parsed.answers || parsed.answer_key || parsed;
  const out: Record<string,string> = {};
  for (const [k,v] of Object.entries(source)) {
    const num = parseInt(k,10);
    if (!isNaN(num) && typeof v === "string" && /^[A-D]$/i.test(v.trim())) {
      out[String(num)] = v.trim().toUpperCase();
    }
  }
  return out;
}
