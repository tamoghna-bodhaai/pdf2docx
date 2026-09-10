import { NextRequest, NextResponse } from "next/server";
import { v4 as uuidv4 } from "uuid";
import { readBatches, writeBatches } from "@/lib/db";

export async function GET() {
  const batches = readBatches().sort((a, b) => new Date(b.createdAt).getTime() - new Date(a.createdAt).getTime());
  return NextResponse.json(batches);
}

export async function POST(req: NextRequest) {
  try {
    const body = await req.json().catch(async () => {
      const fd = await req.formData();
      return { name: String(fd.get("name") || ""), academicYear: String(fd.get("academicYear") || ""), description: String(fd.get("description") || "") };
    });
    // also support formData
    let name = "";
    let academicYear: string | null = null;
    let description: string | null = null;
    if (body instanceof FormData) {
      name = String(body.get("name") || "").trim();
      academicYear = String(body.get("academicYear") || "").trim() || null;
      description = String(body.get("description") || "").trim() || null;
    } else {
      name = String(body.name || "").trim();
      academicYear = body.academicYear ? String(body.academicYear).trim() : null;
      description = body.description ? String(body.description).trim() : null;
    }
    // fallback for formData parsed as object above already handled
    if (!name) return NextResponse.json({ error: "Batch name required" }, { status: 400 });
    const batches = readBatches();
    const batch = { id: uuidv4(), name, academicYear, description, createdAt: new Date().toISOString() };
    batches.push(batch);
    writeBatches(batches);
    return NextResponse.json(batch, { status: 201 });
  } catch (e: any) {
    return NextResponse.json({ error: e.message || "Failed to create batch" }, { status: 500 });
  }
}
