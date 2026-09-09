# OmiCheckr — Vision LLM Bubble Sheet Grader (MVP)

Mobile-first web app to test whether a Vision LLM can reliably grade photographed bubble sheets.

## MVP Flow

```
Create Exam → Upload Question Paper & Answer Key → Verify Key → Scan Papers (Submit & Next, no waiting) → Results → Student Detail & PDF Report
```

Core UX: teacher does **not** wait for Vision LLM to finish before capturing next sheet. Background processing with polling (`GET /api/exams/[id]/submissions` every 2s, status: `UPLOADED → PROCESSING → COMPLETED/REVIEW_REQUIRED/FAILED`).

5 students max, MCQ A/B/C/D, grading is deterministic backend logic — Vision LLM only *reads* bubbles, never scores.

## Stack

- Next.js 16 (App Router) + Tailwind CSS (mobile-first)
- File-based JSON DB (`data/exams.json`, `data/submissions.json`) — swap to Supabase via `src/lib/db.ts`
- Local filesystem storage `public/uploads/` — swap to Supabase Storage
- `pdf-lib` for PDF reports, `pdf-parse` + `mammoth` for doc extraction
- Vision abstraction `src/lib/visionExtractor.ts` → `extractAnswerSheet(image, count)`

## Quick Start

```bash
npm install
npm run dev # http://localhost:3000
```

No API key required — runs in `MOCK_VISION` mode with seeded random extractions.

**Use OpenRouter:** set `OPENROUTER_API_KEY` + `OPENROUTER_VISION_MODEL` (e.g. `google/gemini-2.0-flash-001` — $0.10/1M tokens, vision) — same OpenAI-compatible API at `https://openrouter.ai/api/v1`. Or use `OPENAI_API_KEY` directly. Config is unified in `src/lib/llm.ts` — just set `MOCK_VISION=false`. See `.env.example`.

## Tests Done

- Created exam (10 Q, answer key `B,D,A,C,B,A,D,C,B,A`)
- Uploaded 5 sheets concurrently — each returned `UPLOADED` instantly, background job completed in ~2s
- Polling showed `Processing...` → `Completed`/`Review Required`
- Rejected 6th upload (429)
- Edited extracted answers → score recalculated deterministically
- Generated PDF report (`/api/submissions/[id]/report`)

## Architecture

```
src/lib/answerKeyExtractor.ts  Document → JSON { "1":"B" }  (LLM + regex fallback)
src/lib/visionExtractor.ts     Image → { student_name, roll_number, answers, uncertain_questions }
src/lib/grading.ts             deterministic: correct×marks - incorrect×negative
src/lib/pdfReport.ts            Result → PDF via pdf-lib
src/lib/db.ts                  JSON file DB (3 tables → 2 files for MVP)
```

Swap Vision model by implementing `VisionExtractor` interface — answer key never sent to Vision LLM (isolation rule).

## Screens (6)

1. `/` — Home (Create Exam + recent exams)
2. `/exam/create` — Create Exam (name, subject, Q count, marks, paper + key upload)
3. `/exam/[id]/verify` — Verify Answer Key (editable grid + bulk comma edit)
4. `/exam/[id]/scan` — Scan Papers (Take Photo with `capture="environment"` + Upload, preview → Submit & Next)
5. `/exam/[id]/results` — Results (score per student, status badges)
6. `/exam/[id]/student/[submissionId]` — Student Detail (edit answers, recalc, download PDF)

## Deploy

```bash
npm run build && npm start
```

**OpenRouter example:**
```bash
OPENROUTER_API_KEY=sk-or-v1-...
OPENROUTER_VISION_MODEL=google/gemini-2.0-flash-001
MOCK_VISION=false npm run dev
```
Or for OpenAI: `OPENAI_API_KEY=sk-... MOCK_VISION=false`. Any OpenAI-compatible endpoint works via `LLM_BASE_URL` + `LLM_API_KEY`.

For production Vision LLM set key + `MOCK_VISION=false`.
