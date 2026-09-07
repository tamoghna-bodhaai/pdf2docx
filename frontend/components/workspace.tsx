"use client";

/* eslint-disable @next/next/no-location-assign-relative-destination -- FastAPI owns same-origin authentication redirects. */

import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { useResponsiveDrawer } from "@/hooks/use-responsive-drawer";
import { Brand } from "./brand";
import { api } from "@/lib/api/client";
import { toolForJob, useWorkspaceUrl, type Tool } from "@/hooks/use-workspace-url";
import { useTheme } from "@/hooks/use-theme";
import { useNotifications } from "@/hooks/use-notifications";
import { PdfToDocxTool } from "@/features/pdf-to-docx/pdf-to-docx-tool";
import { ImageToPdfTool } from "@/features/image-to-pdf/image-to-pdf-tool";
import { MergePdfTool } from "@/features/merge-pdf/merge-pdf-tool";
import { SplitPdfTool } from "@/features/split-pdf/split-pdf-tool";
import { ComparisonViewer } from "@/features/viewer/comparison-viewer";
import type { JobDto } from "@/lib/api/types";

const ACTIVE = new Set(["queued", "rendering", "transcribing", "building", "processing"]);
const navigation: Array<{ tool: Tool; label: string }> = [
  { tool: "pdf-to-docx", label: "PDF to DOCX" },
  { tool: "images-to-pdf", label: "Images to PDF" },
  { tool: "split-pdf", label: "Split PDF" },
  { tool: "merge-pdf", label: "Merge PDF" },
];

export function Workspace() {
  const { tool, panel, jobId, legacyHistory, replace, setPanel } = useWorkspaceUrl();
  const { close: sidebarClose, hidden: sidebarHidden, mobile: sidebarMobile, open: sidebarOpen, panelRef: sidebarPanelRef, show: sidebarShow, triggerRef: sidebarTriggerRef } = useResponsiveDrawer("(max-width: 1279px)");
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
  function viewFiles(job: JobDto) { replace(toolForJob(job.kind), job.id, "files"); }
  function open(job: JobDto) {
    replace(toolForJob(job.kind), job.id, job.kind === "pdf_to_docx" && job.status === "done" ? "viewer" : "files");
  }
  useEffect(() => {
    if (!legacyHistory || !selected.data) return;
    replace(toolForJob(selected.data.kind), selected.data.id, "history");
  }, [legacyHistory, replace, selected.data]);

  const viewing = panel === "viewer" && selected.data?.kind === "pdf_to_docx" && selected.data.status === "done";
  const toolProps = { panel, onPanel: setPanel, jobs, onOpen: open, onViewFiles: viewFiles };
  return <>{viewing && <ComparisonViewer key={selected.data!.id} job={selected.data!} onBack={() => replace("pdf-to-docx", jobId, "files")} />}<div hidden={viewing} className={`workspace-shell ${sidebarOpen ? "sidebar-open" : ""}`}>
    {sidebarMobile && sidebarOpen && <button className="sidebar-backdrop" type="button" aria-label="Close navigation" onClick={() => sidebarClose()} />}
    <aside ref={sidebarPanelRef} className="sidebar" aria-label="Main navigation" role={sidebarMobile ? "dialog" : undefined} aria-modal={sidebarMobile && sidebarOpen || undefined} inert={sidebarHidden || undefined} aria-hidden={sidebarHidden || undefined}>
      <div className="sidebar-top"><Brand />{sidebarMobile && <button type="button" onClick={() => sidebarClose()}>Close navigation</button>}<nav className="sidebar-nav" aria-label="Workspace navigation">{([{value: "setup", label: "Uploads"}, {value: "conversions", label: "Conversions"}, {value: "history", label: "History"}] as const).map(item => <button className={`nav-item ${panel === item.value || item.value === "setup" && panel === "files" ? "active" : ""}`} type="button" key={item.value} onClick={() => {setPanel(item.value); sidebarClose();}}>{item.label}</button>)}</nav></div>
      <div className="account-panel"><div className="account-identity"><span className="account-avatar">{user.data?.email.slice(0, 1).toUpperCase() || "A"}</span><span><small>Account</small><strong>{user.data?.email || "Loading…"}</strong></span></div><button type="button" disabled={notifications.disabled} onClick={notifications.toggle}>{notifications.enabled ? "Disable" : "Enable"} notifications</button><div className="account-actions"><button type="button" onClick={toggle}>{theme === "light" ? "Dark" : "Light"} theme</button><button type="button" onClick={async () => {await api.logout().catch(() => undefined); window.location.href = "/login";}}>Sign out</button></div></div>
    </aside>
    <div className="workspace-main" inert={sidebarMobile && sidebarOpen || undefined}>
    <header className="topbar">
      <button className="navigation-trigger" ref={sidebarTriggerRef} type="button" onClick={sidebarShow} aria-expanded={sidebarOpen}>Navigation</button>
      <nav className="tool-nav" aria-label="Document tools">{navigation.map((item) => <button type="button" className={tool === item.tool ? "active" : ""} aria-current={tool === item.tool ? "page" : undefined} onClick={() => replace(item.tool, null, "setup")} key={item.tool}>{item.label}</button>)}</nav>

    </header>
    {config.isLoading && <main className="state-card"><strong>Loading workspace…</strong></main>}
    {selected.error && <p className="warn" role="alert">Couldn’t restore this job: {selected.error.message}<button type="button" onClick={() => selected.refetch()}>Try again</button></p>}
    {config.error && <main className="state-card" role="alert"><strong>Couldn’t load the workspace</strong><p>{config.error.message}</p><button type="button" onClick={() => config.refetch()}>Try again</button></main>}
    {config.data && (!jobId || selected.data) && tool === "pdf-to-docx" && <PdfToDocxTool {...toolProps} onRemoved={(ids) => { if (jobId && ids.includes(jobId)) replace("pdf-to-docx", null, "setup"); }} onNew={() => { if (jobId) replace("pdf-to-docx", null, "setup"); }} onCompare={(job) => replace("pdf-to-docx", job.id, "viewer")} key={`${tool}-${jobId || "new"}`} config={config.data} restoredJob={selected.data} />}
    {config.data && (!jobId || selected.data) && tool === "images-to-pdf" && <ImageToPdfTool {...toolProps} key={tool} config={config.data} restoredJob={selected.data} selectedJobId={jobId} onCreated={open} onDeleted={() => replace("images-to-pdf", null, "setup")} />}
    {config.data && (!jobId || selected.data) && tool === "split-pdf" && <SplitPdfTool {...toolProps} key={`${tool}-${jobId || "new"}`} config={config.data} restoredJob={selected.data} onCreated={open} onDeleted={() => replace("split-pdf", null, "setup")} />}
    {config.data && (!jobId || selected.data) && tool === "merge-pdf" && <MergePdfTool {...toolProps} key={`${tool}-${jobId || "new"}`} config={config.data} restoredJob={selected.data} onCreated={open} onDeleted={() => replace("merge-pdf", null, "setup")} />}
    </div><div className="toast-region" role="status" aria-live="polite">{notifications.notices.map((notice) => <div className="toast" key={notice.id}>{notice.message}<button type="button" aria-label="Dismiss notification" onClick={() => notifications.dismiss(notice.id)}>×</button></div>)}</div>
  </div></>;
}
