import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { JobDto, JobStatus } from "@/lib/api/types";
import { useNotifications } from "./use-notifications";

const shown: NotificationMock[] = [];
class NotificationMock {
  static permission: NotificationPermission = "granted";
  static requestPermission = vi.fn(async () => NotificationMock.permission);
  onclick: ((this: Notification, event: Event) => unknown) | null = null;
  close = vi.fn();
  body: string;
  constructor(_title: string, options?: NotificationOptions) {
    this.body = options?.body ?? "";
    shown.push(this);
  }
}

function job(status: JobStatus, values: Partial<JobDto> = {}): JobDto {
  return {
    id: "one", filename: "paper.pdf", kind: "pdf_to_docx", source_filenames: ["paper.pdf"],
    output_filename: "paper.docx", output_pages: 0, page_range: null, batch_id: "", pages: 2,
    layout: "mathpix", requested_formats: ["docx"], multi_column: false, diagnostics: [], status,
    done: 0, total: 2, error: null, size_bytes: 100, cost: 0, cost_known: false,
    created_at: "2026-09-04T00:00:00Z", started_at: null, finished_at: null,
    has_docx: false, has_md: false, has_source: true, has_pdf: false, has_rebuilt: false,
    mathpix_formats: [], has_detection: false, has_package: false, ...values,
  };
}

describe("completion notifications", () => {
  beforeEach(() => {
    shown.length = 0;
    NotificationMock.permission = "granted";
    NotificationMock.requestPermission.mockClear();
    Object.defineProperty(window, "Notification", { configurable: true, value: NotificationMock });
    Object.defineProperty(document, "hidden", { configurable: true, value: true });
    vi.spyOn(document, "hasFocus").mockReturnValue(false);
    localStorage.clear();
  });

  afterEach(() => vi.restoreAllMocks());

  it("seeds completed history quietly and reports one active-to-terminal transition", () => {
    const completed = job("done");
    const initial = renderHook(({ jobs }) => useNotifications(jobs), { initialProps: { jobs: [completed] } });
    expect(initial.result.current.notices).toEqual([]);
    initial.unmount();

    const current = renderHook(({ jobs }) => useNotifications(jobs), { initialProps: { jobs: [job("processing")] } });
    current.rerender({ jobs: [completed] });
    expect(current.result.current.notices.map((notice) => notice.message)).toEqual(["paper.docx is ready."]);
    current.rerender({ jobs: [completed] });
    expect(current.result.current.notices).toHaveLength(1);
  });

  it("deduplicates a finished batch and uses a background desktop notification", async () => {
    localStorage.setItem("pdf2docx-desktop-notifications", "enabled");
    const active = [job("processing", { id: "a", batch_id: "batch" }), job("queued", { id: "b", batch_id: "batch" })];
    const terminal = [job("done", { id: "a", batch_id: "batch" }), job("error", { id: "b", batch_id: "batch" })];
    const { result, rerender } = renderHook(({ jobs }) => useNotifications(jobs), { initialProps: { jobs: active } });
    await act(async () => rerender({ jobs: terminal }));
    expect(result.current.notices[0]?.message).toBe("Batch finished: 1 completed, 1 failed.");
    expect(shown.map((notice) => notice.body)).toEqual(["Batch finished: 1 completed, 1 failed."]);
    rerender({ jobs: terminal });
    expect(shown).toHaveLength(1);
  });

  it("requests permission and persists the control state", async () => {
    NotificationMock.permission = "default";
    NotificationMock.requestPermission.mockImplementation(async () => {
      NotificationMock.permission = "granted";
      return "granted";
    });
    const { result } = renderHook(() => useNotifications([]));
    await act(async () => result.current.toggle());
    expect(result.current.enabled).toBe(true);
    expect(localStorage.getItem("pdf2docx-desktop-notifications")).toBe("enabled");
  });
});
