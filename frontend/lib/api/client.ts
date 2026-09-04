import type {
  AuthConfigDto, BatchDto, ConfigDto, DetectionDto, HistoryDto, JobDto, JobKind,
  PageOrientation, PageRange, UserDto,
} from "./types";

/* eslint-disable @next/next/no-location-assign-relative-destination -- FastAPI owns same-origin authentication redirects. */

type ErrorBody = { detail?: unknown };

export class ApiError extends Error {
  constructor(message: string, public readonly status: number, public readonly details?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

function detailMessage(body: ErrorBody | null, fallback: string): string {
  if (typeof body?.detail === "string" && body.detail.trim()) return body.detail;
  if (Array.isArray(body?.detail)) {
    const messages = body.detail.flatMap((item) => {
      if (typeof item === "string") return [item];
      if (item && typeof item === "object" && "msg" in item && typeof item.msg === "string") {
        return [item.msg];
      }
      return [];
    });
    if (messages.length) return messages.join(" · ");
  }
  return fallback;
}

async function parseError(response: Response): Promise<ApiError> {
  const body = await response.json().catch(() => null) as ErrorBody | null;
  return new ApiError(detailMessage(body, `Request failed (${response.status}).`), response.status, body?.detail);
}

async function request<T>(path: string, init: RequestInit = {}, redirectOnUnauthorized = true): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, { credentials: "same-origin", ...init });
  } catch (cause) {
    throw new ApiError(cause instanceof Error ? cause.message : "Network request failed.", 0);
  }
  if (response.status === 401 && redirectOnUnauthorized && typeof window !== "undefined") {
    window.location.href = "/login";
  }
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

function form(values: Record<string, string | number | boolean | undefined>): FormData {
  const data = new FormData();
  for (const [key, value] of Object.entries(values)) {
    if (value !== undefined) data.append(key, String(value));
  }
  return data;
}

export function upload<T>(
  path: string,
  body: FormData,
  onProgress?: (percent: number) => void,
): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.withCredentials = true;
    xhr.responseType = "json";
    xhr.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress?.(Math.round(event.loaded / event.total * 100));
    });
    xhr.addEventListener("load", () => {
      if (xhr.status === 401) window.location.href = "/login";
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.response as T);
      else reject(new ApiError(detailMessage(xhr.response as ErrorBody | null, `Upload failed (${xhr.status}).`), xhr.status));
    });
    xhr.addEventListener("error", () => reject(new ApiError("Upload failed. Check your connection and try again.", 0)));
    xhr.addEventListener("abort", () => reject(new ApiError("Upload cancelled.", 0)));
    xhr.send(body);
  });
}

export const api = {
  authConfig: () => request<AuthConfigDto>("/api/auth/config", {}, false),
  me: () => request<UserDto>("/api/auth/me"),
  login: (email: string, password: string) => request<UserDto>(
    "/api/auth/login", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, password }) }, false,
  ),
  signup: (email: string, password: string, inviteCode: string) => request<UserDto>(
    "/api/auth/signup", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email, password, invite_code: inviteCode }) }, false,
  ),
  logout: () => request<{ signed_out: boolean }>("/api/auth/logout", { method: "POST" }),
  config: () => request<ConfigDto>("/api/config"),
  history: () => request<HistoryDto>("/api/history"),
  clearHistory: (kind?: JobKind) => request<{ deleted: number }>(
    `/api/history${kind ? `?kind=${encodeURIComponent(kind)}` : ""}`, { method: "DELETE" },
  ),
  job: (id: string) => request<JobDto>(`/api/jobs/${encodeURIComponent(id)}`),
  deleteJob: (id: string) => request<{ deleted: string }>(`/api/jobs/${encodeURIComponent(id)}`, { method: "DELETE" }),
  markdown: (id: string) => request<{ markdown: string }>(`/api/jobs/${encodeURIComponent(id)}/markdown`),
  detection: (id: string) => request<DetectionDto>(`/api/jobs/${encodeURIComponent(id)}/detection`),
  uploadPdfBatch: (files: File[], onProgress?: (percent: number) => void) => {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    return upload<BatchDto>("/api/convert/batch", body, onProgress);
  },
  uploadImages: (files: File[], orientation: PageOrientation = "auto", onProgress?: (percent: number) => void) => {
    const body = new FormData();
    files.forEach((file) => body.append("files", file));
    body.append("orientation", orientation);
    return upload<JobDto>("/api/tools/images-to-pdf", body, onProgress);
  },
  uploadSplitPdf: (file: File, onProgress?: (percent: number) => void) => {
    const body = new FormData();
    body.append("file", file);
    return upload<JobDto>("/api/tools/split-pdf", body, onProgress);
  },
  split: (id: string, ranges: PageRange[], merge: boolean) => request<JobDto>(
    `/api/jobs/${encodeURIComponent(id)}/split`, { method: "POST", body: form({ ranges: JSON.stringify(ranges), merge }) },
  ),
  startJob: (id: string, formats: string[], multiColumn: boolean) => request<JobDto>(
    `/api/jobs/${encodeURIComponent(id)}/start`, { method: "POST", body: form({ formats: formats.join(","), multi_column: multiColumn }) },
  ),
  jobAction: (id: string, action: "pause" | "resume" | "cancel") => request<JobDto>(
    `/api/jobs/${encodeURIComponent(id)}/${action}`, { method: "POST" },
  ),
  batchAction: (id: string, action: "start" | "pause" | "resume" | "cancel", formats: string[] = [], multiColumn = false) => request<BatchDto>(
    `/api/batches/${encodeURIComponent(id)}/${action}`,
    { method: "POST", body: action === "start" || action === "resume" ? form({ formats: formats.join(","), multi_column: multiColumn }) : undefined },
  ),
  batch: (id: string) => request<BatchDto>(`/api/batches/${encodeURIComponent(id)}`),
  downloadUrl: (id: string, format: string) => `/api/jobs/${encodeURIComponent(id)}/download?format=${encodeURIComponent(format)}`,
  pageUrl: (id: string, page: number, width?: number) => `/api/jobs/${encodeURIComponent(id)}/page/${page}.png${width ? `?width=${width}` : ""}`,
  artifactUrl: (id: string, key: string) => `/api/jobs/${encodeURIComponent(id)}/artifacts/${encodeURIComponent(key)}`,
  assetUrl: (id: string, asset: string) => `/api/jobs/${encodeURIComponent(id)}/asset/${asset.split("/").map(encodeURIComponent).join("/")}`,
  packageUrl: (id: string) => `/api/jobs/${encodeURIComponent(id)}/package.zip`,
  batchPackageUrl: (id: string) => `/api/batches/${encodeURIComponent(id)}/package.zip`,
};
