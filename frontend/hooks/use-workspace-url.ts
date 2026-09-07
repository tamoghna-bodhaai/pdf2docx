"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect } from "react";
import type { JobKind } from "@/lib/api/types";

export type Tool = "pdf-to-docx" | "images-to-pdf" | "split-pdf" | "merge-pdf";
export type DockPanel = "setup" | "files" | "conversions" | "history" | "viewer";
const TOOLS = new Set<Tool>(["pdf-to-docx", "images-to-pdf", "split-pdf", "merge-pdf"]);
const PANELS = new Set<DockPanel>(["setup", "files", "conversions", "history", "viewer"]);

export function useWorkspaceUrl() {
  const params = useSearchParams();
  const router = useRouter();
  const rawTool = params.get("tool");
  const legacyHistory = rawTool === "history";
  const tool: Tool = rawTool && TOOLS.has(rawTool as Tool) ? rawTool as Tool : "pdf-to-docx";
  const rawPanel = params.get("panel") as DockPanel | null;
  const panel: DockPanel = legacyHistory ? "history" : rawPanel && PANELS.has(rawPanel) ? rawPanel : "setup";
  const jobId = params.get("job");
  const replace = useCallback((nextTool: Tool, nextJob?: string | null, nextPanel?: DockPanel) => {
    const next = new URLSearchParams(params.toString());
    next.set("tool", nextTool);
    next.set("panel", nextPanel ?? panel);
    if (nextJob) next.set("job", nextJob); else next.delete("job");
    router.replace(`/?${next.toString()}`, { scroll: false });
  }, [panel, params, router]);
  useEffect(() => {
    if (!legacyHistory || jobId) return;
    const next = new URLSearchParams(params.toString());
    next.set("tool", "pdf-to-docx");
    next.set("panel", "history");
    router.replace(`/?${next.toString()}`, { scroll: false });
  }, [jobId, legacyHistory, params, router]);
  return {
    tool, panel, jobId, legacyHistory, replace,
    setPanel: (nextPanel: DockPanel) => replace(tool, jobId, nextPanel),
  };
}

export function toolForJob(kind: JobKind): Tool {
  if (kind === "images_to_pdf") return "images-to-pdf";
  if (kind === "merge_pdf") return "merge-pdf";
  if (kind === "split_pdf") return "split-pdf";
  return "pdf-to-docx";
}

export function kindForTool(tool: Tool): JobKind {
  if (tool === "images-to-pdf") return "images_to_pdf";
  if (tool === "merge-pdf") return "merge_pdf";
  if (tool === "split-pdf") return "split_pdf";
  return "pdf_to_docx";
}
