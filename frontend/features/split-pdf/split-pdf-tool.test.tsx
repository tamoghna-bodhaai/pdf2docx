import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api/client";
import { config, job } from "@/test/fixtures";
import { renderWithQuery } from "@/test/render";
import { SplitPdfTool, SplitRangeForm } from "./split-pdf-tool";

describe("split range", () => {
  it("retains values, reports the live count, and focuses the first invalid field", async () => {
    const user = userEvent.setup();
    renderWithQuery(<SplitRangeForm job={job()} />);
    const start = screen.getByLabelText("Start page");
    const end = screen.getByLabelText("End page");
    await user.clear(start); await user.type(start, "7");
    await user.clear(end); await user.type(end, "3");
    await user.click(screen.getByRole("button", { name: "Extract pages" }));

    expect(start).toHaveFocus();
    expect(start).toHaveValue("7");
    expect(end).toHaveValue("3");
    expect(screen.getByText("Start page must not come after end page.")).toBeInTheDocument();
  });

  it("submits an inclusive valid range", async () => {
    const split = vi.spyOn(api, "split").mockResolvedValue(job({ status: "done", output_pages: 3, page_range: { start: 2, end: 4 }, has_pdf: true }));
    const user = userEvent.setup();
    renderWithQuery(<SplitRangeForm job={job()} />);
    const start = screen.getByLabelText("Start page"); const end = screen.getByLabelText("End page");
    await user.clear(start); await user.type(start, "2"); await user.clear(end); await user.type(end, "4");
    expect(screen.getByText("Pages 2–4 · 3 pages")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Extract pages" }));
    expect(split).toHaveBeenCalledWith("job-1", 2, 4);
  });

  it("treats a zero upload limit as unlimited", async () => {
    const upload = vi.spyOn(api, "uploadSplitPdf").mockReturnValue(new Promise(() => undefined));
    const view = renderWithQuery(<SplitPdfTool config={{ ...config, max_upload_mb: 0 }} />);
    const picker = view.container.querySelector<HTMLInputElement>('input[type="file"]')!;
    fireEvent.change(picker, { target: { files: [new File(["pdf"], "large.pdf", { type: "application/pdf" })] } });
    await waitFor(() => expect(upload).toHaveBeenCalled());
    expect(screen.getByText(/no size limit/)).toBeInTheDocument();
  });
});
