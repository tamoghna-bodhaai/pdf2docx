export type ExamStatus = "draft" | "answer_key_ready" | "scanning" | "completed";
export type SubmissionStatus = "UPLOADED" | "PROCESSING" | "COMPLETED" | "REVIEW_REQUIRED" | "FAILED";
export type SheetType = "bubble" | "handwritten" | "auto";

export interface MarkingSchemeSection {
  from: number;
  to: number;
  marks: number;
  negativeMarks: number;
}

export interface Batch {
  id: string;
  name: string;
  academicYear?: string | null;
  description?: string | null;
  createdAt: string;
}

export interface Subject {
  id: string;
  batchId: string;
  name: string;
  code?: string | null;
  createdAt: string;
}

export interface Student {
  id: string;
  batchId: string;
  name: string;
  nameNorm: string;
  studentCode: string; // STU-001 per batch, system-generated
  createdAt: string;
}

export type UncertainMarking = "zero" | "negative";

export interface Exam {
  id: string;
  name: string;
  subject: string;
  batchId?: string | null;
  subjectId?: string | null;
  questionCount: number;
  marksPerQuestion: number; // deprecated: kept for backwards compat, use markingScheme
  negativeMarks: number; // deprecated
  markingScheme: MarkingSchemeSection[];
  sheetType: SheetType; // "bubble" (OMR), "handwritten" (1.a 2.b list), "auto" (detect)
  uncertainMarking?: UncertainMarking; // how to score UNCERTAIN/MULTIPLE: "zero" (0) default or "negative" (-neg)
  questionPaperUrl: string | null;
  questionPaperName: string | null;
  answerKeyUrl: string | null;
  answerKeyName: string | null;
  answerKeyJson: Record<string, string> | null; // "1":"B"
  createdAt: string;
  status: ExamStatus;
}

export interface Submission {
  id: string;
  examId: string;
  imageUrl: string;
  originalName: string;
  studentName: string | null;
  rollNumber: string | null; // deprecated, kept for compat
  studentId?: string | null;
  studentCode?: string | null;
  extractedAnswers: Record<string, string> | null;
  uncertainQuestions: string[];
  sheetType?: SheetType; // how this sheet was interpreted; defaults to exam sheetType
  imageHash?: string | null; // SHA256 of original upload for dedup/cache
  visionMeta?: VisionMeta | null;
  status: SubmissionStatus;
  score: number | null;
  correct: number | null;
  incorrect: number | null;
  blank: number | null;
  details: GradingDetail[] | null;
  error?: string;
  createdAt: string;
  updatedAt: string;
}

export interface GradingDetail {
  question: number;
  studentAnswer: string;
  correctAnswer: string;
  result: "Correct" | "Incorrect" | "Blank" | "Uncertain";
  marks: number;
}

export interface VisionMeta {
  model: string;
  imageHash: string;
  latencyMs: number;
  confidences?: Record<string, number> | null;
  detectedSheetType?: SheetType | null;
  retryCount?: number;
  cached?: boolean;
}

export interface VisionExtractionResult {
  student_name: string | null;
  roll_number: string | null;
  answers: Record<string, string>;
  uncertain_questions: string[];
  confidences?: Record<string, number>;
  detectedSheetType?: SheetType | null;
  visionMeta?: VisionMeta | null;
}
