import fs from "fs";
import path from "path";
import { Exam, Submission, Batch, Subject, Student } from "./types";

// Railway has persistent volume at /data if configured; Vercel uses /tmp. Default to cwd/data for local/dev.
const DATA_DIR = process.env.RAILWAY_VOLUME_MOUNT_PATH
  ? path.join(process.env.RAILWAY_VOLUME_MOUNT_PATH, "data")
  : process.env.VERCEL
    ? path.join("/tmp", "data")
    : path.join(process.cwd(), "data");
const EXAMS_FILE = path.join(DATA_DIR, "exams.json");
const SUBMISSIONS_FILE = path.join(DATA_DIR, "submissions.json");
const BATCHES_FILE = path.join(DATA_DIR, "batches.json");
const SUBJECTS_FILE = path.join(DATA_DIR, "subjects.json");
const STUDENTS_FILE = path.join(DATA_DIR, "students.json");

function ensureDataDir() {
  if (!fs.existsSync(DATA_DIR)) fs.mkdirSync(DATA_DIR, { recursive: true });
  if (!fs.existsSync(EXAMS_FILE)) fs.writeFileSync(EXAMS_FILE, JSON.stringify([], null, 2));
  if (!fs.existsSync(SUBMISSIONS_FILE)) fs.writeFileSync(SUBMISSIONS_FILE, JSON.stringify([], null, 2));
  if (!fs.existsSync(BATCHES_FILE)) fs.writeFileSync(BATCHES_FILE, JSON.stringify([], null, 2));
  if (!fs.existsSync(SUBJECTS_FILE)) fs.writeFileSync(SUBJECTS_FILE, JSON.stringify([], null, 2));
  if (!fs.existsSync(STUDENTS_FILE)) fs.writeFileSync(STUDENTS_FILE, JSON.stringify([], null, 2));
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
  if (!exam.uncertainMarking || !["zero", "negative"].includes(exam.uncertainMarking)) {
    exam.uncertainMarking = "zero";
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

// ——— Batches / Subjects / Students ———

export function readBatches(): Batch[] {
  ensureDataDir();
  try {
    return JSON.parse(fs.readFileSync(BATCHES_FILE, "utf-8"));
  } catch {
    return [];
  }
}
export function writeBatches(batches: Batch[]) {
  ensureDataDir();
  fs.writeFileSync(BATCHES_FILE, JSON.stringify(batches, null, 2));
}
export function getBatch(id: string): Batch | undefined {
  return readBatches().find((b) => b.id === id);
}

export function readSubjects(): Subject[] {
  ensureDataDir();
  try {
    return JSON.parse(fs.readFileSync(SUBJECTS_FILE, "utf-8"));
  } catch {
    return [];
  }
}
export function writeSubjects(subjects: Subject[]) {
  ensureDataDir();
  fs.writeFileSync(SUBJECTS_FILE, JSON.stringify(subjects, null, 2));
}
export function getSubject(id: string): Subject | undefined {
  return readSubjects().find((s) => s.id === id);
}
export function getSubjectsByBatch(batchId: string): Subject[] {
  return readSubjects().filter((s) => s.batchId === batchId).sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());
}

export function readStudents(): Student[] {
  ensureDataDir();
  try {
    return JSON.parse(fs.readFileSync(STUDENTS_FILE, "utf-8"));
  } catch {
    return [];
  }
}
export function writeStudents(students: Student[]) {
  ensureDataDir();
  fs.writeFileSync(STUDENTS_FILE, JSON.stringify(students, null, 2));
}
export function getStudent(id: string): Student | undefined {
  return readStudents().find((s) => s.id === id);
}
export function getStudentsByBatch(batchId: string): Student[] {
  return readStudents().filter((s) => s.batchId === batchId).sort((a, b) => a.studentCode.localeCompare(b.studentCode));
}
export function getExamsByBatch(batchId: string): Exam[] {
  return readExams().filter((e) => e.batchId === batchId).sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
}
export function getExamsBySubject(subjectId: string): Exam[] {
  return readExams().filter((e) => e.subjectId === subjectId).sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
}
export function getSubmissionsByBatch(batchId: string): Submission[] {
  const examIds = new Set(getExamsByBatch(batchId).map((e) => e.id));
  return readSubmissions().filter((s) => examIds.has(s.examId));
}
export function getSubmissionsBySubject(subjectId: string): Submission[] {
  const examIds = new Set(getExamsBySubject(subjectId).map((e) => e.id));
  return readSubmissions().filter((s) => examIds.has(s.examId));
}

export function normalizeStudentName(name: string): string {
  return name.trim().toLowerCase().replace(/\s+/g, " ");
}

export function resolveOrCreateStudent(batchId: string, rawName: string): Student {
  const name = rawName.trim();
  const nameNorm = normalizeStudentName(name);
  if (!nameNorm) throw new Error("Student name required");
  const students = readStudents();
  const existing = students.find((s) => s.batchId === batchId && s.nameNorm === nameNorm);
  if (existing) return existing;
  const count = students.filter((s) => s.batchId === batchId).length + 1;
  const studentCode = `STU-${String(count).padStart(3, "0")}`;
  const student: Student = {
    id: `${batchId}-${studentCode}-${Date.now().toString(36)}`,
    batchId,
    name,
    nameNorm,
    studentCode,
    createdAt: new Date().toISOString(),
  };
  students.push(student);
  writeStudents(students);
  return student;
}
