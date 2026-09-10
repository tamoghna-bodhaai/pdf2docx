import { NextRequest, NextResponse } from "next/server";
import { getExam, readExams, writeExams, readSubmissions, writeSubmissions } from "@/lib/db";
import { validateMarkingScheme, normalizeMarkingScheme, getMarkingScheme } from "@/lib/markingScheme";
import { gradeSubmission } from "@/lib/grading";

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const exam = getExam(id);
  if (!exam) return NextResponse.json({ error: "Exam not found" }, { status: 404 });
  return NextResponse.json(exam);
}

export async function DELETE(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const exams = readExams();
  const idx = exams.findIndex(e => e.id === id);
  if (idx === -1) return NextResponse.json({ error: "Exam not found" }, { status: 404 });

  const deletedExam = exams[idx];

  // Remove exam
  exams.splice(idx, 1);
  writeExams(exams);

  // Remove associated submissions (frees DB - MVP)
  const subs = readSubmissions();
  const remaining = subs.filter(s => s.examId !== id);
  if (remaining.length !== subs.length) {
    writeSubmissions(remaining);
  }

  // Best-effort file cleanup — Railway volume aware
  try {
    const fs = await import("fs");
    const path = await import("path");
    const toDelete: string[] = [];
    const baseCandidates = (url: string) => {
      const rel = url.replace(/^\//, "");
      const c: string[] = [];
      if (process.env.RAILWAY_VOLUME_MOUNT_PATH) c.push(path.join(process.env.RAILWAY_VOLUME_MOUNT_PATH, rel));
      c.push(path.join(process.cwd(), "public", rel));
      c.push(path.join("/tmp", rel));
      return c;
    };
    if (deletedExam.questionPaperUrl) toDelete.push(...baseCandidates(deletedExam.questionPaperUrl));
    if (deletedExam.answerKeyUrl) toDelete.push(...baseCandidates(deletedExam.answerKeyUrl));
    // also delete student sheets for this exam
    for (const s of subs) {
      if (s.examId === id && s.imageUrl) {
        toDelete.push(...baseCandidates(s.imageUrl));
      }
    }
    for (const p of toDelete) {
      try { if (fs.existsSync(p)) fs.unlinkSync(p); } catch {}
    }
  } catch {}

  return NextResponse.json({ success: true });
}

export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await req.json();
  const exams = readExams();
  const idx = exams.findIndex(e => e.id === id);
  if (idx === -1) return NextResponse.json({ error: "Not found" }, { status: 404 });

  // Allow updating answerKeyJson and status
  if (body.answerKeyJson) {
    exams[idx].answerKeyJson = body.answerKeyJson;
    exams[idx].status = "answer_key_ready";
  }
  if (body.status) exams[idx].status = body.status;

  // Allow updating sheetType
  if (body.sheetType && ["bubble","handwritten","auto"].includes(String(body.sheetType))) {
    exams[idx].sheetType = body.sheetType as any;
  }
  // Allow updating uncertainMarking (exam-wide)
  let uncertainMarkingChanged = false;
  if (body.uncertainMarking && ["zero","negative"].includes(String(body.uncertainMarking))) {
    exams[idx].uncertainMarking = body.uncertainMarking as any;
    uncertainMarkingChanged = true;
  }

  // Allow updating markingScheme (variable marking)
  let markingSchemeChanged = false;
  if (body.markingScheme) {
    const scheme = (body.markingScheme as any[]).map((s: any) => ({
      from: parseInt(String(s.from), 10),
      to: parseInt(String(s.to), 10),
      marks: parseFloat(String(s.marks)),
      negativeMarks: parseFloat(String(s.negativeMarks)),
    }));
    const err = validateMarkingScheme(scheme, exams[idx].questionCount);
    if (err) return NextResponse.json({ error: err }, { status: 400 });
    exams[idx].markingScheme = normalizeMarkingScheme(scheme);
    // keep legacy fields in sync
    exams[idx].marksPerQuestion = exams[idx].markingScheme[0].marks;
    exams[idx].negativeMarks = exams[idx].markingScheme[0].negativeMarks;
    markingSchemeChanged = true;
  }

  writeExams(exams);

  // If marking scheme or answer key or uncertainMarking changed, re-grade existing submissions that have been graded
  if ((markingSchemeChanged || body.answerKeyJson || uncertainMarkingChanged) && exams[idx].answerKeyJson) {
    const subs = readSubmissions();
    let any = false;
    const scheme = getMarkingScheme(exams[idx]);
    const uncertainMarking = (exams[idx] as any).uncertainMarking || "zero";
    for (const sub of subs) {
      if (sub.examId !== id) continue;
      if (!sub.extractedAnswers || !sub.details) continue;
      // Re-grade
      const grading = gradeSubmission(sub.extractedAnswers, exams[idx].answerKeyJson!, scheme, uncertainMarking);
      sub.score = grading.score;
      sub.correct = grading.correct;
      sub.incorrect = grading.incorrect;
      sub.blank = grading.blank;
      sub.details = grading.details;
      const uncertain = sub.uncertainQuestions?.length ? sub.uncertainQuestions.length > 0 : Object.values(sub.extractedAnswers).some(v => v === "UNCERTAIN" || v === "MULTIPLE");
      // Keep FAILED as is, otherwise update status based on uncertain
      if (sub.status !== "FAILED") {
        sub.status = uncertain ? "REVIEW_REQUIRED" : "COMPLETED";
      }
      sub.updatedAt = new Date().toISOString();
      any = true;
    }
    if (any) writeSubmissions(subs);
  }

  return NextResponse.json(exams[idx]);
}
