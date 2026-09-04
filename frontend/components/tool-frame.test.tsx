import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api/client";
import { job } from "@/test/fixtures";
import { renderWithQuery } from "@/test/render";
import { ToolFrame } from "./tool-frame";

describe("tool context dock", () => {
  it("filters and clears history by tool and exposes selected artifacts in Files", async () => {
    Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value() { this.open = true; } });
    Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value() { this.open = false; } });
    const clearHistory = vi.spyOn(api, "clearHistory").mockResolvedValue({ deleted: 1 });
    const user = userEvent.setup();
    const split = job({ status: "done", output_filename: "report-pages-1-2.pdf", artifacts: [{ key: "range-1", filename: "report-pages-1-2.pdf", media_type: "application/pdf", pages: 2 }] });
    const image = job({ id: "image", kind: "images_to_pdf", status: "done", filename: "photo.png" });
    const { rerender } = renderWithQuery(<ToolFrame panel="history" onPanel={vi.fn()} kind="split_pdf" title="Split PDF" eyebrow="No charge" description="" canvas={<div />} setup={<div />} jobs={[split, image]} selected={split} onOpen={vi.fn()} />);

    expect(screen.getAllByText("report-pages-1-2.pdf").length).toBeGreaterThan(0);
    expect(screen.queryByText("photo.png")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear completed history" }));
    await user.click(screen.getByRole("button", { name: "Clear history" }));
    await waitFor(() => expect(clearHistory).toHaveBeenCalledWith("split_pdf"));

    rerender(<ToolFrame panel="files" onPanel={vi.fn()} kind="split_pdf" title="Split PDF" eyebrow="No charge" description="" canvas={<div />} setup={<div />} jobs={[split, image]} selected={split} onOpen={vi.fn()} />);
    expect(screen.getByRole("link", { name: /report-pages-1-2\.pdf/ })).toHaveAttribute("href", "/api/jobs/job-1/artifacts/range-1");
  });
});
