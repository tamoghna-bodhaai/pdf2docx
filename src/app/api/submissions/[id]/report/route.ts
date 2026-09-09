import { NextRequest, NextResponse } from "next/server";
import { getSubmission, getExam } from "@/lib/db";
import { generateStudentReportPdf } from "@/lib/pdfReport";

export async function GET(_req: NextRequest, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const sub = getSubmission(id);
  if (!sub) return NextResponse.json({ error: "Not found" }, { status:404 });
  const exam = getExam(sub.examId);
  if (!exam) return NextResponse.json({ error: "Exam not found" }, { status:404 });
  if (!sub.details) return NextResponse.json({ error: "Submission not graded yet" }, { status:400 });

  const pdfBytes = await generateStudentReportPdf(exam, sub);
  return new NextResponse(Buffer.from(pdfBytes), {
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition": `attachment; filename="${(sub.studentName || "report").replace(/\s+/g,"_")}_${exam.name.replace(/\s+/g,"_")}.pdf"`,
    },
  });
}
