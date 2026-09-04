"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { ConfirmDialog } from "@/components/confirm-dialog";
import { DownloadMenu } from "@/components/download-menu";
import { api, ApiError } from "@/lib/api/client";
import type { HistoryDto, JobDto } from "@/lib/api/types";

const labels = { pdf_to_docx: "PDF to DOCX", images_to_pdf: "Images to PDF", split_pdf: "Split PDF" };

function when(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date);
}

export function RecentFiles({ data, loading, error, onRetry, onOpen }: { data?: HistoryDto; loading: boolean; error: Error | null; onRetry: () => void; onOpen: (job: JobDto) => void }) {
  const queryClient = useQueryClient();
  const [confirm, setConfirm] = useState<{ mode: "job"; job: JobDto } | { mode: "all" } | null>(null);
  const [actionError, setActionError] = useState("");
  const remove = useMutation({
    mutationFn: async () => confirm?.mode === "job" ? api.deleteJob(confirm.job.id) : api.clearHistory(),
    onSuccess: () => { setConfirm(null); setActionError(""); queryClient.invalidateQueries({ queryKey: ["history"] }); },
    onError: (cause) => { setConfirm(null); setActionError(cause instanceof ApiError ? cause.message : "Nothing was deleted. Try again."); },
  });
  const jobs = data?.jobs ?? [];
  return (
    <section className="history-section workspace-card" id="history-panel" aria-labelledby="history-title">
      <header className="section-heading"><div><p className="eyebrow">Your workspace</p><h2 id="history-title">Recent files</h2></div><span>{jobs.length ? `${jobs.length} file${jobs.length === 1 ? "" : "s"}` : ""}</span></header>
      {loading && <div className="history-loading" aria-label="Loading recent files"><span /><span /><span /></div>}
      {(error || actionError) && <div className="state-card" role="alert"><strong>Couldn’t load recent files</strong><p>{actionError || error?.message || "Check your connection and try again."}</p><button type="button" onClick={onRetry}>Try again</button></div>}
      {!loading && !error && !jobs.length && <div className="state-card"><span className="empty-icon" aria-hidden="true">PDF</span><strong>No files yet</strong><p>Your first document will appear here.</p></div>}
      {!loading && !error && jobs.length > 0 && <div className="history-table-wrap"><table id="history-table"><thead><tr><th>Document</th><th>Tool</th><th>Date</th><th>Pages</th><th>Status</th><th>Cost</th><th><span className="sr-only">Actions</span></th></tr></thead><tbody>{jobs.map((job) => <tr key={job.id}><td><button className="history-document" type="button" onClick={() => onOpen(job)}><strong>{job.output_filename || job.filename}</strong><small>{job.source_filenames.join(" · ")}</small></button></td><td>{labels[job.kind]}</td><td>{when(job.created_at)}</td><td>{job.kind === "pdf_to_docx" ? `${job.pages} source` : job.kind === "images_to_pdf" ? `${job.output_pages || job.pages} output` : `${job.pages} source · ${job.output_pages || "—"} output`}</td><td><span className={`history-status ${job.status}`}>{job.status}</span></td><td>{job.kind === "pdf_to_docx" ? job.cost_known ? `$${job.cost.toFixed(4)}` : "—" : "No charge"}</td><td className="history-row-actions"><button className="icon-action" type="button" onClick={() => onOpen(job)}>Open</button><DownloadMenu job={job} /><button className="icon-action" type="button" aria-label={`Delete ${job.filename}`} onClick={() => setConfirm({ mode: "job", job })}>Delete</button></td></tr>)}</tbody></table></div>}
      {jobs.length > 0 && <div className="history-actions"><button type="button" onClick={() => setConfirm({ mode: "all" })}>Clear completed history</button></div>}
      <ConfirmDialog open={Boolean(confirm)} title={confirm?.mode === "job" ? `Delete ${confirm.job.filename}?` : "Clear recent files?"} description={confirm?.mode === "job" ? "This removes the source and every stored output. This cannot be undone." : "Every file that is not currently running will be deleted with its outputs."} action={confirm?.mode === "all" ? "Clear history" : "Delete"} onClose={() => setConfirm(null)} onConfirm={() => remove.mutate()} />
    </section>
  );
}
