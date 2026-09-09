export type ExamStatus = "draft" | "answer_key_ready" | "scanning" | "completed";
export type SubmissionStatus = "UPLOADED" | "PROCESSING" | "COMPLETED" | "REVIEW_REQUIRED" | "FAILED";
export type SheetType = "bubble" | "handwritten" | "auto";

export interface MarkingSchemeSection {
  from: number;
  to: number;
  marks: number;
  negativeMarks: number;
}

export interface Exam {
  id: string;
  name: string;
  subject: string;
  questionCount: number;
  marksPerQuestion: number; // deprecated: kept for backwards compat, use markingScheme
  negativeMarks: number; // deprecated
  markingScheme: MarkingSchemeSection[];
  sheetType: SheetType; // "bubble" (OMR), "handwritten" (1.a 2.b list), "auto" (detect)
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
  rollNumber: string | null;
  extractedAnswers: Record<string, string> | null;
  uncertainQuestions: string[];
  sheetType?: SheetType; // how this sheet was interpreted; defaults to exam sheetType
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

export interface VisionExtractionResult {
  student_name: string | null;
  roll_number: string | null;
  answers: Record<string, string>;
  uncertain_questions: string[];
}
