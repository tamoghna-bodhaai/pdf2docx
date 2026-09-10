import { NextRequest, NextResponse } from "next/server";
import { getSubject, getExamsBySubject, getSubmissionsBySubject, getStudentsByBatch } from "@/lib/db";

export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const subject = getSubject(id);
  if (!subject) return NextResponse.json({ error: "Subject not found" }, { status: 404 });
  const exams = getExamsBySubject(id);
  const subs = getSubmissionsBySubject(id).filter((s) => s.status === "COMPLETED" || s.status === "REVIEW_REQUIRED");
  const totalSubmissions = subs.length;
  const distinctStudents = new Set(subs.map((s) => s.studentId).filter(Boolean)).size;
  const avgScore = subs.length ? subs.reduce((a, s) => a + (s.score ?? 0), 0) / subs.length : null;

  // Top students by avg score across exams in this subject
  const perStudent: Record<string, { studentId:string; studentCode?:string|null; name?:string|null; scores:number[] }> = {};
  for (const s of subs) {
    if (!s.studentId) continue;
    if (!perStudent[s.studentId]) perStudent[s.studentId] = { studentId: s.studentId, studentCode: s.studentCode || null, name: s.studentName || null, scores: [] };
    perStudent[s.studentId].scores.push(s.score ?? 0);
  }
  const topStudents = Object.values(perStudent)
    .map((p) => ({ ...p, avgScore: p.scores.reduce((a,b)=>a+b,0)/p.scores.length, count: p.scores.length }))
    .sort((a,b)=> b.avgScore - a.avgScore)
    .slice(0,10);

  // Q-wise % correct
  const qWise: Record<string, number> = {};
  if (exams.length && subs.length) {
    // aggregate across subs: for each Q, count correct / total attempted
    const counts: Record<string, { correct:number; total:number }> = {};
    for (const s of subs) {
      if (!s.details) continue;
      for (const d of s.details) {
        const q = String(d.question);
        if (!counts[q]) counts[q] = { correct:0, total:0 };
        counts[q].total += 1;
        if (d.result === "Correct") counts[q].correct += 1;
      }
    }
    for (const [q, v] of Object.entries(counts)) qWise[q] = v.total ? (v.correct / v.total)*100 : 0;
  }

  return NextResponse.json({
    subject,
    examCount: exams.length,
    totalSubmissions,
    distinctStudents,
    avgScore,
    topStudents,
    qWise,
  });
}
