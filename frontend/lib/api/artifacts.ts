import type { JobDto } from "./types";

export function visibleArtifacts(job: JobDto) {
  const pdfCount = job.artifacts.filter(item => item.media_type === "application/pdf").length;
  return job.artifacts.filter(item => job.kind !== "split_pdf" || item.media_type !== "application/zip" || pdfCount > 1);
}
