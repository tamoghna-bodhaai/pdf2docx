import { NextRequest, NextResponse } from "next/server";
import fs from "fs";
import path from "path";

// Serves /uploads/... from /tmp/uploads/... on Vercel (fallback to public/uploads for local)
// This makes student sheets / question papers viewable after refresh even though Vercel's FS is ephemeral
export async function GET(_req: NextRequest, { params }: { params: Promise<{ path: string[] }> }) {
  const { path: segments } = await params;
  if (!segments || segments.length === 0) return new NextResponse("Not found", { status: 404 });

  // Prevent path traversal
  const safe = segments.map((s) => path.basename(s)).join("/");
  // Original URL was /uploads/<safe> -> file is at /tmp/uploads/<safe> on Vercel, public/uploads/<safe> locally
  const candidates = [
    path.join("/tmp", "uploads", safe),
    path.join(process.cwd(), "public", "uploads", safe),
    path.join(process.cwd(), "uploads", safe),
  ];

  let filePath: string | null = null;
  for (const p of candidates) {
    try {
      if (fs.existsSync(p) && fs.statSync(p).isFile()) {
        filePath = p;
        break;
      }
    } catch {}
  }
  if (!filePath) return new NextResponse("Not found", { status: 404 });

  const ext = filePath.split(".").pop()?.toLowerCase() || "";
  const mime =
    ext === "png" ? "image/png" :
    ext === "jpg" || ext === "jpeg" ? "image/jpeg" :
    ext === "webp" ? "image/webp" :
    ext === "heic" ? "image/heic" :
    ext === "heif" ? "image/heif" :
    ext === "pdf" ? "application/pdf" :
    "application/octet-stream";

  const buffer = fs.readFileSync(filePath);
  return new NextResponse(buffer as any, {
    status: 200,
    headers: {
      "Content-Type": mime,
      "Content-Length": String(buffer.length),
      "Cache-Control": "public, max-age=31536000, immutable",
    },
  });
}
