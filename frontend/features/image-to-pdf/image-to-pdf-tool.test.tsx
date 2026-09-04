import { fireEvent, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { config } from "@/test/fixtures";
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
    await user.click(screen.getByRole("button", { name: "Move second.png earlier" }));
    expect(screen.getAllByRole("img").map((image) => image.getAttribute("alt"))).toEqual(["Preview of second.png", "Preview of first.png"]);
    await user.click(screen.getByRole("button", { name: "Remove second.png" }));
    expect(revoke).toHaveBeenCalledWith("blob:2");
    view.unmount();
    expect(revoke).toHaveBeenCalledWith("blob:1");
  });

  it("treats a zero upload limit as unlimited", () => {
    const view = renderWithQuery(<ImageToPdfTool config={{ ...config, image_max_upload_mb: 0 }} />);
    const picker = view.container.querySelector<HTMLInputElement>('input[type="file"]')!;
    fireEvent.change(picker, { target: { files: [new File(["content"], "large.png", { type: "image/png" })] } });
    expect(screen.getByText("large.png")).toBeInTheDocument();
    expect(screen.getByText(/no size limit/)).toBeInTheDocument();
  });
});
