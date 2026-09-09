import { Exam, MarkingSchemeSection } from "./types";

export function validateMarkingScheme(
  scheme: MarkingSchemeSection[],
  questionCount: number
): string | null {
  if (!Array.isArray(scheme) || scheme.length === 0) return "Marking scheme is empty";
  const sorted = [...scheme].sort((a, b) => a.from - b.from);
  if (sorted[0].from !== 1) return `Scheme must start at 1, got ${sorted[0].from}`;
  if (sorted[sorted.length - 1].to !== questionCount) return `Scheme must end at ${questionCount}, got ${sorted[sorted.length - 1].to}`;
  for (let i = 0; i < sorted.length; i++) {
    const s = sorted[i];
    if (!Number.isInteger(s.from) || !Number.isInteger(s.to)) return `From/to must be integers (section ${i + 1})`;
    if (s.from < 1 || s.to > questionCount) return `Section ${i + 1} out of bounds (1-${questionCount})`;
    if (s.from > s.to) return `Section ${i + 1}: from (${s.from}) > to (${s.to})`;
    if (typeof s.marks !== "number" || isNaN(s.marks) || s.marks < 0) return `Section ${i + 1}: marks must be >=0`;
    if (typeof s.negativeMarks !== "number" || isNaN(s.negativeMarks) || s.negativeMarks < 0) return `Section ${i + 1}: negativeMarks must be >=0`;
    if (i > 0) {
      const prev = sorted[i - 1];
      if (s.from !== prev.to + 1) {
        if (s.from <= prev.to) return `Sections overlap: ${prev.from}-${prev.to} and ${s.from}-${s.to}`;
        return `Gap between sections: ${prev.to + 1}..${s.from - 1} not covered`;
      }
    }
  }
  return null;
}

export function computeMaxMarks(scheme: MarkingSchemeSection[]): number {
  return scheme.reduce((sum, s) => sum + (s.to - s.from + 1) * s.marks, 0);
}

export function getMarksForQuestion(
  q: number,
  scheme: MarkingSchemeSection[]
): { marks: number; negativeMarks: number } {
  for (const s of scheme) {
    if (q >= s.from && q <= s.to) return { marks: s.marks, negativeMarks: s.negativeMarks };
  }
  // Should not happen if validated full coverage; fallback
  return { marks: 0, negativeMarks: 0 };
}

export function normalizeMarkingScheme(scheme: MarkingSchemeSection[]): MarkingSchemeSection[] {
  return [...scheme].sort((a, b) => a.from - b.from).map(s => ({
    from: s.from,
    to: s.to,
    marks: s.marks,
    negativeMarks: s.negativeMarks,
  }));
}

export function getMarkingScheme(exam: Exam): MarkingSchemeSection[] {
  if (Array.isArray((exam as any).markingScheme) && (exam as any).markingScheme.length > 0) {
    return normalizeMarkingScheme((exam as any).markingScheme as MarkingSchemeSection[]);
  }
  // Legacy fallback
  return [{ from: 1, to: exam.questionCount, marks: exam.marksPerQuestion, negativeMarks: exam.negativeMarks }];
}

export function legacyToScheme(exam: Pick<Exam, "questionCount" | "marksPerQuestion" | "negativeMarks">): MarkingSchemeSection[] {
  return [{ from: 1, to: exam.questionCount, marks: exam.marksPerQuestion, negativeMarks: exam.negativeMarks }];
}

export function formatSchemeSummary(scheme: MarkingSchemeSection[]): string {
  return scheme.map(s => `${s.from}-${s.to}: +${s.marks}/-${s.negativeMarks}`).join(" • ");
}
