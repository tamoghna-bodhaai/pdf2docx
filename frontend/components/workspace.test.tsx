import { screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api/client";
import { config, job } from "@/test/fixtures";
import { renderWithQuery } from "@/test/render";
import { Workspace } from "./workspace";

const navigation = vi.hoisted(() => ({ replace: vi.fn(), query: "tool=history&job=split-job" }));
vi.mock("next/navigation", () => {
  let query = "";
  let params = new URLSearchParams();
  return {
    useRouter: () => navigation,
    useSearchParams: () => {
      if (query !== navigation.query) { query = navigation.query; params = new URLSearchParams(query); }
      return params;
    },
  };
});

describe("legacy history links", () => {
  beforeEach(() => {
    navigation.replace.mockReset();
    navigation.query = "tool=history&job=split-job";
    vi.spyOn(api, "config").mockResolvedValue(config);
    vi.spyOn(api, "me").mockResolvedValue({ id: "user-1", email: "person@example.com" });
    vi.spyOn(api, "history").mockResolvedValue({ jobs: [], count: 0, total_cost: 0 });
    vi.spyOn(api, "job").mockResolvedValue(job({ id: "split-job", kind: "split_pdf" }));
  });

  it("migrates the retained job to its tool-specific History panel", async () => {
    renderWithQuery(<Workspace />);

    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith(
      "/?tool=split-pdf&job=split-job&panel=history",
      { scroll: false },
    ));
  });

  it("keeps a completed PDF-to-DOCX legacy link in History instead of opening the viewer", async () => {
    navigation.query = "tool=history&job=docx-job";
    vi.mocked(api.job).mockResolvedValue(job({
      id: "docx-job", kind: "pdf_to_docx", status: "done", has_docx: true,
    }));
    renderWithQuery(<Workspace />);

    await waitFor(() => expect(navigation.replace).toHaveBeenCalledWith(
      "/?tool=pdf-to-docx&job=docx-job&panel=history",
      { scroll: false },
    ));
    await waitFor(() => expect(screen.getByRole("navigation", { name: "Workspace navigation" })).toBeInTheDocument());
  });
});
