import { NextRequest, NextResponse } from "next/server";
import { readBatches, writeBatches, readSubjects, getBatch } from "@/lib/db";

export async function GET(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const batch = getBatch(id);
  if (!batch) return NextResponse.json({ error: "Batch not found" }, { status: 404 });
  return NextResponse.json(batch);
}

export async function PATCH(req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const body = await req.json();
  const batches = readBatches();
  const idx = batches.findIndex((b) => b.id === id);
  if (idx === -1) return NextResponse.json({ error: "Batch not found" }, { status: 404 });
  if (body.name !== undefined) batches[idx].name = String(body.name).trim() || batches[idx].name;
  if (body.academicYear !== undefined) batches[idx].academicYear = body.academicYear ? String(body.academicYear).trim() : null;
  if (body.description !== undefined) batches[idx].description = body.description ? String(body.description).trim() : null;
  writeBatches(batches);
  return NextResponse.json(batches[idx]);
}

export async function DELETE(_req: NextRequest, ctx: { params: Promise<{ id: string }> }) {
  const { id } = await ctx.params;
  const subjects = readSubjects().filter((s) => s.batchId === id);
  if (subjects.length > 0) return NextResponse.json({ error: "Cannot delete batch with subjects. Delete subjects first." }, { status: 400 });
  const batches = readBatches();
  const next = batches.filter((b) => b.id !== id);
  if (next.length === batches.length) return NextResponse.json({ error: "Batch not found" }, { status: 404 });
  writeBatches(next);
  return NextResponse.json({ ok: true });
}
