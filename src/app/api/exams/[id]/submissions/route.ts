import { NextRequest, NextResponse } from "next/server";
import { v4 as uuidv4 } from "uuid";
import path from "path";
import fs from "fs";
import { getExam, readSubmissions, writeSubmissions, getSubmissionsByExam, readExams, writeExams } from "@/lib/db";
import { extractAnswerSheet } from "@/lib/visionExtractor";
import { gradeSubmission } from "@/lib/grading";
import { getMarkingScheme } from "@/lib/markingScheme";

const MAX_SHEETS_PER_EXAM = 100;

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const exam = getExam(id);
  if (!exam) return NextResponse.json({ error: "Exam not found" }, { status: 404 });
  const subs = getSubmissionsByExam(id);
  return NextResponse.json(subs);
}

export async function POST(req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id: examId } = await params;
  const exam = getExam(examId);
  if (!exam) return NextResponse.json({ error: "Exam not found" }, { status: 404 });
  if (!exam.answerKeyJson) return NextResponse.json({ error: "Answer key not ready" }, { status: 400 });

  const subs = getSubmissionsByExam(examId);
  if (subs.length >= MAX_SHEETS_PER_EXAM) {
    return NextResponse.json({ error: `Maximum ${MAX_SHEETS_PER_EXAM} sheets reached for this exam` }, { status: 400 });
  }

  const formData = await req.formData();
  // Collect all files: support "file", "files", "file[]" and multiple entries
  const rawFiles: File[] = [];
  for (const key of ["file", "files", "file[]"]) {
    const vals = formData.getAll(key);
    for (const v of vals) if (v instanceof File && v.size > 0) rawFiles.push(v as File);
  }
  // Fallback: any File entry not yet captured (e.g. client appends with different key)
  if (rawFiles.length === 0) {
    for (const [, v] of formData.entries()) if (v instanceof File && (v as File).size > 0 && !rawFiles.includes(v as File)) rawFiles.push(v as File);
  }

  if (rawFiles.length === 0) return NextResponse.json({ error: "No file uploaded" }, { status: 400 });

  if (subs.length + rawFiles.length > MAX_SHEETS_PER_EXAM) {
    return NextResponse.json({ error: `Only ${MAX_SHEETS_PER_EXAM - subs.length} slots remaining (you tried to upload ${rawFiles.length})` }, { status: 400 });
  }

  const dir = path.join(process.cwd(), "public", "uploads", "student-sheets");
  fs.mkdirSync(dir, { recursive: true });

  const now = new Date().toISOString();
  const created: any[] = [];

  for (const file of rawFiles) {
    const ext = file.name.split(".").pop()?.toLowerCase() || "jpg";
    const submissionId = uuidv4();
    const filename = `${submissionId}.${ext}`;
    const buffer = Buffer.from(await file.arrayBuffer());
    fs.writeFileSync(path.join(dir, filename), buffer);
    const imageUrl = `/uploads/student-sheets/${filename}`;

    const newSubmission = {
      id: submissionId,
      examId,
      imageUrl,
      originalName: file.name,
      studentName: null,
      rollNumber: null,
      extractedAnswers: null,
      uncertainQuestions: [] as string[],
      status: "UPLOADED" as const,
      score: null,
      correct: null,
      incorrect: null,
      blank: null,
      details: null,
      createdAt: now,
      updatedAt: now,
    };
    created.push(newSubmission);
  }

  const allSubs = readSubmissions();
  allSubs.push(...created);
  writeSubmissions(allSubs);

  // Update exam status to scanning
  const exams = readExams();
  const examIdx = exams.findIndex(e => e.id === examId);
  if (examIdx !== -1) { exams[examIdx].status = "scanning"; writeExams(exams); }

  // Fire background processing for each submission asynchronously
  for (const sub of created) {
    const delay = 1500 + Math.random() * 2000;
    setTimeout(async () => {
      try {
        let current = readSubmissions();
        let idx = current.findIndex(s => s.id === sub.id);
        if (idx === -1) return;
        current[idx].status = "PROCESSING";
        current[idx].updatedAt = new Date().toISOString();
        writeSubmissions(current);

        const result = await extractAnswerSheet(sub.imageUrl, exam.questionCount);

        // Re-fetch exam inside background task to get latest scheme (in case it changed after upload)
        const latestExam = getExam(examId) ?? exam;
        const scheme = getMarkingScheme(latestExam);
        const grading = gradeSubmission(result.answers, latestExam.answerKeyJson!, scheme);
        const hasUncertain = result.uncertain_questions.length > 0 || Object.values(result.answers).some(v => v === "UNCERTAIN" || v === "MULTIPLE");

        current = readSubmissions();
        idx = current.findIndex(s => s.id === sub.id);
        if (idx === -1) return;
        current[idx].studentName = result.student_name;
        current[idx].rollNumber = result.roll_number;
        current[idx].extractedAnswers = result.answers;
        current[idx].uncertainQuestions = result.uncertain_questions;
        current[idx].score = grading.score;
        current[idx].correct = grading.correct;
        current[idx].incorrect = grading.incorrect;
        current[idx].blank = grading.blank;
        current[idx].details = grading.details;
        current[idx].status = hasUncertain ? "REVIEW_REQUIRED" : "COMPLETED";
        current[idx].updatedAt = new Date().toISOString();
        writeSubmissions(current);
      } catch (e:any) {
        console.error("Processing failed for", sub.id, e);
        let current = readSubmissions();
        const idx = current.findIndex(s => s.id === sub.id);
        if (idx !== -1) {
          (current[idx] as any).status = "FAILED";
          (current[idx] as any).error = e.message || "Vision extraction failed";
          current[idx].updatedAt = new Date().toISOString();
          writeSubmissions(current);
        }
      }
    }, delay);
  }

  // For backwards compat: single file returns object, batch returns array
  if (created.length === 1) return NextResponse.json(created[0], { status: 201 });
  return NextResponse.json(created, { status: 201 });
}
