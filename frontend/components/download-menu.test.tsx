import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { job } from "@/test/fixtures";
import { DownloadMenu } from "./download-menu";

describe("DownloadMenu", () => {
  it("can gain download links after initially rendering nothing", () => {
    const { rerender } = render(<DownloadMenu job={job({ kind: "pdf_to_docx" })} />);

    expect(screen.queryByText("Download")).not.toBeInTheDocument();

    rerender(<DownloadMenu job={job({ kind: "pdf_to_docx", has_docx: true })} />);

    expect(screen.getByText("Download")).toBeInTheDocument();
  });

  it("closes when clicking outside the menu", async () => {
    const user = userEvent.setup();
    const { container } = render(<DownloadMenu job={job({ kind: "pdf_to_docx", has_docx: true })} />);
    const menu = container.querySelector("details");

    await user.click(screen.getByText("Download"));
    expect(menu).toHaveAttribute("open");

    await user.click(document.body);
    expect(menu).not.toHaveAttribute("open");
  });

  it("does not offer a per-document ZIP for PDF to DOCX", async () => {
    const user = userEvent.setup();
    render(<DownloadMenu job={job({ kind: "pdf_to_docx", has_docx: true, has_package: true })} />);

    await user.click(screen.getByText("Download"));
    expect(screen.getByText("DOCX")).toBeVisible();
    expect(screen.queryByText("Download all (.zip)")).not.toBeInTheDocument();
  });
});
