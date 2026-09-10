import { NextRequest, NextResponse } from "next/server";
import { readStudents, writeStudents, getBatch, normalizeStudentName, resolveOrCreateStudent } from "@/lib/db";

export async function GET(req: NextRequest) {
  const batchId = req.nextUrl.searchParams.get("batchId");
  if (!batchId) return NextResponse.json({ error: "batchId required" }, { status: 400 });
  const students = readStudents().filter((s) => s.batchId === batchId).sort((a, b) => a.studentCode.localeCompare(b.studentCode));
  return NextResponse.json(students);
}

export async function POST(req: NextRequest) {
  try {
    const contentType = req.headers.get("content-type") || "";
    if (contentType.includes("multipart/form-data")) {
      const fd = await req.formData();
      const batchId = String(fd.get("batchId") || "").trim();
      const file = fd.get("file") as File | null;
      const nameSingle = String(fd.get("name") || "").trim();
      if (!batchId) return NextResponse.json({ error: "batchId required" }, { status: 400 });
      if (!getBatch(batchId)) return NextResponse.json({ error: "Batch not found" }, { status: 404 });

      if (nameSingle) {
        const s = resolveOrCreateStudent(batchId, nameSingle);
        return NextResponse.json(s, { status: 201 });
      }
      if (file && file.size > 0) {
        const text = await file.text();
        // headers: name[,studentCode] — only name matters; code auto-generated
        const lines = text.split(/\r?\n/).map((l) => l.trim()).filter(Boolean);
        // detect header
        let start = 0;
        if (lines[0]?.toLowerCase().includes("name")) start = 1;
        const created: any[] = [];
        for (let i = start; i < lines.length; i++) {
          const line = lines[i];
          if (!line) continue;
          // csv: split by comma not in quotes
          const cols = line.split(",").map((c) => c.trim().replace(/^"|"$/g, ""));
          const name = cols[0] || line;
          if (!name) continue;
          try {
            const s = resolveOrCreateStudent(batchId, name);
            created.push(s);
          } catch {}
        }
        return NextResponse.json({ created: created.length, students: created }, { status: 201 });
      }
      return NextResponse.json({ error: "Provide name or CSV file with name column" }, { status: 400 });
    }

    const body = await req.json();
    const batchId = String(body.batchId || "").trim();
    const name = String(body.name || "").trim();
    const csv = body.csv as string | undefined; // optional raw csv string
    if (!batchId) return NextResponse.json({ error: "batchId required" }, { status: 400 });
    if (!getBatch(batchId)) return NextResponse.json({ error: "Batch not found" }, { status: 404 });

    if (name) {
      const s = resolveOrCreateStudent(batchId, name);
      return NextResponse.json(s, { status: 201 });
    }
    if (csv) {
      const lines = csv.split(/\r?\n/).map((l: string) => l.trim()).filter(Boolean);
      let start = 0;
      if (lines[0]?.toLowerCase().includes("name")) start = 1;
      const created: any[] = [];
      for (let i = start; i < lines.length; i++) {
        const n = lines[i].split(",")[0]?.trim().replace(/^"|"$/g, "");
        if (!n) continue;
        created.push(resolveOrCreateStudent(batchId, n));
      }
      return NextResponse.json({ created: created.length, students: created }, { status: 201 });
    }
    return NextResponse.json({ error: "name or csv required" }, { status: 400 });
  } catch (e: any) {
    return NextResponse.json({ error: e.message || "Failed to create student" }, { status: 500 });
  }
}
