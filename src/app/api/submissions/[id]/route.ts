import { NextRequest, NextResponse } from "next/server";
import { readSubmissions, writeSubmissions, getSubmission, getExam, readExams } from "@/lib/db";
import { gradeSubmission } from "@/lib/grading";
import { getMarkingScheme } from "@/lib/markingScheme";

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const sub = getSubmission(id);
  if (!sub) return NextResponse.json({ error: "Submission not found" }, { status: 404 });
  return NextResponse.json(sub);
}

export async function PATCH(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const body = await req.json();
  const subs = readSubmissions();
  const idx = subs.findIndex(s => s.id === id);
  if (idx === -1) return NextResponse.json({ error: "Not found" }, { status: 404 });

  const sub = subs[idx];
  const exam = getExam(sub.examId);
  if (!exam || !exam.answerKeyJson) return NextResponse.json({ error: "Exam or answer key missing" }, { status: 400 });

  // Allow editing studentName, rollNumber, extractedAnswers, sheetType
  if (body.studentName !== undefined) subs[idx].studentName = body.studentName || null;
  if (body.rollNumber !== undefined) subs[idx].rollNumber = body.rollNumber || null;
  if (body.sheetType && ["bubble","handwritten","auto"].includes(String(body.sheetType))) {
    (subs[idx] as any).sheetType = body.sheetType;
  }

  if (body.extractedAnswers) {
    // Validate
    const cleaned: Record<string,string> = {};
    for (const [k,v] of Object.entries(body.extractedAnswers)) {
      const val = String(v).toUpperCase();
      if (!["A","B","C","D","E","BLANK","MULTIPLE","UNCERTAIN"].includes(val)) {
        return NextResponse.json({ error: `Invalid answer ${val}` }, { status: 400 });
      }
      cleaned[String(parseInt(k,10))] = val;
    }
    // Ensure all questions present
    for (let i=1;i<=exam.questionCount;i++) {
      if (!cleaned[String(i)]) cleaned[String(i)] = "BLANK";
    }
    subs[idx].extractedAnswers = cleaned;

    // Re-grade (respect exam-wide uncertainMarking)
    const scheme = getMarkingScheme(exam);
    const uncertainMarking = (exam as any).uncertainMarking || "zero";
    const grading = gradeSubmission(cleaned, exam.answerKeyJson, scheme, uncertainMarking);
    subs[idx].score = grading.score;
    subs[idx].correct = grading.correct;
    subs[idx].incorrect = grading.incorrect;
    subs[idx].blank = grading.blank;
    subs[idx].details = grading.details;

    // Update uncertain
    const uncertain = Object.entries(cleaned).filter(([,v])=> v==="UNCERTAIN"||v==="MULTIPLE").map(([k])=>k);
    subs[idx].uncertainQuestions = uncertain;
    // If previously FAILED or REVIEW_REQUIRED and now edited, mark COMPLETED
    if (subs[idx].status === "FAILED" || subs[idx].status === "REVIEW_REQUIRED") {
      subs[idx].status = uncertain.length > 0 ? "REVIEW_REQUIRED" : "COMPLETED";
    } else if (uncertain.length > 0) {
      subs[idx].status = "REVIEW_REQUIRED";
    } else {
      subs[idx].status = "COMPLETED";
    }
  }

  // Retry logic: if status FAILED and body.retry true, reset to UPLOADED and trigger reprocessing?
  if (body.retry && subs[idx].status === "FAILED") {
    subs[idx].status = "UPLOADED";
    subs[idx].error = undefined;
    // Trigger async reprocess
    const { extractAnswerSheet } = await import("@/lib/visionExtractor");
    const submissionId = id;
    const retrySheetType = (subs[idx] as any).sheetType || (exam as any).sheetType || "bubble";
    setTimeout(async () => {
      try {
        let current = readSubmissions();
        let sIdx = current.findIndex(s=>s.id===submissionId);
        if (sIdx===-1) return;
        current[sIdx].status = "PROCESSING";
        current[sIdx].updatedAt = new Date().toISOString();
        writeSubmissions(current);
        const result = await extractAnswerSheet(current[sIdx].imageUrl, exam.questionCount, retrySheetType as any);
        const currentExamId = (current[sIdx] as any).examId ?? sub.examId;
        const latestExam = getExam(currentExamId) ?? exam;
        const scheme2 = getMarkingScheme(latestExam);
        const uncertainMarking2 = (latestExam as any).uncertainMarking || "zero";
        const grading = gradeSubmission(result.answers, latestExam.answerKeyJson!, scheme2, uncertainMarking2);
        current = readSubmissions();
        sIdx = current.findIndex(s=>s.id===submissionId);
        if (sIdx===-1) return;
        current[sIdx].studentName = result.student_name;
        current[sIdx].rollNumber = result.roll_number;
        current[sIdx].extractedAnswers = result.answers;
        current[sIdx].uncertainQuestions = result.uncertain_questions;
        (current[sIdx] as any).visionMeta = (result as any).visionMeta || null;
        current[sIdx].score = grading.score;
        current[sIdx].correct = grading.correct;
        current[sIdx].incorrect = grading.incorrect;
        current[sIdx].blank = grading.blank;
        current[sIdx].details = grading.details;
        current[sIdx].status = result.uncertain_questions.length > 0 ? "REVIEW_REQUIRED" : "COMPLETED";
        current[sIdx].updatedAt = new Date().toISOString();
        writeSubmissions(current);
      } catch (e:any) {
        let current = readSubmissions();
        const sIdx = current.findIndex(s=>s.id===submissionId);
        if (sIdx!==-1){ current[sIdx].status="FAILED"; current[sIdx].error=e.message; writeSubmissions(current);}
      }
    }, 1500);
  }

  subs[idx].updatedAt = new Date().toISOString();
  writeSubmissions(subs);
  return NextResponse.json(subs[idx]);
}

export async function DELETE(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const subs = readSubmissions();
  const subToDelete = subs.find(s => s.id === id);
  if (!subToDelete) return NextResponse.json({ error: "Not found" }, { status:404});
  const filtered = subs.filter(s => s.id !== id);
  writeSubmissions(filtered);
  // Free backend space: delete image file from disk — Railway volume aware
  try {
    const fs = await import("fs");
    const path = await import("path");
    if (subToDelete.imageUrl) {
      const rel = subToDelete.imageUrl.replace(/^\//, "");
      const candidates: string[] = [];
      if (process.env.RAILWAY_VOLUME_MOUNT_PATH) candidates.push(path.join(process.env.RAILWAY_VOLUME_MOUNT_PATH, rel));
      candidates.push(path.join(process.cwd(), "public", rel), path.join("/tmp", rel), path.join(process.cwd(), rel));
      for (const p of candidates) {
        try { if (fs.existsSync(p)) fs.unlinkSync(p); } catch {}
      }
    }
  } catch {}
  return NextResponse.json({ success:true });
}
