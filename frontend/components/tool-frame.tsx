"use client";

import type { ReactNode } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { api } from "@/lib/api/client";
import type { DockPanel } from "@/hooks/use-workspace-url";
import type { JobDto, JobKind } from "@/lib/api/types";
import { DownloadMenu } from "./download-menu";
import { ConfirmDialog } from "./confirm-dialog";

const ACTIVE = new Set(["ready", "paused", "queued", "rendering", "transcribing", "building", "processing", "error"]);
const tabs: Array<{ value: DockPanel; label: string }> = [
  { value: "setup", label: "Setup" },
  { value: "files", label: "Files" },
  { value: "conversions", label: "Conversions" },
  { value: "history", label: "History" },
];
const DOCK_QUERY = "(max-width: 1100px)";
function subscribeDock(listener: () => void) {
  if (typeof window.matchMedia !== "function") return () => undefined;
  const media = window.matchMedia(DOCK_QUERY);
  media.addEventListener("change", listener);
  return () => media.removeEventListener("change", listener);
}
function dockSnapshot() { return typeof window.matchMedia === "function" && window.matchMedia(DOCK_QUERY).matches; }

function when(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

function JobCard({ job, onOpen, onDelete }: { job: JobDto; onOpen: (job: JobDto) => void; onDelete: (job: JobDto) => void }) {
  const cost = job.kind === "pdf_to_docx" ? job.cost_known ? `$${job.cost.toFixed(4)}` : "—" : "No charge";
  return <article className="dock-job-card">
    <div className="dock-job-heading"><div><strong title={job.output_filename || job.filename}>{job.output_filename || job.filename}</strong><small>{when(job.created_at)} · {job.output_pages || job.pages} pages</small></div><span className={`history-status ${job.status}`}>{job.status}</span></div>
    <div className="dock-job-meta"><span>{cost}</span><span>{job.source_filenames.length} source{job.source_filenames.length === 1 ? "" : "s"}</span></div>
    <div className="dock-job-actions"><button className="primary" type="button" onClick={() => onOpen(job)}>Open</button><details><summary aria-label={`More actions for ${job.filename}`}>•••</summary><div><DownloadMenu job={job} /><button type="button" onClick={() => onOpen(job)}>View files</button><button type="button" aria-label={`Delete ${job.filename}`} onClick={() => onDelete(job)}>Delete</button></div></details></div>
  </article>;
}

function FilesPanel({ job }: { job?: JobDto }) {
  if (!job) return <div className="dock-empty"><strong>No job selected</strong><p>Choose a conversion to see its source and outputs.</p></div>;
  return <div className="dock-stack">
    <section className="dock-section"><h3>Source</h3>{job.source_filenames.map((name, index) => <div className="artifact-row" key={`${name}-${index}`}><span>{name}</span><small>{job.pages ? `${job.pages} pages` : "Source"}</small></div>)}</section>
    <section className="dock-section"><h3>Outputs</h3>{job.artifacts.length ? job.artifacts.map((artifact) => <a className="artifact-row" href={api.artifactUrl(job.id, artifact.key)} download key={artifact.key}><span>{artifact.filename}</span><small>{artifact.pages === null ? "ZIP package" : `${artifact.pages} pages`}</small></a>) : job.has_pdf || job.has_docx || job.has_md ? <DownloadMenu job={job} /> : <p>No output is ready yet.</p>}</section>
  </div>;
}

export function ToolFrame({
  panel, onPanel, kind, title, eyebrow, description, canvas, setup, jobs, selected, onOpen,
}: {
  panel: DockPanel;
  onPanel: (panel: DockPanel) => void;
  kind: JobKind;
  title: string;
  eyebrow: string;
  description: string;
  canvas: ReactNode;
  setup: ReactNode;
  jobs: JobDto[];
  selected?: JobDto;
  onOpen: (job: JobDto) => void;
}) {
  const client = useQueryClient();
  const [dockOpen, setDockOpen] = useState(false);
  const overlay = useSyncExternalStore(subscribeDock, dockSnapshot, () => false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const dockRef = useRef<HTMLElement>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const [confirmJob, setConfirmJob] = useState<JobDto | null>(null);
  const scoped = jobs.filter((job) => job.kind === kind);
  const conversions = scoped.filter((job) => ACTIVE.has(job.status));
  const history = scoped.filter((job) => job.status === "done" || job.status === "cancelled");
  const clear = useMutation({
    mutationFn: () => api.clearHistory(kind),
    onSuccess: () => { setConfirmClear(false); client.invalidateQueries({ queryKey: ["history"] }); },
  });
  const remove = useMutation({
    mutationFn: (job: JobDto) => api.deleteJob(job.id),
    onSuccess: () => { setConfirmJob(null); client.invalidateQueries({ queryKey: ["history"] }); },
  });
  useEffect(() => {
    if (!overlay || !dockOpen) return;
    dockRef.current?.querySelector<HTMLElement>("button, a, input, summary")?.focus();
    const close = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.preventDefault(); setDockOpen(false); triggerRef.current?.focus();
    };
    document.addEventListener("keydown", close);
    return () => document.removeEventListener("keydown", close);
  }, [dockOpen, overlay]);
  function choose(next: DockPanel) { onPanel(next); setDockOpen(true); }

  return <div className={`tool-workbench ${dockOpen ? "dock-open" : ""}`}>
    <main className="workbench-canvas">
      <header className="canvas-heading"><div><p className="eyebrow">{eyebrow}</p><h1>{title}</h1><p>{description}</p></div><button ref={triggerRef} className="dock-trigger" type="button" aria-controls="context-dock" aria-expanded={dockOpen} onClick={() => setDockOpen((open) => !open)}>Workspace</button></header>
      {canvas}
    </main>
    <button className="dock-backdrop" type="button" aria-label="Close workspace panel" onClick={() => setDockOpen(false)} />
    <aside ref={dockRef} className="context-dock" id="context-dock" aria-label="Tool workspace" aria-hidden={overlay && !dockOpen || undefined} inert={overlay && !dockOpen || undefined}>
      <div className="dock-grabber" aria-hidden="true" />
      <nav className="dock-tabs" aria-label="Workspace panels">{tabs.map((tab) => <button type="button" className={panel === tab.value ? "active" : ""} aria-current={panel === tab.value ? "page" : undefined} onClick={() => choose(tab.value)} key={tab.value}>{tab.label}</button>)}</nav>
      <div className="dock-content">
        {panel === "setup" && setup}
        {panel === "files" && <FilesPanel job={selected} />}
        {panel === "conversions" && <div className="dock-stack">{conversions.length ? conversions.map((job) => <JobCard job={job} onOpen={onOpen} onDelete={setConfirmJob} key={job.id} />) : <div className="dock-empty"><strong>No active conversions</strong><p>Queued, running, and actionable jobs appear here.</p></div>}</div>}
        {panel === "history" && <div className="dock-stack">{history.length ? history.map((job) => <JobCard job={job} onOpen={onOpen} onDelete={setConfirmJob} key={job.id} />) : <div className="dock-empty"><strong>No history yet</strong><p>Finished work for this tool appears here.</p></div>}{history.length > 0 && <button type="button" className="clear-history" onClick={() => setConfirmClear(true)}>Clear completed history</button>}</div>}
      </div>
      <button className="dock-close" type="button" onClick={() => setDockOpen(false)}>Close</button>
    </aside>
    <ConfirmDialog open={confirmClear} title="Clear completed history?" description="Completed and inactive jobs for this tool will be deleted with their stored outputs." action="Clear history" onClose={() => setConfirmClear(false)} onConfirm={() => clear.mutate()} />
    <ConfirmDialog open={Boolean(confirmJob)} title={`Delete ${confirmJob?.filename || "this job"}?`} description="This removes the source and every stored output. This cannot be undone." action="Delete" onClose={() => setConfirmJob(null)} onConfirm={() => confirmJob && remove.mutate(confirmJob)} />
  </div>;
}
