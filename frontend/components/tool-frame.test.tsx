import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api/client";
import { job } from "@/test/fixtures";
import { renderWithQuery } from "@/test/render";
import { WorkspaceContent } from "./workspace-content";

describe("tool context dock", () => {
  it("shows every history section and clears each separately", async () => {
    Object.defineProperty(HTMLDialogElement.prototype, "showModal", { configurable: true, value() { this.open = true; } });
    Object.defineProperty(HTMLDialogElement.prototype, "close", { configurable: true, value() { this.open = false; } });
    const clearHistory = vi.spyOn(api, "clearHistory").mockResolvedValue({ deleted: 1 });
    const user = userEvent.setup();
    const split = job({ status: "done", output_filename: "report-pages-1-2.pdf", artifacts: [{ key: "range-1", filename: "report-pages-1-2.pdf", media_type: "application/pdf", pages: 2 }] });
    const image = job({ id: "image", kind: "images_to_pdf", status: "done", filename: "photo.png" });
    renderWithQuery(<WorkspaceContent panel="history" kind="split_pdf" jobs={[split, image, job({id: "docx", kind: "pdf_to_docx", status: "done", filename: "paid.pdf"})]} selected={split} onOpen={vi.fn()}><div /></WorkspaceContent>);

    expect(screen.getAllByText("report-pages-1-2.pdf").length).toBeGreaterThan(0);
    expect(screen.getByText("photo.png")).toBeInTheDocument();
    expect(screen.getByText("paid.pdf")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear Split PDF history" }));
    await user.click(screen.getByRole("button", { name: "Clear history" }));
    await waitFor(() => expect(clearHistory).toHaveBeenCalledWith("split_pdf"));

    for (const [label, kind] of [["PDF to DOCX", "pdf_to_docx"], ["Images to PDF", "images_to_pdf"]]) {
      await user.click(screen.getByRole("button", {name: `Clear ${label} history`}));
      await user.click(screen.getByRole("button", {name: "Clear history"}));
      await waitFor(() => expect(clearHistory).toHaveBeenCalledWith(kind));
    }
  });
});
