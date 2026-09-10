import { NextRequest, NextResponse } from "next/server";
import { getBatch, getExamsByBatch, getSubmissionsByBatch, getSubjectsByBatch } from "@/lib/db";

export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const batch = getBatch(id);
  if (!batch) return NextResponse.json({ error: "Batch not found" }, { status: 404 });
  const exams = getExamsByBatch(id);
  const subjects = getSubjectsByBatch(id);
  const subs = getSubmissionsByBatch(id).filter((s) => s.status === "COMPLETED" || s.status === "REVIEW_REQUIRED");
  const totalSubmissions = subs.length;
  const avgScore = subs.length ? subs.reduce((a,s)=>a + (s.score??0),0)/subs.length : null;
  return NextResponse.json({
    batch,
    examCount: exams.length,
    subjectCount: subjects.length,
    totalSubmissions,
    avgScore,
    exams: exams.slice(0,10),
    subjects,
  });
}
