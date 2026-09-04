import { fireEvent, screen, waitFor } from "@testing-library/react";
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
});
