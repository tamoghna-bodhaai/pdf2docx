import { fireEvent, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { job } from "@/test/fixtures";
import { renderWithQuery } from "@/test/render";
import { ComparisonViewer } from "./comparison-viewer";

describe("comparison viewer keyboard controls", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(
      JSON.stringify({ markdown: "Converted page" }),
      { status: 200, headers: { "content-type": "application/json" } },
    )));
  });

  it("moves focus with tab arrows and pages with document arrows", async () => {
    renderWithQuery(<ComparisonViewer job={job({ kind: "pdf_to_docx", pages: 3 })} onBack={vi.fn()} />);
    const source = screen.getByRole("tab", { name: "Source" });
    const converted = screen.getByRole("tab", { name: "Converted" });
    source.focus();
    fireEvent.keyDown(source, { key: "ArrowRight" });
    expect(converted).toHaveFocus();
    expect(converted).toHaveAttribute("aria-selected", "true");

    const rendered = screen.getByRole("tab", { name: "Rendered" });
    const text = screen.getByRole("tab", { name: "Text" });
    rendered.focus();
    fireEvent.keyDown(rendered, { key: "ArrowRight" });
    expect(text).toHaveFocus();
    expect(text).toHaveAttribute("aria-selected", "true");

    fireEvent.keyDown(document, { key: "ArrowRight" });
    expect(screen.getByLabelText("Page number")).toHaveValue("2");
    await waitFor(() => expect(fetch).toHaveBeenCalled());
  });
});
