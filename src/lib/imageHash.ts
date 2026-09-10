import crypto from "crypto";

export function sha256(buffer: Buffer): string {
  return crypto.createHash("sha256").update(buffer).digest("hex");
}

export function shortHash(hash: string, len = 12): string {
  return hash.slice(0, len);
}

export function hashToSeed(hash: string): number {
  // Use first 8 hex chars → 32-bit int for LLM seed
  const slice = hash.slice(0, 8);
  const n = parseInt(slice, 16);
  return isNaN(n) ? 0 : n % 2147483647;
}
