"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { WorkspaceContent } from "@/components/workspace-content";
import { ToolFrame } from "@/components/tool-frame";
import { DownloadMenu } from "@/components/download-menu";
import { Progress } from "@/components/progress";
import { UploadZone } from "@/components/upload-zone";
import { removeCancelled } from "@/lib/api/remove-cancelled";
import { api, ApiError } from "@/lib/api/client";
import type { ConfigDto, JobDto } from "@/lib/api/types";
import type { DockPanel } from "@/hooks/use-workspace-url";
import styles from "@/features/tools/tools.module.css";

const ACTIVE = new Set(["queued", "rendering", "transcribing", "building", "processing"]);

function statusLabel(job: JobDto) {
  if (job.status === "processing") return "Waiting for conversion service";
  if (job.status === "transcribing") return "Converting";
  if (job.status === "rendering") return "Preparing PDF";
  if (job.status === "building") return "Preparing downloads";
  return job.status;
}

function progress(job: JobDto) {
  if (job.status === "done") return 100;
  const base = ({ queued: 2, processing: 2, rendering: 8, transcribing: 15, building: 90 } as Record<string, number>)[job.status] ?? 0;
  return Math.min(98, base + (job.total ? Math.round(job.done / job.total * 70) : 0));
}

export function PdfToDocxTool({ config, restoredJob, onNew = () => undefined, onCompare = () => undefined, onOpen = () => undefined, onViewFiles = onOpen, panel = "setup", onRemoved = () => undefined, jobs: historyJobs = [] }: {
  config: ConfigDto; restoredJob?: JobDto; onNew?: () => void; onCompare?: (job: JobDto) => void; onOpen?: (job: JobDto) => void; onViewFiles?: (job: JobDto) => void;
  onRemoved?: (ids: string[]) => void; panel?: DockPanel; onPanel?: (panel: DockPanel) => void; jobs?: JobDto[];
}) {
  const client = useQueryClient();
  const [batchId, setBatchId] = useState<string | null>(restoredJob?.batch_id || null);
  const [uploadedJobs, setUploadedJobs] = useState<JobDto[]>(restoredJob && !restoredJob.batch_id ? [restoredJob] : []);
  const [formats, setFormats] = useState(restoredJob?.requested_formats.length ? restoredJob.requested_formats : ["docx"]);
  const [multiColumn, setMultiColumn] = useState(restoredJob?.multi_column ?? false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [error, setError] = useState("");
  const batch = useQuery({
    queryKey: ["batch", batchId], queryFn: () => api.batch(batchId!), enabled: Boolean(batchId),
    refetchInterval: (query) => query.state.data?.jobs.some((job) => ACTIVE.has(job.status)) ? 1_400 : false,
  });
  const singleJobId = !batchId && uploadedJobs.length === 1 ? uploadedJobs[0].id : null;
  const single = useQuery({
    queryKey: ["job", singleJobId], queryFn: () => api.job(singleJobId!), enabled: Boolean(singleJobId),
    initialData: singleJobId ? uploadedJobs[0] : undefined,
    refetchInterval: (query) => query.state.data && ACTIVE.has(query.state.data.status) ? 1_400 : false,
  });
  const conversionJobs = (batch.data?.jobs ?? (singleJobId && single.data ? [single.data] : uploadedJobs)).filter(job => job.status !== "cancelled");
  const [trackedKeys, setTrackedKeys] = useState<string[]>([]);
  const available = [...historyJobs.filter(job => !conversionJobs.some(selected => selected.id === job.id)), ...conversionJobs];
  const actionableKeys = available.filter(job => job.kind === "pdf_to_docx" && !["done", "cancelled"].includes(job.status)).map(job => job.batch_id || job.id);
  if (actionableKeys.some(key => !trackedKeys.includes(key))) setTrackedKeys([...new Set([...trackedKeys, ...actionableKeys])]);
  const previousGroups = [...new Set(trackedKeys)].map(key => available.filter(job => job.kind === "pdf_to_docx" && (job.batch_id || job.id) === key && job.status !== "cancelled")).filter(group => group.length && group[0].batch_id !== batchId && !group.some(job => conversionJobs.some(selected => selected.id === job.id)));
  async function forget(ids: string[]) {
    setError("");
    await removeCancelled(client, ids);
    setUploadedJobs(current => current.filter(job => !ids.includes(job.id)));
    if (conversionJobs.length && conversionJobs.every(job => ids.includes(job.id))) { setBatchId(null); setUploadProgress(null); }
    onRemoved(ids);
  }
  const uploadMutation = useMutation({
    mutationFn: (files: File[]) => api.uploadPdfBatch(files, setUploadProgress),
    onSuccess: (data) => { setBatchId(data.batch_id); setUploadedJobs(data.jobs); setUploadProgress(100); setError(data.rejected.map((item) => `${item.filename}: ${item.detail}`).join(" · ")); client.invalidateQueries({ queryKey: ["history"] }); },
    onError: (cause) => { setError(cause instanceof ApiError ? cause.message : "The PDFs could not be uploaded."); setUploadProgress(null); },
  });
  const action = useMutation({
    mutationFn: ({ target, name, saved }: { saved?: JobDto; target: string; name: "start" | "pause" | "resume" | "cancel" }) => api.batchAction(target, name, saved?.requested_formats.length ? saved.requested_formats : formats, saved?.multi_column ?? multiColumn),
    onSuccess: async (data, variables) => {
      if (variables.name === "cancel") await forget(available.filter(job => job.batch_id === data.batch_id && !data.jobs.some(remaining => remaining.id === job.id && remaining.status !== "cancelled")).map(job => job.id));
      client.setQueryData(["batch", data.batch_id], {...data, jobs: data.jobs.filter(job => job.status !== "cancelled")}); client.invalidateQueries({ queryKey: ["history"] });
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "That action failed."),
  });
  const jobAction = useMutation({
    mutationFn: ({ job, name }: { job: JobDto; name: "pause" | "resume" | "cancel" }) => name === "resume"
      ? api.jobAction(job.id, name, conversionJobs.some(selected => selected.id === job.id) ? formats : job.requested_formats, conversionJobs.some(selected => selected.id === job.id) ? multiColumn : job.multi_column)
      : api.jobAction(job.id, name),
    onSuccess: async (updated, variables) => {
      if (variables.name === "cancel") { await forget([updated.id]); client.invalidateQueries({queryKey: ["history"]}); return; }
      client.setQueryData(["job", updated.id], updated);
      setUploadedJobs((current) => current.map((job) => job.id === updated.id ? updated : job));
      if (updated.batch_id) client.invalidateQueries({queryKey: ["batch", updated.batch_id]});
      client.invalidateQueries({ queryKey: ["history"] });
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "That action failed."),
  });
  const jobStart = useMutation({
    mutationFn: (job: JobDto) => api.startJob(job.id, conversionJobs.some(selected => selected.id === job.id) ? formats : job.requested_formats.length ? job.requested_formats : formats, conversionJobs.some(selected => selected.id === job.id) ? multiColumn : job.multi_column),
    onSuccess: (updated) => {
      client.setQueryData(["job", updated.id], updated);
      setUploadedJobs((current) => current.map((job) => job.id === updated.id ? updated : job));
      if (updated.batch_id) client.invalidateQueries({queryKey: ["batch", updated.batch_id]});
      client.invalidateQueries({ queryKey: ["history"] });
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "That file could not be started."),
  });
  const requestable = useMemo(() => config.mathpix_formats.filter((entry) => entry.requestable), [config]);

  function chooseFiles(files: File[]) {
    setError("");
    const pdfs = files.filter((file) => file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf"));
    if (!pdfs.length || pdfs.length !== files.length) { setError("Choose PDF files only."); return; }
    if (pdfs.length > config.batch_max_files) { setError(`Choose at most ${config.batch_max_files} PDFs.`); return; }
    if (config.max_upload_mb && pdfs.some((file) => file.size > config.max_upload_mb * 1_048_576)) { setError(`Each PDF must be ${config.max_upload_mb} MB or smaller.`); return; }
    uploadMutation.mutate(pdfs);
  }

  function renderBatch(group: JobDto[], groupBatchId: string | null, current: boolean) {
    const isBatch = group.length > 1;
    const loneJob = group[0];
    return <section className="job-card batch-panel" aria-label={isBatch ? "Batch conversion" : "File conversion"}>
          <div className="job-header"><span className="file-icon" aria-hidden="true">PDF</span><div className="job-copy"><h2>{isBatch ? "Batch conversion" : loneJob.filename}</h2><p>{group.length} file{isBatch ? "s" : ""} · {group.reduce((sum, job) => sum + job.pages, 0)} pages</p></div>{current ? <button type="button" onClick={() => { setBatchId(null); setUploadedJobs([]); setUploadProgress(null); onNew(); }}>New conversion</button> : isBatch ? <button type="button" onClick={() => onViewFiles(loneJob)}>View files</button> : <button type="button" onClick={() => loneJob.status === "done" ? onCompare(loneJob) : onViewFiles(loneJob)}>{loneJob.status === "done" ? "Open" : "View file"}</button>}</div>
          <ul className="batch-list" aria-label={isBatch ? "Files in this batch" : "File conversion"}>{group.map((job) => <li className="batch-row" key={job.id}><div className="batch-row-main"><strong>{job.filename}</strong><small>{job.pages} pages</small></div><span className={`status-badge ${job.status === "done" ? "complete" : job.status === "error" ? "error" : ACTIVE.has(job.status) ? "working" : ""}`}>{statusLabel(job)}</span>{(ACTIVE.has(job.status) || job.status === "done") && <div className="progress-track batch-row-progress"><i style={{ width: `${progress(job)}%` }} /></div>}<div className="row-actions">{job.status === "done" && <button className="primary" type="button" onClick={() => onCompare(job)}>Open</button>}{job.status === "ready" && <><button className="primary" type="button" disabled={!formats.length || !config.mathpix_key_configured || jobStart.isPending} onClick={() => jobStart.mutate(job)}>Convert</button><button type="button" onClick={() => jobAction.mutate({ job, name: "pause" })}>Pause</button></>}{job.status === "queued" && <button type="button" disabled={jobAction.isPending} onClick={() => jobAction.mutate({ job, name: "pause" })}>Pause</button>}{job.status === "paused" && <button type="button" onClick={() => jobAction.mutate({ job, name: "resume" })}>Resume</button>}{(job.status === "error") && job.has_source && <button type="button" disabled={!formats.length || !config.mathpix_key_configured || jobStart.isPending} onClick={() => jobStart.mutate(job)}>Retry</button>}{(ACTIVE.has(job.status) || job.status === "ready" || job.status === "paused") && <button type="button" onClick={() => jobAction.mutate({ job, name: "cancel" })}>Cancel</button>}<DownloadMenu job={job} /></div>{job.error && <p className="batch-row-error" role="status">{job.error}</p>}</li>)}</ul>{isBatch && groupBatchId && <div className={styles.primaryRow}>{!current && group.some(job => job.status === "ready") && <button type="button" disabled={!config.mathpix_key_configured || action.isPending} onClick={() => action.mutate({target: groupBatchId, name: "start", saved: group[0]})}>Convert all</button>}{group.some((job) => job.status === "ready" || job.status === "queued") && <button type="button" onClick={() => action.mutate({ target: groupBatchId, saved: current ? undefined : group[0], name: "pause" })}>Pause all</button>}{group.some((job) => job.status === "paused") && <button type="button" onClick={() => action.mutate({ target: groupBatchId, saved: current ? undefined : group[0], name: "resume" })}>Resume all</button>}{group.some((job) => !["done", "error", "cancelled"].includes(job.status)) && <button type="button" onClick={() => action.mutate({ target: groupBatchId, saved: current ? undefined : group[0], name: "cancel" })}>Cancel all</button>}{group.some(job => job.status === "done" && job.has_package) && <a className="download-link" href={api.batchPackageUrl(groupBatchId)}>Download batch ZIP</a>}</div>}</section>;
  }
  const canvas = <>
      {!config.mathpix_key_configured && <div className="warn" role="status"><strong>Conversion is unavailable</strong><span>The conversion service is not configured on this server.</span></div>}
      {conversionJobs.length === 0 && <UploadZone accept="application/pdf,.pdf" multiple title="Drop your PDF here" buttonLabel="Choose PDF" hint={`PDF only · ${config.max_upload_mb ? `up to ${config.max_upload_mb} MB each` : "no size limit"} · ${config.batch_max_files} files`} disabled={uploadMutation.isPending || Boolean(batchId)} onFiles={chooseFiles} />}
      {uploadProgress !== null && uploadProgress < 100 && <Progress value={uploadProgress} label={uploadProgress < 100 ? `Uploading… ${uploadProgress}%` : "Upload complete"} />}
      {error && <p className={styles.error} role="alert">{error}</p>}
      {conversionJobs.length > 0 && renderBatch(conversionJobs, batchId, true)}
      {previousGroups.length > 0 && <section className="previous-conversions" aria-label="Previous conversions"><h2>Previous conversions</h2>{previousGroups.sort((a, b) => b[0].created_at.localeCompare(a[0].created_at)).map(group => <div key={group[0].batch_id || group[0].id}>{renderBatch(group, group[0].batch_id || null, false)}</div>)}</section>}
    </>;
  const setup = <div className={styles.setupPanel}><div><p className="eyebrow">Conversion setup</p><h2>Output</h2><p>Choose the editable formats and page layout to create.</p></div><details className="format-disclosure"><summary>Output formats · {formats.map(format => format.toUpperCase()).join(", ") || "None selected"}</summary><fieldset className={styles.formatGrid}><legend className="field-label">Output formats</legend>{requestable.map((format) => <label className={styles.formatOption} key={format.ext}><input type="checkbox" checked={formats.includes(format.ext)} onChange={(event) => setFormats((current) => event.target.checked ? [...current, format.ext] : current.filter((item) => item !== format.ext))} /><span>{format.ext.toUpperCase()}</span></label>)}</fieldset></details><label className={styles.layoutOption}><input type="checkbox" checked={multiColumn} onChange={(event) => setMultiColumn(event.target.checked)} /><span><strong>Match source page columns</strong><small>For books and papers set in two columns.</small></span></label></div>;
  const actionFooter = <><p>{conversionJobs.length} files · {formats.length} selected formats</p>{conversionJobs.length > 1 && batchId && conversionJobs.some((job) => job.status === "ready") && <button className="primary" type="button" onClick={() => action.mutate({ target: batchId, name: "start" })} disabled={action.isPending || !formats.length || !config.mathpix_key_configured}>Convert all</button>}</>;
  return <ToolFrame title="PDF to DOCX" description="Convert PDFs into editable documents and compare the result beside the source." canvas={<WorkspaceContent panel={panel} kind="pdf_to_docx" jobs={historyJobs} selected={restoredJob?.kind === "pdf_to_docx" ? restoredJob : conversionJobs[0]} onOpen={onOpen} onViewFiles={onViewFiles}>{canvas}</WorkspaceContent>} settings={setup} actionFooter={actionFooter} />;
}
