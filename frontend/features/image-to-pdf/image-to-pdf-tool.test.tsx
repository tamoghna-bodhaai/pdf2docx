import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { config } from "@/test/fixtures";
import { job } from "@/test/fixtures";
import { api } from "@/lib/api/client";
import { renderWithQuery } from "@/test/render";
import { ImageToPdfTool } from "./image-to-pdf-tool";

describe("ordered image list", () => {
  const create = vi.fn<(blob: Blob) => string>();
  const revoke = vi.fn<(url: string) => void>();
  beforeEach(() => {
    let number = 0; create.mockImplementation(() => `blob:${++number}`);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: create });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: revoke });
    vi.stubGlobal("crypto", { randomUUID: () => String(Math.random()) });
  });
  afterEach(() => { create.mockReset(); revoke.mockReset(); vi.unstubAllGlobals(); });

  it("moves images explicitly and revokes URLs on removal and unmount", async () => {
    const user = userEvent.setup();
    const view = renderWithQuery(<ImageToPdfTool config={config} />);
    const picker = view.container.querySelector<HTMLInputElement>('input[type="file"]')!;
    fireEvent.change(picker, { target: { files: [new File(["a"], "first.png", { type: "image/png" }), new File(["b"], "second.png", { type: "image/png" })] } });
    expect(screen.getAllByText(/Page [12]/).map((node) => node.textContent)).toEqual(expect.arrayContaining([expect.stringContaining("Page 1"), expect.stringContaining("Page 2")]));
    await user.type(screen.getByRole("button", { name: /Reorder second\.png/ }), "{ArrowUp}");
    expect(screen.getAllByRole("img").map((image) => image.getAttribute("alt"))).toEqual(["Preview of second.png", "Preview of first.png"]);
    await user.click(screen.getAllByRole("button", { name: "Remove" })[0]);
    expect(revoke).toHaveBeenCalledWith("blob:2");
    view.unmount();
    expect(revoke).toHaveBeenCalledWith("blob:1");
  });

  it("treats a zero upload limit as unlimited", () => {
    const view = renderWithQuery(<ImageToPdfTool config={{ ...config, image_max_upload_mb: 0 }} />);
    const picker = view.container.querySelector<HTMLInputElement>('input[type="file"]')!;
    fireEvent.change(picker, { target: { files: [new File(["content"], "large.png", { type: "image/png" })] } });
    expect(screen.getByText("large.png")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Create PDF/ })).toBeEnabled();
  });

  it("submits visible order with the selected orientation", async () => {
    const upload = vi.spyOn(api, "uploadImages").mockResolvedValue(job({ kind: "images_to_pdf", status: "done", page_orientation: "portrait" }));
    const user = userEvent.setup();
    const view = renderWithQuery(<ImageToPdfTool config={config} />);
    const picker = view.container.querySelector<HTMLInputElement>('input[type="file"]')!;
    const first = new File(["a"], "first.png", { type: "image/png" });
    const second = new File(["b"], "second.png", { type: "image/png" });
    fireEvent.change(picker, { target: { files: [first, second] } });
    await user.type(screen.getByRole("button", { name: /Reorder second\.png/ }), "{ArrowUp}");
    await user.click(screen.getByRole("radio", { name: "Portrait" }));
    await user.click(screen.getByRole("button", { name: /Create PDF/ }));
    expect(upload).toHaveBeenCalledWith([second, first], "portrait", expect.any(Function));
    await waitFor(() => {
      expect(revoke).toHaveBeenCalledWith("blob:1");
      expect(revoke).toHaveBeenCalledWith("blob:2");
    });
  });

  it("keeps editable previews while processing and restores drag order when cancelled", async () => {
    vi.spyOn(api, "uploadImages").mockResolvedValue(job({ kind: "images_to_pdf", status: "processing" }));
    const user = userEvent.setup();
    const view = renderWithQuery(<ImageToPdfTool config={config} />);
    const picker = view.container.querySelector<HTMLInputElement>('input[type="file"]')!;
    fireEvent.change(picker, { target: { files: [new File(["a"], "first.png", { type: "image/png" }), new File(["b"], "second.png", { type: "image/png" })] } });
    const [, secondCard] = screen.getAllByRole("listitem");
    Object.defineProperty(document, "elementFromPoint", { configurable: true, value: vi.fn(() => secondCard) });
    const firstHandle = screen.getByRole("button", { name: /Reorder first\.png/ });
    fireEvent.pointerDown(firstHandle, { pointerId: 1 });
    fireEvent.pointerMove(screen.getByRole("list", { name: "PDF pages" }), { pointerId: 1, clientX: 10, clientY: 10 });
    expect(screen.getAllByRole("img").map((image) => image.getAttribute("alt"))).toEqual(["Preview of second.png", "Preview of first.png"]);
    fireEvent.pointerCancel(firstHandle, { pointerId: 1 });
    expect(screen.getAllByRole("img").map((image) => image.getAttribute("alt"))).toEqual(["Preview of first.png", "Preview of second.png"]);
    expect(screen.getByText("Image reordering cancelled.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Create PDF/ }));
    expect(revoke).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Create another" }));
    expect(screen.getByText("first.png")).toBeInTheDocument();
  });
});
