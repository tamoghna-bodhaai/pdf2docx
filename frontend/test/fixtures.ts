import type { ConfigDto, JobDto } from "@/lib/api/types";

export function job(values: Partial<JobDto> = {}): JobDto {
  return {
    id: "job-1", filename: "report.pdf", kind: "split_pdf", source_filenames: ["report.pdf"],
    output_filename: "", output_pages: 0, page_range: null, page_orientation: "auto",
    page_ranges: [], merge_ranges: false, artifacts: [], batch_id: "", pages: 8,
    layout: "mathpix", requested_formats: [], multi_column: false, diagnostics: [], status: "ready",
    done: 0, total: 8, error: null, size_bytes: 1000, cost: 0, cost_known: false,
    created_at: "2026-01-01T00:00:00Z", started_at: null, finished_at: null,
    has_docx: false, has_md: false, has_source: true, has_pdf: false, has_rebuilt: false,
    mathpix_formats: [], has_detection: false, has_package: false, ...values,
  };
}

export const config: ConfigDto = {
  provider: "Mathpix", mathpix_key_configured: false, mathpix_formats: [], max_pages: 0,
  max_upload_mb: 50, batch_max_files: 10, batch_workers: 3, mathpix_page_rate: .0015,
  remote_delete: true, history_limit: 100, local_tools_available: true,
  accepted_image_types: ["image/jpeg", "image/png", "image/webp"], image_max_files: 30,
  image_max_pixels: 40_000_000, image_max_upload_mb: 50,
};
