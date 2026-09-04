"use client";

/* eslint-disable @next/next/no-location-assign-relative-destination -- Signing out must reload through FastAPI's authenticated root. */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Brand } from "./brand";
import { Icon } from "./icons";
import { api } from "@/lib/api/client";
import { toolForJob, useWorkspaceUrl, type Tool } from "@/hooks/use-workspace-url";
import { useTheme } from "@/hooks/use-theme";
import { useNotifications } from "@/hooks/use-notifications";
import { PdfToDocxTool } from "@/features/pdf-to-docx/pdf-to-docx-tool";
import { ImageToPdfTool } from "@/features/image-to-pdf/image-to-pdf-tool";
import { SplitPdfTool } from "@/features/split-pdf/split-pdf-tool";
import { RecentFiles } from "@/features/history/recent-files";
import { ComparisonViewer } from "@/features/viewer/comparison-viewer";
import type { JobDto } from "@/lib/api/types";
import styles from "@/features/tools/tools.module.css";

const ACTIVE = new Set(["queued", "rendering", "transcribing", "building", "processing"]);
const navigation: Array<{ tool: Tool; label: string; icon: "file" | "images" | "split" | "history" }> = [
  { tool: "pdf-to-docx", label: "PDF to DOCX", icon: "file" },
  { tool: "images-to-pdf", label: "Images to PDF", icon: "images" },
  { tool: "split-pdf", label: "Split PDF", icon: "split" },
  { tool: "history", label: "History", icon: "history" },
];

export function Workspace() {
  const { tool, jobId, replace } = useWorkspaceUrl();
  const [drawer, setDrawer] = useState(false);
  const { theme, toggle } = useTheme();
  const config = useQuery({ queryKey: ["config"], queryFn: api.config });
  const user = useQuery({ queryKey: ["me"], queryFn: api.me });
  const history = useQuery({
    queryKey: ["history"], queryFn: api.history,
    refetchInterval: (query) => query.state.data?.jobs.some((job) => ACTIVE.has(job.status)) ? 1_400 : false,
  });
  const selectedFromHistory = history.data?.jobs.find((job) => job.id === jobId);
  const selected = useQuery({ queryKey: ["job", jobId], queryFn: () => api.job(jobId!), enabled: Boolean(jobId), initialData: selectedFromHistory });
  const notifications = useNotifications(history.data?.jobs ?? [], () => replace("history", null));

  function selectTool(next: Tool) { replace(next, null); setDrawer(false); }
  function open(job: JobDto) { replace(toolForJob(job.kind), job.id); setDrawer(false); }
  if (selected.data?.kind === "pdf_to_docx" && selected.data.status === "done") {
    return <ComparisonViewer job={selected.data} onBack={() => replace("history", null)} />;
  }

  return (
    <div className={`workspace-shell ${drawer ? "sidebar-open" : ""}`}>
      <aside className="sidebar" id="sidebar" aria-label="Workspace navigation" aria-hidden={!drawer ? undefined : false}>
        <div className="sidebar-top"><Brand /><nav className="sidebar-nav" aria-label="Tools"><span className="nav-label">Tools</span>{navigation.map((item) => <button className={`nav-item ${tool === item.tool ? "active" : ""}`} type="button" aria-current={tool === item.tool ? "page" : undefined} onClick={() => selectTool(item.tool)} key={item.tool}><Icon name={item.icon} /><span>{item.label}</span></button>)}</nav>{tool === "pdf-to-docx" && <div className={`provider-state ${config.data?.mathpix_key_configured ? "" : "unavailable"}`}><span className="provider-dot" aria-hidden="true" /><span><strong>Conversion service</strong><small>{config.isLoading ? "Checking availability…" : config.data?.mathpix_key_configured ? "Available" : "Key required"}</small></span></div>}</div>
        <div className="account-panel"><div className="account-identity"><span className="account-avatar" aria-hidden="true">{user.data?.email.slice(0, 1).toUpperCase() || "A"}</span><span><small>Signed in as</small><strong title={user.data?.email}>{user.data?.email || "Loading…"}</strong></span></div><button className="notification-toggle" type="button" disabled={notifications.disabled} aria-label={notifications.disabled ? `Desktop notifications: ${notifications.status}` : notifications.enabled ? "Disable desktop notifications" : "Enable desktop notifications"} aria-pressed={notifications.enabled} onClick={notifications.toggle}><span><strong>Desktop notifications</strong><small>{notifications.status}</small></span></button><div className="account-actions"><button className="theme-toggle" type="button" aria-label={`Switch to ${theme === "light" ? "dark" : "light"} theme`} aria-pressed={theme === "dark"} onClick={toggle}><span aria-hidden="true">{theme === "light" ? "☾" : "☀"}</span><span>{theme === "light" ? "Dark" : "Light"} theme</span></button><button type="button" onClick={async () => { await api.logout().catch(() => undefined); window.location.href = "/login"; }}>Sign out</button></div></div>
      </aside>
      <button className="sidebar-backdrop" type="button" aria-label="Close navigation" aria-hidden={!drawer} onClick={() => setDrawer(false)} />
      <main className="main-area"><header className="mobile-header"><button className="menu-toggle" type="button" aria-label={drawer ? "Close navigation" : "Open navigation"} aria-controls="sidebar" aria-expanded={drawer} onClick={() => setDrawer((value) => !value)}><Icon name="menu" /></button><span className="mobile-wordmark">PDF<span>2</span>DOCX</span><button className="mobile-upload" type="button" onClick={() => selectTool(tool === "history" ? "pdf-to-docx" : tool)}>New file</button></header>
        <section className="dashboard-view"><div className="dashboard-content">
          {config.isLoading && <div className="state-card"><strong>Loading workspace…</strong><p>Reading tool limits and recent files.</p></div>}
          {config.error && <div className="state-card" role="alert"><strong>Couldn’t load the workspace</strong><p>{config.error.message}</p><button type="button" onClick={() => config.refetch()}>Try again</button></div>}
          {config.data && tool === "pdf-to-docx" && <PdfToDocxTool key={`${tool}-${jobId || "new"}`} config={config.data} restoredJob={selected.data} onOpen={open} />}
          {config.data && tool === "images-to-pdf" && <ImageToPdfTool key={`${tool}-${jobId || "new"}`} config={config.data} restoredJob={selected.data} onCreated={open} />}
          {config.data && tool === "split-pdf" && <SplitPdfTool key={`${tool}-${jobId || "new"}`} config={config.data} restoredJob={selected.data} onCreated={open} />}
          {tool === "history" && <header className={styles.toolHeader}><p className="eyebrow">Your workspace</p><h1>Files and results.</h1><p>Open a conversion, download an output, regenerate a page range, or remove stored files.</p></header>}
          <RecentFiles data={history.data} loading={history.isLoading} error={history.error} onRetry={() => history.refetch()} onOpen={open} />
        </div></section>
      </main>
      <div className="toast-region" role="status" aria-live="polite">{notifications.notices.map((notice) => <div className="toast" key={notice.id}>{notice.message}<button type="button" aria-label="Dismiss notification" onClick={() => notifications.dismiss(notice.id)}>×</button></div>)}</div>
    </div>
  );
}
