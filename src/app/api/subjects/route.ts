import { NextRequest, NextResponse } from "next/server";
import { v4 as uuidv4 } from "uuid";
import { readSubjects, writeSubjects, getBatch } from "@/lib/db";

export async function GET(req: NextRequest) {
  const batchId = req.nextUrl.searchParams.get("batchId");
  const subjects = readSubjects().sort((a, b) => new Date(a.createdAt).getTime() - new Date(b.createdAt).getTime());
  if (batchId) return NextResponse.json(subjects.filter((s) => s.batchId === batchId));
  return NextResponse.json(subjects);
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json();
    const batchId = String(body.batchId || "").trim();
    const name = String(body.name || "").trim();
    const code = body.code ? String(body.code).trim() : null;
    if (!batchId || !name) return NextResponse.json({ error: "batchId and name required" }, { status: 400 });
    if (!getBatch(batchId)) return NextResponse.json({ error: "Batch not found" }, { status: 404 });
    const subjects = readSubjects();
    const exists = subjects.find((s) => s.batchId === batchId && s.name.toLowerCase() === name.toLowerCase());
    if (exists) return NextResponse.json({ error: "Subject already exists in this batch" }, { status: 400 });
    const subject = { id: uuidv4(), batchId, name, code, createdAt: new Date().toISOString() };
    subjects.push(subject);
    writeSubjects(subjects);
    return NextResponse.json(subject, { status: 201 });
  } catch (e: any) {
    return NextResponse.json({ error: e.message || "Failed to create subject" }, { status: 500 });
  }
}
