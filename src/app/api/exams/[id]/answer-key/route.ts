import { NextRequest, NextResponse } from "next/server";
import { getExam, readExams, writeExams } from "@/lib/db";

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await req.json();
  const { answerKeyJson } = body;
  if (!answerKeyJson || typeof answerKeyJson !== "object") {
    return NextResponse.json({ error: "Invalid answerKeyJson" }, { status: 400 });
  }
  const exams = readExams();
  const idx = exams.findIndex(e => e.id === id);
  if (idx === -1) return NextResponse.json({ error: "Exam not found" }, { status: 404 });

  // Validate
  const cleaned: Record<string,string> = {};
  for (const [k,v] of Object.entries(answerKeyJson)) {
    const num = parseInt(k,10);
    if (isNaN(num)) continue;
    const val = String(v).trim().toUpperCase();
    if (!["A","B","C","D"].includes(val)) return NextResponse.json({ error: `Invalid answer ${val} for Q${k}` }, { status: 400 });
    cleaned[String(num)] = val;
  }

  const qc = exams[idx].questionCount;
  if (Object.keys(cleaned).length !== qc) {
    return NextResponse.json({ error: `Expected ${qc} answers, got ${Object.keys(cleaned).length}` }, { status: 400 });
  }

  exams[idx].answerKeyJson = cleaned;
  exams[idx].status = "answer_key_ready";
  writeExams(exams);
  return NextResponse.json(exams[idx]);
}
