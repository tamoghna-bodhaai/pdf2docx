import { NextRequest, NextResponse } from "next/server";
import { readSubjects, writeSubjects, getSubject, readExams } from "@/lib/db";

export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const s = getSubject(id);
  if (!s) return NextResponse.json({ error: "Subject not found" }, { status: 404 });
  return NextResponse.json(s);
}

export async function PATCH(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body = await req.json();
  const subjects = readSubjects();
  const idx = subjects.findIndex((s) => s.id === id);
  if (idx === -1) return NextResponse.json({ error: "Subject not found" }, { status: 404 });
  if (body.name !== undefined) subjects[idx].name = String(body.name).trim() || subjects[idx].name;
  if (body.code !== undefined) subjects[idx].code = body.code ? String(body.code).trim() : null;
  writeSubjects(subjects);
  return NextResponse.json(subjects[idx]);
}

export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const exams = readExams().filter((e) => e.subjectId === id);
  if (exams.length > 0) return NextResponse.json({ error: "Cannot delete subject with exams. Move or delete exams first." }, { status: 400 });
  const subjects = readSubjects();
  const next = subjects.filter((s) => s.id !== id);
  if (next.length === subjects.length) return NextResponse.json({ error: "Subject not found" }, { status: 404 });
  writeSubjects(next);
  return NextResponse.json({ ok: true });
}
