import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api/client";
import { config, job } from "@/test/fixtures";
import { renderWithQuery } from "@/test/render";
import { PdfToDocxTool } from "./pdf-to-docx-tool";

const pdfConfig = {
  ...config,
  mathpix_key_configured: true,
  mathpix_formats: [{ ext: "docx", media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", note: "", requestable: true, always: false }],
};

describe("PDF conversion controls", () => {
  afterEach(() => vi.restoreAllMocks());

  it("starts an individual staged job with the selected format and layout", async () => {
    const staged = job({ kind: "pdf_to_docx", requested_formats: ["docx"] });
    const start = vi.spyOn(api, "startJob").mockResolvedValue({ ...staged, status: "queued" });
    const user = userEvent.setup();
    renderWithQuery(<PdfToDocxTool config={pdfConfig} restoredJob={staged} onOpen={vi.fn()} />);

    await user.click(screen.getByText("Match source page columns"));
    await user.click(screen.getByRole("button", { name: "Convert" }));

    await waitFor(() => expect(start).toHaveBeenCalledWith("job-1", ["docx"], true));
  });

  it("offers retry for a failed job whose source is retained", async () => {
    const failed = job({ kind: "pdf_to_docx", status: "error", error: "interrupted", has_source: true });
    vi.spyOn(api, "startJob").mockResolvedValue({ ...failed, status: "queued" });
    const user = userEvent.setup();
    renderWithQuery(<PdfToDocxTool config={pdfConfig} restoredJob={failed} onOpen={vi.fn()} />);

    expect(screen.getByText("interrupted")).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(api.startJob).toHaveBeenCalledWith("job-1", ["docx"], false);
  });

  it("rejects a mixed selection before starting an upload", () => {
    const upload = vi.spyOn(api, "uploadPdfBatch");
    const view = renderWithQuery(<PdfToDocxTool config={pdfConfig} onOpen={vi.fn()} />);
    const picker = view.container.querySelector<HTMLInputElement>('input[type="file"]')!;
    fireEvent.change(picker, { target: { files: [new File(["pdf"], "paper.pdf", { type: "application/pdf" }), new File(["text"], "notes.txt", { type: "text/plain" })] } });

    expect(screen.getByRole("alert")).toHaveTextContent("Choose PDF files only.");
    expect(upload).not.toHaveBeenCalled();
  });
  it("keeps all requestable formats in the disclosure and passes their selection unchanged", async () => {
    const staged = job({kind: "pdf_to_docx"});
    const start = vi.spyOn(api, "startJob").mockResolvedValue({...staged, status: "queued"});
    const user = userEvent.setup();
    renderWithQuery(<PdfToDocxTool config={{...pdfConfig, mathpix_formats: [...pdfConfig.mathpix_formats, {ext: "tex", media_type: "text/plain", note: "", requestable: true, always: false}]}} restoredJob={staged} />);
    await user.click(screen.getByText("Output formats · DOCX"));
    await user.click(screen.getByRole("checkbox", {name: "TEX"}));
    await user.click(screen.getByRole("button", {name: /^Convert$/}));
    expect(start).toHaveBeenCalledWith("job-1", ["docx", "tex"], false);
  });

  it("pauses, resumes and cancels a multi-file batch from the canvas", async () => {
    const staged = job({kind: "pdf_to_docx", batch_id: "batch-1"});
    const second = {...staged, id: "job-2", filename: "second.pdf"};
    const response = (status: "ready" | "paused" | "cancelled") => ({batch_id: "batch-1", jobs: [{...staged, status}, {...second, status}], rejected: [], package_ready: false, package_count: 0});
    vi.spyOn(api, "batch").mockResolvedValue(response("ready"));
    const action = vi.spyOn(api, "batchAction").mockImplementation(async (_, name) => response(name === "pause" ? "paused" : name === "cancel" ? "cancelled" : "ready"));
    const user = userEvent.setup();
    renderWithQuery(<PdfToDocxTool config={pdfConfig} restoredJob={staged} />);
    await user.click(await screen.findByRole("button", {name: "Pause all"}));
    await user.click(await screen.findByRole("button", {name: "Resume all"}));
    await user.click(await screen.findByRole("button", {name: "Cancel all"}));
    expect(action.mock.calls.map(call => call[1])).toEqual(["pause", "resume", "cancel"]);
    await waitFor(() => expect(screen.queryByText("8 pages · cancelled")).not.toBeInTheDocument());
  });

  it("treats a single batch member as a file and keeps only file controls", async () => {
    const staged = job({kind: "pdf_to_docx", batch_id: "batch-1", status: "paused"});
    vi.spyOn(api, "batch").mockResolvedValue({batch_id: "batch-1", jobs: [staged], rejected: [], package_ready: false, package_count: 0});
    const resume = vi.spyOn(api, "jobAction").mockResolvedValue({...staged, status: "queued"});
    renderWithQuery(<PdfToDocxTool config={pdfConfig} restoredJob={staged} onOpen={vi.fn()} />);

    expect(await screen.findByRole("region", {name: "File conversion"})).toBeVisible();
    expect(screen.queryByRole("button", {name: /all/i})).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", {name: "Resume"}));
    expect(resume).toHaveBeenCalledWith("job-1", "resume", ["docx"], false);
  });

});


it("tracks a previous single file while uploading B and retains its individual download", async () => {
  const a = job({id: "a", filename: "first.pdf", kind: "pdf_to_docx", batch_id: "a-batch", status: "paused", requested_formats: ["tex"], multi_column: true});
  const b = job({id: "b", filename: "second.pdf", kind: "pdf_to_docx", batch_id: "b-batch"});
  const data = (item: typeof a) => ({batch_id: item.batch_id, jobs: [item], rejected: [], package_ready: false, package_count: 0});
  vi.spyOn(api, "batch").mockResolvedValue(data(b));
  vi.spyOn(api, "uploadPdfBatch").mockResolvedValue(data(b));
  const action = vi.spyOn(api, "jobAction").mockResolvedValue({...a, status: "queued"});
  const props = {config: pdfConfig, jobs: [a], onOpen: vi.fn()};
  const view = renderWithQuery(<PdfToDocxTool {...props} />);
  fireEvent.change(view.container.querySelector('input[type="file"]')!, {target: {files: [new File(["pdf"], "second.pdf", {type: "application/pdf"})]}});
  await screen.findAllByText("second.pdf");
  const previous = screen.getByRole("region", {name: "Previous conversions"});
  await userEvent.click(within(previous).getByRole("button", {name: "Resume"}));
  expect(action).toHaveBeenCalledWith("a", "resume", ["tex"], true);
  view.rerender(<PdfToDocxTool {...props} jobs={[{...a, status: "done", has_docx: true}, b]} />);
  expect(within(previous).getAllByText("first.pdf")).toHaveLength(2);
  expect(within(previous).getByText("DOCX")).toHaveAttribute("href", "/api/jobs/a/download?format=docx");
  await userEvent.click(screen.getByRole("button", {name: "New conversion"}));
  expect(screen.getByRole("button", {name: /Choose PDF/})).toBeInTheDocument();
  expect(screen.getAllByText("second.pdf")).toHaveLength(2);
});

it("restores all remaining batch members and saved format and column settings", async () => {
  const a = job({id: "a", filename: "a.pdf", kind: "pdf_to_docx", batch_id: "batch", requested_formats: ["tex"], multi_column: true});
  const b = {...a, id: "b", filename: "b.pdf"};
  vi.spyOn(api, "batch").mockResolvedValue({batch_id: "batch", jobs: [a, b], rejected: [], package_ready: false, package_count: 0});
  renderWithQuery(<PdfToDocxTool config={pdfConfig} restoredJob={a} jobs={[a, b]} />);
  await screen.findByText("b.pdf");
  expect(screen.getAllByText("a.pdf")).toHaveLength(1);
  expect(screen.getByText("Output formats · TEX")).toBeInTheDocument();
  expect(screen.getByRole("checkbox", {name: /Match source page columns/})).toBeChecked();
  expect(screen.queryByRole("region", {name: "Previous conversions"})).not.toBeInTheDocument();
});

it("leaves a failed cancellation visible and retryable, then removes the accepted job", async () => {
  const staged = job({kind: "pdf_to_docx", status: "queued"});
  vi.spyOn(api, "job").mockResolvedValue(staged);
  const cancel = vi.spyOn(api, "jobAction").mockRejectedValueOnce(new Error("offline")).mockResolvedValue({...staged, status: "cancelled"});
  const removed = vi.fn();
  renderWithQuery(<PdfToDocxTool config={pdfConfig} restoredJob={staged} onRemoved={removed} />);
  await userEvent.click(screen.getByRole("button", {name: "Cancel"}));
  expect(await screen.findByRole("alert")).toHaveTextContent("That action failed.");
  expect(screen.getByRole("button", {name: "Cancel"})).toBeEnabled();
  await userEvent.click(screen.getByRole("button", {name: "Cancel"}));
  await waitFor(() => expect(removed).toHaveBeenCalledWith([staged.id]));
  expect(cancel).toHaveBeenCalledTimes(2);
  expect(screen.queryByText("8 pages · queued")).not.toBeInTheDocument();
});


it("shows provider waiting without claiming OCR progress", () => {
  const waiting = job({kind: "pdf_to_docx", status: "processing", done: 0});
  vi.spyOn(api, "job").mockResolvedValue(waiting);
  renderWithQuery(<PdfToDocxTool config={pdfConfig} restoredJob={waiting} />);
  expect(screen.getByText("Waiting for conversion service")).toBeVisible();
  expect(screen.queryByText("transcribing")).not.toBeInTheDocument();
});
