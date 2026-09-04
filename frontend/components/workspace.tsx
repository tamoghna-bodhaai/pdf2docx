"use client";

/* eslint-disable @next/next/no-location-assign-relative-destination -- FastAPI owns same-origin authentication redirects. */

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { Brand } from "./brand";
import { api } from "@/lib/api/client";
import { toolForJob, useWorkspaceUrl, type Tool } from "@/hooks/use-workspace-url";
import { useTheme } from "@/hooks/use-theme";
import { useNotifications } from "@/hooks/use-notifications";
import { PdfToDocxTool } from "@/features/pdf-to-docx/pdf-to-docx-tool";
import { ImageToPdfTool } from "@/features/image-to-pdf/image-to-pdf-tool";
import { SplitPdfTool } from "@/features/split-pdf/split-pdf-tool";
import { ComparisonViewer } from "@/features/viewer/comparison-viewer";
import type { JobDto } from "@/lib/api/types";

const ACTIVE = new Set(["queued", "rendering", "transcribing", "building", "processing"]);
const navigation: Array<{ tool: Tool; label: string }> = [
  { tool: "pdf-to-docx", label: "PDF to DOCX" },
  { tool: "images-to-pdf", label: "Images to PDF" },
  { tool: "split-pdf", label: "Split PDF" },
];

export function Workspace() {
  const { tool, panel, jobId, legacyHistory, replace, setPanel } = useWorkspaceUrl();
  const { theme, toggle } = useTheme();
  const config = useQuery({ queryKey: ["config"], queryFn: api.config });
  const user = useQuery({ queryKey: ["me"], queryFn: api.me });
  const history = useQuery({
    queryKey: ["history"], queryFn: api.history,
    refetchInterval: (query) => query.state.data?.jobs.some((job) => ACTIVE.has(job.status)) ? 1_400 : false,
  });
  const selectedFromHistory = history.data?.jobs.find((job) => job.id === jobId);
  const selected = useQuery({
    queryKey: ["job", jobId], queryFn: () => api.job(jobId!), enabled: Boolean(jobId),
    initialData: selectedFromHistory,
    refetchInterval: (query) => query.state.data && ACTIVE.has(query.state.data.status) ? 1_400 : false,
  });
  const notifications = useNotifications(history.data?.jobs ?? [], () => setPanel("history"));
  const jobs = history.data?.jobs ?? [];
  function open(job: JobDto) { replace(toolForJob(job.kind), job.id, "files"); }
  useEffect(() => {
    if (!legacyHistory || !selected.data) return;
    replace(toolForJob(selected.data.kind), selected.data.id, "history");
  }, [legacyHistory, replace, selected.data]);

  if (panel !== "history" && selected.data?.kind === "pdf_to_docx" && selected.data.status === "done") {
    return <ComparisonViewer job={selected.data} onBack={() => replace("pdf-to-docx", selected.data?.id, "files")} />;
  }

  const toolProps = { panel, onPanel: setPanel, jobs, onOpen: open };
  return <div className="workspace-shell">
    <header className="topbar">
      <Brand />
      <nav className="tool-nav" aria-label="Document tools">{navigation.map((item) => <button type="button" className={tool === item.tool ? "active" : ""} aria-current={tool === item.tool ? "page" : undefined} onClick={() => replace(item.tool, null, "setup")} key={item.tool}>{item.label}</button>)}</nav>
      <details className="account-menu"><summary aria-label="Account and appearance">{user.data?.email.slice(0, 1).toUpperCase() || "A"}</summary><div><strong>{user.data?.email || "Loading…"}</strong><button type="button" onClick={toggle}>{theme === "light" ? "Dark" : "Light"} theme</button><button type="button" disabled={notifications.disabled} onClick={notifications.toggle}>{notifications.enabled ? "Disable" : "Enable"} notifications</button><button type="button" onClick={async () => { await api.logout().catch(() => undefined); window.location.href = "/login"; }}>Sign out</button></div></details>
    </header>
    {config.isLoading && <main className="state-card"><strong>Loading workspace…</strong></main>}
    {config.error && <main className="state-card" role="alert"><strong>Couldn’t load the workspace</strong><p>{config.error.message}</p><button type="button" onClick={() => config.refetch()}>Try again</button></main>}
    {config.data && tool === "pdf-to-docx" && <PdfToDocxTool {...toolProps} key={`${tool}-${jobId || "new"}`} config={config.data} restoredJob={selected.data} />}
    {config.data && tool === "images-to-pdf" && <ImageToPdfTool {...toolProps} key={tool} config={config.data} restoredJob={selected.data} selectedJobId={jobId} onCreated={open} onDeleted={() => replace("images-to-pdf", null, "setup")} />}
    {config.data && tool === "split-pdf" && <SplitPdfTool {...toolProps} key={`${tool}-${jobId || "new"}`} config={config.data} restoredJob={selected.data} onCreated={open} onDeleted={() => replace("split-pdf", null, "setup")} />}
    <div className="toast-region" role="status" aria-live="polite">{notifications.notices.map((notice) => <div className="toast" key={notice.id}>{notice.message}<button type="button" aria-label="Dismiss notification" onClick={() => notifications.dismiss(notice.id)}>×</button></div>)}</div>
  </div>;
}
