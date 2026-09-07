"use client";
import { visibleArtifacts } from "@/lib/api/artifacts";
import type { ReactNode } from "react";
import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api/client";
import type { DockPanel } from "@/hooks/use-workspace-url";
import type { JobDto, JobKind } from "@/lib/api/types";
import { DownloadMenu } from "./download-menu";
import { ConfirmDialog } from "./confirm-dialog";
const ACTIVE = new Set(["ready", "paused", "queued", "rendering", "transcribing", "building", "processing", "error"]);
function when(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: "medium" }).format(date);
}

function JobCard({ job, onOpen, onViewFiles, onDelete }: { job: JobDto; onOpen: (job: JobDto) => void; onViewFiles: (job: JobDto) => void; onDelete: (job: JobDto) => void }) {
  const cost = job.kind === "pdf_to_docx" ? job.cost_known ? `$${job.cost.toFixed(4)}` : "—" : null;
  return <article className="dock-job-card">
    <div className="dock-job-heading"><div><strong title={job.output_filename || job.filename}>{job.output_filename || job.filename}</strong><small>{when(job.created_at)} · {job.output_pages || job.pages} pages</small></div><span className={`history-status ${job.status}`}>{job.status}</span></div>
    <div className="dock-job-meta">{cost !== null && <span>{cost}</span>}<span>{job.source_filenames.length} source{job.source_filenames.length === 1 ? "" : "s"}</span></div>
    <div className="dock-job-actions"><button className="primary" type="button" onClick={() => onOpen(job)}>Open</button><details><summary aria-label={`More actions for ${job.filename}`}>•••</summary><div><DownloadMenu job={job} /><button type="button" onClick={() => onViewFiles(job)}>View files</button><button type="button" aria-label={`Delete ${job.filename}`} onClick={() => onDelete(job)}>Delete</button></div></details></div>
  </article>;
}

function FilesPanel({ job }: { job?: JobDto }) {
  if (!job) return <div className="dock-empty"><strong>No job selected</strong><p>Choose a conversion to see its source and outputs.</p></div>;
  return <div className="dock-stack">
    <section className="dock-section"><h3>Source</h3>{job.source_filenames.map((name, index) => <div className="artifact-row" key={`${name}-${index}`}><span>{name}</span><small>{job.pages ? `${job.pages} pages` : "Source"}</small></div>)}</section>
    <section className="dock-section"><h3>Outputs</h3>{job.artifacts.length ? visibleArtifacts(job).map((artifact) => <a className="artifact-row" href={api.artifactUrl(job.id, artifact.key)} download key={artifact.key}><span>{artifact.filename}</span><small>{artifact.pages === null ? "ZIP package" : `${artifact.pages} pages`}</small></a>) : job.has_pdf || job.has_docx || job.has_md ? <DownloadMenu job={job} /> : <p>No output is ready yet.</p>}</section>
  </div>;
}

export function WorkspaceContent({panel, kind, jobs, selected, onOpen, onViewFiles = onOpen, children}: {panel: DockPanel; kind: JobKind; jobs: JobDto[]; selected?: JobDto; onOpen: (job: JobDto) => void; onViewFiles?: (job: JobDto) => void; children: ReactNode}) {
  const client = useQueryClient();
  const [confirmClear, setConfirmClear] = useState<JobKind | null>(null);
  const [confirmJob, setConfirmJob] = useState<JobDto | null>(null);
  const conversions = jobs.filter((job) => ACTIVE.has(job.status));
  const sections: Array<[JobKind, string]> = [["pdf_to_docx", "PDF to DOCX"], ["split_pdf", "Split PDF"], ["merge_pdf", "Merge PDF"], ["images_to_pdf", "Images to PDF"]];
  const clear = useMutation({
    mutationFn: () => api.clearHistory(confirmClear!),
    onSuccess: () => { setConfirmClear(null); client.invalidateQueries({ queryKey: ["history"] }); },
  });
  const remove = useMutation({
    mutationFn: (job: JobDto) => api.deleteJob(job.id),
    onSuccess: () => { setConfirmJob(null); client.invalidateQueries({ queryKey: ["history"] }); },
  });
return <><div hidden={panel === "history" || panel === "conversions"}>{children}{panel === "files" && kind !== "split_pdf" && kind !== "merge_pdf" && <FilesPanel job={selected} />}</div>
        {panel === "conversions" && <div className="dock-stack"><h2>Conversions</h2>{conversions.length ? conversions.map((job) => <JobCard job={job} onOpen={onOpen} onViewFiles={onViewFiles} onDelete={setConfirmJob} key={job.id} />) : <div className="dock-empty"><strong>No active conversions</strong><p>Queued, running, and actionable jobs appear here.</p></div>}</div>}
        {panel === "history" && <div className="dock-stack"><h2>History</h2>{sections.map(([sectionKind, label]) => {
          const completed = jobs.filter(job => job.kind === sectionKind && job.status === "done").sort((a, b) => b.created_at.localeCompare(a.created_at));
          return <section className="dock-section" aria-label={`${label} history`} key={sectionKind}><h3>{label} <span>({completed.length})</span></h3>{completed.length ? completed.map(job => <JobCard job={job} onOpen={onOpen} onViewFiles={onViewFiles} onDelete={setConfirmJob} key={job.id} />) : <div className="dock-empty"><strong>No completed {label} jobs</strong></div>}{completed.length > 0 && <button type="button" className="clear-history" onClick={() => setConfirmClear(sectionKind)}>Clear {label} history</button>}</section>;
        })}</div>}

    {(clear.error || remove.error) && <p role="alert">{clear.error?.message || remove.error?.message}</p>}
    <ConfirmDialog open={Boolean(confirmClear)} title="Clear completed history?" description="Completed jobs in this section will be deleted with their stored outputs." action="Clear history" onClose={() => setConfirmClear(null)} onConfirm={() => clear.mutate()} />
    <ConfirmDialog open={Boolean(confirmJob)} title={`Delete ${confirmJob?.filename || "this job"}?`} description="This removes the source and every stored output. This cannot be undone." action="Delete" onClose={() => setConfirmJob(null)} onConfirm={() => confirmJob && remove.mutate(confirmJob)} />
</>;
}
