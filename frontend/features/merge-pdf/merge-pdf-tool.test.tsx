import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api/client";
import { config, job } from "@/test/fixtures";
import { renderWithQuery } from "@/test/render";
import { MergePdfTool } from "./merge-pdf-tool";

const sources = [{ id: "a", filename: "first.pdf", pages: 2, size_bytes: 100 }, { id: "b", filename: "second.pdf", pages: 1, size_bytes: 100 }];
const saved = () => job({ kind: "merge_pdf", merge_sources: sources, has_pdf: true });

describe("Merge PDF", () => {
  it("restores sources, saves keyboard order and retains focus", async () => {
    const order = vi.spyOn(api, "orderMergeSources").mockResolvedValue({ ...saved(), merge_sources: [...sources].reverse(), merge_dirty: true });
    renderWithQuery(<MergePdfTool config={config} restoredJob={saved()} />);
    const grip = screen.getByRole("button", { name: /Reorder second/ });
    grip.focus(); fireEvent.keyDown(grip, { key: "ArrowUp" });
    await waitFor(() => expect(order).toHaveBeenCalledWith("job-1", ["b", "a"]));
    await waitFor(() => expect(screen.getByRole("button", { name: /Reorder second.*position 1/ })).toHaveFocus());
    expect(screen.getByRole("link", { name: "Download previous result" })).toBeInTheDocument();
  });

  it("rolls back failed saves and disables edits while pending", async () => {
    let reject!: (error: Error) => void;
    vi.spyOn(api, "orderMergeSources").mockReturnValue(new Promise((_, fail) => { reject = fail; }));
    const view = renderWithQuery(<MergePdfTool config={config} restoredJob={saved()} />);
    fireEvent.keyDown(screen.getByRole("button", { name: /Reorder second/ }), { key: "ArrowUp" });
    expect(view.container.querySelector('input[type="file"]')).toBeDisabled();
    expect(screen.getByRole("button", { name: "Saving order…" })).toBeDisabled();
    reject(new Error("Save failed"));
    await screen.findByRole("alert");
    expect(screen.getByRole("button", { name: /Reorder first.*position 1/ })).toBeEnabled();
  });

  it("restores a cancelled pointer drag without saving", () => {
    const order = vi.spyOn(api, "orderMergeSources");
    renderWithQuery(<MergePdfTool config={config} restoredJob={saved()} />);
    const grip = screen.getByRole("button", { name: /Reorder first/ });
    fireEvent(grip, new MouseEvent("pointerdown", { button: 0, bubbles: true }));
    fireEvent.dragOver(screen.getByRole("button", { name: /Reorder second/ }).closest("li")!);
    expect(screen.getByRole("button", { name: /Reorder first.*position 2/ })).toBeInTheDocument();
    fireEvent.keyDown(grip, { key: "Escape" });
    expect(screen.getByRole("button", { name: /Reorder first.*position 1/ })).toBeInTheDocument();
    expect(order).not.toHaveBeenCalled();
  });

  it("appends and removes sources through saved edits", async () => {
    const upload = vi.spyOn(api, "uploadMergePdfs").mockResolvedValue({ ...saved(), merge_sources: [...sources, { ...sources[0], id: "c", filename: "third.pdf" }] });
    const order = vi.spyOn(api, "orderMergeSources").mockResolvedValue({ ...saved(), merge_sources: [sources[1], { ...sources[0], id: "c", filename: "third.pdf" }] });
    const view = renderWithQuery(<MergePdfTool config={config} restoredJob={saved()} />);
    fireEvent.change(view.container.querySelector('input[type="file"]')!, { target: { files: [new File(["pdf"], "third.pdf")] } });
    await screen.findByRole("button", { name: /Reorder third/ });
    expect(upload).toHaveBeenCalledWith(expect.any(Array), "job-1", expect.any(Function));
    await userEvent.click(screen.getByLabelText("Actions for first.pdf, position 1"));
    await userEvent.click(screen.getAllByRole("button", { name: "Remove" })[0]);
    await waitFor(() => expect(order).toHaveBeenCalledWith("job-1", ["b", "c"]));
  });
});
