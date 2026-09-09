import fs from "fs";
import path from "path";
import { Exam, Submission } from "./types";

const DATA_DIR = path.join(process.cwd(), "data");
const EXAMS_FILE = path.join(DATA_DIR, "exams.json");
const SUBMISSIONS_FILE = path.join(DATA_DIR, "submissions.json");

function ensureDataDir() {
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
  if (!fs.existsSync(EXAMS_FILE)) fs.writeFileSync(EXAMS_FILE, JSON.stringify([], null, 2));
  if (!fs.existsSync(SUBMISSIONS_FILE)) fs.writeFileSync(SUBMISSIONS_FILE, JSON.stringify([], null, 2));
}

function migrateExam(exam: any): Exam {
  if (!exam.markingScheme || !Array.isArray(exam.markingScheme) || exam.markingScheme.length === 0) {
    // legacy fallback
    exam.markingScheme = [{ from: 1, to: exam.questionCount, marks: exam.marksPerQuestion ?? 1, negativeMarks: exam.negativeMarks ?? 0 }];
  }
  if (!exam.sheetType || !["bubble", "handwritten", "auto"].includes(exam.sheetType)) {
    // legacy exams were bubble-only; keep behavior stable
    exam.sheetType = "bubble";
  }
  // ensure deprecated fields stay in sync for backwards compat display
  // keep them as first section values if uniform, otherwise use first? keep legacy as computed avg not needed; sync not required
  return exam as Exam;
}

export function readExams(): Exam[] {
  ensureDataDir();
  try {
    const raw: any[] = JSON.parse(fs.readFileSync(EXAMS_FILE, "utf-8"));
    let mutated = false;
    const migrated = raw.map((e) => {
      const before = JSON.stringify(e.markingScheme);
      const m = migrateExam(e);
      if (JSON.stringify(m.markingScheme) !== before) mutated = true;
      return m;
    });
    if (mutated) {
      try { fs.writeFileSync(EXAMS_FILE, JSON.stringify(migrated, null, 2)); } catch {}
    }
    return migrated;
  } catch {
    return [];
  }
}

export function writeExams(exams: Exam[]) {
  ensureDataDir();
  fs.writeFileSync(EXAMS_FILE, JSON.stringify(exams, null, 2));
}

export function readSubmissions(): Submission[] {
  ensureDataDir();
  try {
    return JSON.parse(fs.readFileSync(SUBMISSIONS_FILE, "utf-8"));
  } catch {
    return [];
  }
}

export function writeSubmissions(subs: Submission[]) {
  ensureDataDir();
  fs.writeFileSync(SUBMISSIONS_FILE, JSON.stringify(subs, null, 2));
}

export function getExam(id: string): Exam | undefined {
  return readExams().find((e) => e.id === id);
}

export function migrateExamIfNeeded(exam: Exam): Exam {
  return migrateExam(exam as any);
}

export function getSubmissionsByExam(examId: string): Submission[] {
  return readSubmissions().filter((s) => s.examId === examId).sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());
}

export function getSubmission(id: string): Submission | undefined {
  return readSubmissions().find((s) => s.id === id);
}
