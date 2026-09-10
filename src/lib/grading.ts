import { GradingDetail, MarkingSchemeSection, UncertainMarking } from "./types";
import { getMarksForQuestion } from "./markingScheme";

export function gradeSubmission(
  extractedAnswers: Record<string, string>,
  answerKey: Record<string, string>,
  marksPerQuestionOrScheme: number | MarkingSchemeSection[],
  negativeMarks?: number | UncertainMarking,
  uncertainMarkingArg?: UncertainMarking
): { score: number; correct: number; incorrect: number; blank: number; details: GradingDetail[] } {
  // Support 4th arg as UncertainMarking when 3rd is scheme (overload): gradeSubmission(answers, key, scheme, "zero")
  let uncertainMarking: UncertainMarking = "zero";
  if (typeof negativeMarks === "string" && ["zero", "negative"].includes(negativeMarks)) {
    uncertainMarking = negativeMarks as UncertainMarking;
    negativeMarks = undefined;
  } else if (uncertainMarkingArg && ["zero", "negative"].includes(uncertainMarkingArg)) {
    uncertainMarking = uncertainMarkingArg as UncertainMarking;
  } else if (typeof negativeMarks === "number") {
    // legacy call gradeSubmission(..., marks, neg) — keep default zero for uncertain
  }
  // Normalize to scheme
  let scheme: MarkingSchemeSection[];
  if (Array.isArray(marksPerQuestionOrScheme)) {
    scheme = marksPerQuestionOrScheme as MarkingSchemeSection[];
  } else {
    const marks = marksPerQuestionOrScheme as number;
    const neg = (typeof negativeMarks === "number" ? negativeMarks : 0) as number;
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
      incorrect++;
      marks = uncertainMarking === "negative" ? -neg : 0;
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
