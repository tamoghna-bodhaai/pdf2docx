"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";
import type { JobKind } from "@/lib/api/types";

export type Tool = "pdf-to-docx" | "images-to-pdf" | "split-pdf" | "history";
const TOOLS = new Set<Tool>(["pdf-to-docx", "images-to-pdf", "split-pdf", "history"]);

export function useWorkspaceUrl() {
  const params = useSearchParams();
  const router = useRouter();
  const rawTool = params.get("tool") as Tool | null;
  const tool: Tool = rawTool && TOOLS.has(rawTool) ? rawTool : "pdf-to-docx";
  const jobId = params.get("job");
  const replace = useCallback((nextTool: Tool, nextJob?: string | null) => {
    const next = new URLSearchParams(params.toString());
    next.set("tool", nextTool);
    if (nextJob) next.set("job", nextJob); else next.delete("job");
    router.replace(`/?${next.toString()}`, { scroll: false });
  }, [params, router]);
  return { tool, jobId, replace };
}

export function toolForJob(kind: JobKind): Tool {
  if (kind === "images_to_pdf") return "images-to-pdf";
  if (kind === "split_pdf") return "split-pdf";
  return "pdf-to-docx";
}
