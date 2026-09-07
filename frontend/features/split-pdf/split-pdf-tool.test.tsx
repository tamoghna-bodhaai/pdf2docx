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
    await user.click(screen.getByRole("button", { name: "Split PDF" }));

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
    await user.click(screen.getByRole("button", { name: "Split PDF" }));
    expect(split).toHaveBeenCalledWith("job-1", [{ start: 2, end: 4 }], false);
  });

  it("warns about overlaps and submits reordered ranges in merged mode", async () => {
    const split = vi.spyOn(api, "split").mockResolvedValue(job({ status: "done", has_pdf: true }));
    const user = userEvent.setup();
    renderWithQuery(<SplitRangeForm job={job({ pages: 5 })} />);
    await user.click(screen.getByRole("button", { name: "Add range" }));
    expect(screen.getByText(/selected more than once/)).toBeInTheDocument();
    await user.clear(screen.getByLabelText("Range 2 start page"));
    await user.type(screen.getByLabelText("Range 2 start page"), "3");
    await user.clear(screen.getByLabelText("Range 2 end page"));
    await user.type(screen.getByLabelText("Range 2 end page"), "4");
    await user.type(screen.getByRole("button", { name: /Reorder range 2/ }), "{ArrowUp}");
    await user.click(screen.getByRole("checkbox", { name: /Merge ranges into one PDF/ }));
    await user.click(screen.getByRole("button", { name: "Split PDF" }));
    expect(split).toHaveBeenCalledWith("job-1", [{ start: 3, end: 4 }, { start: 1, end: 5 }], true);
  });

  it("treats a zero upload limit as unlimited", async () => {
    const upload = vi.spyOn(api, "uploadSplitPdf").mockReturnValue(new Promise(() => undefined));
    const view = renderWithQuery(<SplitPdfTool config={{ ...config, max_upload_mb: 0 }} />);
    const picker = view.container.querySelector<HTMLInputElement>('input[type="file"]')!;
    fireEvent.change(picker, { target: { files: [new File(["pdf"], "large.pdf", { type: "application/pdf" })] } });
    await waitFor(() => expect(upload).toHaveBeenCalled());
    expect(screen.getByText(/no size limit/)).toBeInTheDocument();
  });

  it("keeps merge and extraction controls in Setup and cancels an unfinished drag", async () => {
    renderWithQuery(<SplitPdfTool config={config} restoredJob={job({ pages: 5 })} />);
    const setup = screen.getByRole("complementary", { name: "Tool settings" });
    expect(screen.queryByRole("checkbox", { name: /Merge ranges into one PDF/ })).not.toBeInTheDocument();
    expect(setup).toContainElement(screen.getByRole("button", { name: "Split PDF" }));

    await userEvent.click(screen.getByRole("button", { name: "Add range" }));
    await userEvent.clear(screen.getByLabelText("Range 2 start page"));
    await userEvent.type(screen.getByLabelText("Range 2 start page"), "2");
    await userEvent.clear(screen.getByLabelText("Range 2 end page"));
    await userEvent.type(screen.getByLabelText("Range 2 end page"), "3");
    const ranges = screen.getAllByRole("listitem").slice(-2);
    Object.defineProperty(document, "elementFromPoint", { configurable: true, value: vi.fn(() => ranges[1]) });
    const firstHandle = screen.getByRole("button", { name: /Reorder range 1/ });
    fireEvent.pointerDown(firstHandle, { pointerId: 1 });
    fireEvent.pointerMove(ranges[0].parentElement!, { pointerId: 1, clientX: 10, clientY: 10 });
    expect(screen.getAllByRole("textbox")[0]).toHaveValue("2");
    fireEvent.pointerCancel(firstHandle, { pointerId: 1 });
    expect(screen.getAllByRole("textbox")[0]).toHaveValue("1");
    expect(screen.getByText("Range reordering cancelled.")).toBeInTheDocument();
  });

  it("uses lazy thumbnails and keeps numeric editing available after a preview error", () => {
    renderWithQuery(<SplitRangeForm job={job({ pages: 2 })} />);
    const preview = screen.getByAltText("Page 1 preview");
    expect(preview).toHaveAttribute("loading", "lazy");
    fireEvent.error(preview);
    expect(preview).not.toBeVisible();
    expect(screen.getByLabelText("Start page")).toBeEnabled();
  });
  it("shares pending state with settings, blocks resubmission, and retains ranges after failure", async () => {
    let reject!: (error: Error) => void;
    const split = vi.spyOn(api, "split").mockReturnValue(new Promise((_, fail) => { reject = fail; }));
    const user = userEvent.setup();
    renderWithQuery(<SplitPdfTool config={config} restoredJob={job({pages: 5})} />);
    await user.clear(screen.getByLabelText("End page"));
    await user.type(screen.getByLabelText("End page"), "3");
    const submit = screen.getByRole("button", {name: "Split PDF"});
    await user.click(submit);
    expect(submit).toBeDisabled();
    expect(screen.getByLabelText("Start page")).toBeDisabled();
    await user.click(submit);
    expect(split).toHaveBeenCalledTimes(1);
    reject(new Error("offline"));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("could not be extracted"));
    expect(screen.getByLabelText("End page")).toHaveValue("3");
    expect(submit).toBeEnabled();
  });

});

it("keeps a legacy single-output PDF download in the footer and hides its redundant ZIP", () => {
  const legacy = job({kind: "split_pdf", status: "done", has_pdf: true, page_ranges: [{start: 1, end: 2}], artifacts: [
    {key: "range-1", filename: "pages.pdf", media_type: "application/pdf", pages: 2},
    {key: "package", filename: "ranges.zip", media_type: "application/zip", pages: null},
  ]});
  const view = renderWithQuery(<SplitPdfTool config={config} restoredJob={legacy} panel="files" />);
  const link = screen.getByRole("link", {name: "Download split PDF"});
  expect(view.container.querySelector(".action-footer")).toContainElement(link);
  expect(view.container.querySelector(".workbench-canvas")).not.toContainElement(link);
  expect(screen.queryByText(/ZIP/)).not.toBeInTheDocument();
  expect(screen.queryByRole("checkbox", {name: /Merge ranges/})).not.toBeInTheDocument();
});
