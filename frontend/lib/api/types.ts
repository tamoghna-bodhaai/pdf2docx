export type JobKind = "pdf_to_docx" | "images_to_pdf" | "split_pdf" | "merge_pdf";
export type JobStatus =
  | "ready" | "paused" | "queued" | "rendering" | "transcribing"
  | "building" | "processing" | "done" | "error" | "cancelled";

export interface PageRange { start: number; end: number }
export type PageOrientation = "auto" | "portrait" | "landscape";
export interface JobArtifact {
  key: string;
  filename: string;
  media_type: "application/pdf" | "application/zip";
  pages: number | null;
}

export interface MergeSource { id: string; filename: string; pages: number; size_bytes: number }

export interface JobDto {
  merge_sources?: MergeSource[];
  merge_dirty?: boolean;
  id: string;
  filename: string;
  kind: JobKind;
  source_filenames: string[];
  output_filename: string;
  output_pages: number;
  page_range: PageRange | null;
  page_orientation: PageOrientation;
  page_ranges: PageRange[];
  merge_ranges: boolean;
  artifacts: JobArtifact[];
  batch_id: string;
  pages: number;
  layout: string;
  requested_formats: string[];
  multi_column: boolean;
  diagnostics: Array<Record<string, unknown>>;
  status: JobStatus;
  done: number;
  total: number;
  error: string | null;
  size_bytes: number;
  cost: number;
  cost_known: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  has_docx: boolean;
  has_md: boolean;
  has_source: boolean;
  has_pdf: boolean;
  has_rebuilt: boolean;
  mathpix_formats: string[];
  has_detection: boolean;
  has_package: boolean;
}

export interface FormatDto {
  ext: string;
  media_type: string;
  note: string;
  requestable: boolean;
  always: boolean;
}

export interface ConfigDto {
  provider: string;
  mathpix_key_configured: boolean;
  mathpix_formats: FormatDto[];
  max_pages: number;
  max_upload_mb: number;
  batch_max_files: number;
  batch_workers: number;
  mathpix_page_rate: number;
  remote_delete: boolean;
  history_limit: number;
  local_tools_available: boolean;
  accepted_image_types: string[];
  merge_max_files?: number;
  merge_max_upload_mb?: number;
  image_max_files: number;
  image_max_pixels: number;
  image_max_upload_mb: number;
}

export interface HistoryDto { jobs: JobDto[]; total_cost: number; count: number }
export interface UserDto { id: string; email: string }
export interface AuthConfigDto { signup_open: boolean }

export interface BatchDto {
  batch_id: string;
  jobs: JobDto[];
  rejected: Array<{ filename: string; detail: string }>;
  package_ready: boolean;
  package_count: number;
}

export interface DetectionBlock {
  index: number;
  kind: string;
  bbox: [number, number, number, number];
  text?: string;
}
export interface DetectionPage {
  number: number;
  width: number;
  height: number;
  markdown: string;
  blocks: DetectionBlock[];
}
export interface DetectionDto { mode: string; pages: DetectionPage[] }
