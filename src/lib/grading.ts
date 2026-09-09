import { GradingDetail, MarkingSchemeSection } from "./types";
import { getMarksForQuestion } from "./markingScheme";

export function gradeSubmission(
  extractedAnswers: Record<string, string>,
  answerKey: Record<string, string>,
  marksPerQuestionOrScheme: number | MarkingSchemeSection[],
  negativeMarks?: number
): { score: number; correct: number; incorrect: number; blank: number; details: GradingDetail[] } {
  // Normalize to scheme
  let scheme: MarkingSchemeSection[];
  if (Array.isArray(marksPerQuestionOrScheme)) {
    scheme = marksPerQuestionOrScheme as MarkingSchemeSection[];
  } else {
    const marks = marksPerQuestionOrScheme as number;
    const neg = negativeMarks ?? 0;
    const totalQuestions = Object.keys(answerKey).length;
    scheme = [{ from: 1, to: totalQuestions, marks, negativeMarks: neg }];
  }
  let correct = 0;
  let incorrect = 0;
  let blank = 0;
  let score = 0;
  const details: GradingDetail[] = [];

  const totalQuestions = Object.keys(answerKey).length;

  for (let i = 1; i <= totalQuestions; i++) {
    const q = String(i);
    const correctAns = answerKey[q];
    const studentAns = extractedAnswers[q] ?? "BLANK";

    let result: GradingDetail["result"];
    let marks = 0;
    const { marks: pos, negativeMarks: neg } = getMarksForQuestion(i, scheme);

    if (studentAns === "BLANK" || studentAns === "" || studentAns === null) {
      result = "Blank";
      blank++;
      marks = 0;
    } else if (studentAns === "UNCERTAIN" || studentAns === "MULTIPLE") {
      result = "Uncertain";
      // Count as incorrect for scoring but flagged
      incorrect++;
      marks = -neg;
    } else if (studentAns === correctAns) {
      result = "Correct";
      correct++;
      marks = pos;
    } else {
      result = "Incorrect";
      incorrect++;
      marks = -neg;
    }

    score += marks;

    details.push({
      question: i,
      studentAnswer: studentAns,
      correctAnswer: correctAns,
      result,
      marks,
    });
  }

  // Clamp score at 0 if needed? Don't clamp per PRD, just raw
  return { score, correct, incorrect, blank, details };
}
