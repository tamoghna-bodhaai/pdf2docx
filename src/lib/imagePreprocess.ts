/**
 * Image preprocessing for OMR VLLM — normalize before sending to Vision LLM.
 * Uses sharp if available (Railway/Nixpacks has libvips). Falls back to no-op.
 * - HEIC handling is via sharp's heif support; if unavailable we return original.
 * - EXIF auto-rotate, resize longest edge 1600px, normalize contrast via `normalize()`.
 * - Returns JPEG buffer ready for base64.
 */

let sharp: any = null;
try {
  // Lazy require so build doesn't fail if sharp not installed yet
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  sharp = require("sharp");
} catch {
  sharp = null;
}

export interface PreprocessResult {
  buffer: Buffer;
  mime: string; // always image/jpeg after preprocess when sharp available
  didPreprocess: boolean;
  originalSize: number;
  processedSize: number;
}

export async function preprocessForVision(input: Buffer, originalMime?: string): Promise<PreprocessResult> {
  const originalSize = input.length;
  if (!sharp) {
    // No sharp — return as-is
    return { buffer: input, mime: originalMime || "image/jpeg", didPreprocess: false, originalSize, processedSize: originalSize };
  }
  try {
    let pipeline = sharp(input, { failOn: "none" }).rotate(); // EXIF auto-rotate

    const meta = await pipeline.metadata().catch(() => null);
    // Resize longest edge to 1600px to bound tokens/cost while preserving detail
    const width = meta?.width || 0;
    const height = meta?.height || 0;
    const longest = Math.max(width, height);
    if (longest > 1600) {
      if (width >= height) pipeline = pipeline.resize({ width: 1600, withoutEnlargement: true });
      else pipeline = pipeline.resize({ height: 1600, withoutEnlargement: true });
    } else if (longest === 0) {
      // No metadata — still cap
      pipeline = pipeline.resize({ width: 1600, height: 1600, fit: "inside", withoutEnlargement: true });
    }

    // Light enhancement: normalize contrast (stretches histogram) — cheap and helps faint bubbles
    pipeline = pipeline.normalize().jpeg({ quality: 88, mozjpeg: true });

    const out = await pipeline.toBuffer();
    return { buffer: out, mime: "image/jpeg", didPreprocess: true, originalSize, processedSize: out.length };
  } catch (e) {
    console.warn("[preprocess] sharp failed, falling back to original", (e as Error).message);
    return { buffer: input, mime: originalMime || "image/jpeg", didPreprocess: false, originalSize, processedSize: originalSize };
  }
}

export function getUploadDir(): string {
  const path = require("path");
  if (process.env.RAILWAY_VOLUME_MOUNT_PATH) {
    return path.join(process.env.RAILWAY_VOLUME_MOUNT_PATH, "uploads", "student-sheets");
  }
  if (process.env.VERCEL) return path.join("/tmp", "uploads", "student-sheets");
  return path.join(process.cwd(), "public", "uploads", "student-sheets");
}

export function getDataDir(): string {
  const path = require("path");
  if (process.env.RAILWAY_VOLUME_MOUNT_PATH) return path.join(process.env.RAILWAY_VOLUME_MOUNT_PATH, "data");
  if (process.env.VERCEL) return path.join("/tmp", "data");
  return path.join(process.cwd(), "data");
}
