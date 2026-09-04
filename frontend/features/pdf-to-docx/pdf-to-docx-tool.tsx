"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { ToolFrame } from "@/components/tool-frame";
import { DownloadMenu } from "@/components/download-menu";
import { Progress } from "@/components/progress";
import { UploadZone } from "@/components/upload-zone";
import { api, ApiError } from "@/lib/api/client";
import type { ConfigDto, JobDto } from "@/lib/api/types";
import type { DockPanel } from "@/hooks/use-workspace-url";
import styles from "@/features/tools/tools.module.css";

const ACTIVE = new Set(["queued", "rendering", "transcribing", "building", "processing"]);

function progress(job: JobDto) {
  if (job.status === "done") return 100;
  const base = ({ queued: 2, rendering: 8, transcribing: 15, building: 90 } as Record<string, number>)[job.status] ?? 0;
  return Math.min(98, base + (job.total ? Math.round(job.done / job.total * 70) : 0));
}

export function PdfToDocxTool({ config, restoredJob, onOpen = () => undefined, panel = "setup", onPanel = () => undefined, jobs: historyJobs = [] }: {
  config: ConfigDto; restoredJob?: JobDto; onOpen?: (job: JobDto) => void;
  panel?: DockPanel; onPanel?: (panel: DockPanel) => void; jobs?: JobDto[];
}) {
  const client = useQueryClient();
  const [batchId, setBatchId] = useState<string | null>(restoredJob?.batch_id || null);
  const [uploadedJobs, setUploadedJobs] = useState<JobDto[]>(restoredJob && !restoredJob.batch_id ? [restoredJob] : []);
  const [formats, setFormats] = useState(["docx"]);
  const [multiColumn, setMultiColumn] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [error, setError] = useState("");
  const batch = useQuery({
    queryKey: ["batch", batchId], queryFn: () => api.batch(batchId!), enabled: Boolean(batchId),
    refetchInterval: (query) => query.state.data?.jobs.some((job) => ACTIVE.has(job.status)) ? 1_400 : false,
  });
  const conversionJobs = batch.data?.jobs ?? uploadedJobs;
  const uploadMutation = useMutation({
    mutationFn: (files: File[]) => api.uploadPdfBatch(files, setUploadProgress),
    onSuccess: (data) => { setBatchId(data.batch_id); setUploadedJobs(data.jobs); setUploadProgress(100); setError(data.rejected.map((item) => `${item.filename}: ${item.detail}`).join(" · ")); client.invalidateQueries({ queryKey: ["history"] }); },
    onError: (cause) => { setError(cause instanceof ApiError ? cause.message : "The PDFs could not be uploaded."); setUploadProgress(null); },
  });
  const action = useMutation({
    mutationFn: ({ target, name }: { target: string; name: "start" | "pause" | "resume" | "cancel" }) => api.batchAction(target, name, formats, multiColumn),
    onSuccess: (data) => { client.setQueryData(["batch", data.batch_id], data); client.invalidateQueries({ queryKey: ["history"] }); },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "That action failed."),
  });
  const jobAction = useMutation({
    mutationFn: ({ job, name }: { job: JobDto; name: "pause" | "resume" | "cancel" }) => api.jobAction(job.id, name),
    onSuccess: (updated) => {
      setUploadedJobs((current) => current.map((job) => job.id === updated.id ? updated : job));
      if (updated.batch_id) batch.refetch();
      client.invalidateQueries({ queryKey: ["history"] });
    },
    onError: (cause) => setError(cause instanceof ApiError ? cause.message : "That action failed."),
  });
  const jobStart = useMutation({
    mutationFn: (job: JobDto) => api.startJob(job.id, formats, multiColumn),
    onSuccess: (updated) => {
      setUploadedJobs((current) => current.map((job) => job.id === updated.id ? updated : job));
      if (updated.batch_id) batch.refetch();
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

  const canvas = <>
      {!config.mathpix_key_configured && <div className="warn" role="status"><strong>Conversion is unavailable</strong><span>The conversion service is not configured on this server.</span></div>}
      <UploadZone accept="application/pdf,.pdf" multiple title="Drop your PDF here" buttonLabel="Choose PDF" hint={`PDF only · ${config.max_upload_mb ? `up to ${config.max_upload_mb} MB each` : "no size limit"} · ${config.batch_max_files} files`} disabled={uploadMutation.isPending || Boolean(batchId)} onFiles={chooseFiles} />
      {uploadProgress !== null && <Progress value={uploadProgress} label={uploadProgress < 100 ? `Uploading… ${uploadProgress}%` : "Upload complete"} />}
      {error && <p className={styles.error} role="alert">{error}</p>}
      {conversionJobs.length > 0 && (
        <section className="job-card batch-panel" aria-label="Batch conversion">
          <div className="job-header"><span className="file-icon" aria-hidden="true">PDF</span><div className="job-copy"><h2>{conversionJobs.length === 1 ? conversionJobs[0].filename : "Batch conversion"}</h2><p>{conversionJobs.length} file{conversionJobs.length === 1 ? "" : "s"} · {conversionJobs.reduce((sum, job) => sum + job.pages, 0)} pages</p></div><button type="button" onClick={() => { setBatchId(null); setUploadedJobs([]); setUploadProgress(null); }}>New conversion</button></div>
          <ul className="batch-list" aria-label="Files in this batch">{conversionJobs.map((job) => <li className="batch-row" key={job.id}><div className="batch-row-main"><strong>{job.filename}</strong><small>{job.pages} pages · {job.status}</small></div><span className={`status-badge ${job.status === "done" ? "complete" : job.status === "error" ? "error" : ACTIVE.has(job.status) ? "working" : ""}`}>{job.status}</span>{(ACTIVE.has(job.status) || job.status === "done") && <div className="progress-track batch-row-progress"><i style={{ width: `${progress(job)}%` }} /></div>}<div className="row-actions">{job.status === "done" && <button className="primary" type="button" onClick={() => onOpen(job)}>Open</button>}{job.status === "ready" && <><button className="primary" type="button" disabled={!config.mathpix_key_configured || jobStart.isPending} onClick={() => jobStart.mutate(job)}>Convert</button><button type="button" onClick={() => jobAction.mutate({ job, name: "pause" })}>Pause</button></>}{job.status === "paused" && <button type="button" onClick={() => jobAction.mutate({ job, name: "resume" })}>Resume</button>}{(job.status === "error" || job.status === "cancelled") && job.has_source && <button type="button" disabled={!config.mathpix_key_configured || jobStart.isPending} onClick={() => jobStart.mutate(job)}>Retry</button>}{ACTIVE.has(job.status) && <button type="button" onClick={() => jobAction.mutate({ job, name: "cancel" })}>Cancel</button>}<DownloadMenu job={job} /></div></li>)}</ul>
        </section>
      )}
    </>;
  const setup = <div className={styles.setupPanel}><div><p className="eyebrow">Conversion setup</p><h2>Output</h2><p>Choose the editable formats and page layout to create.</p></div><fieldset className={styles.formatGrid}><legend className="field-label">Output formats</legend>{requestable.map((format) => <label className={styles.formatOption} key={format.ext}><input type="checkbox" checked={formats.includes(format.ext)} onChange={(event) => setFormats((current) => event.target.checked ? [...current, format.ext] : current.filter((item) => item !== format.ext))} /><span>{format.ext.toUpperCase()}</span></label>)}</fieldset><label className={styles.layoutOption}><input type="checkbox" checked={multiColumn} onChange={(event) => setMultiColumn(event.target.checked)} /><span><strong>Match source page columns</strong><small>For books and papers set in two columns.</small></span></label><div className={styles.primaryRow}>{batchId && <>{conversionJobs.some((job) => job.status === "ready") && <button className="primary" type="button" onClick={() => action.mutate({ target: batchId, name: "start" })} disabled={action.isPending || !config.mathpix_key_configured}>Convert all</button>}{conversionJobs.some((job) => job.status === "ready" || job.status === "queued") && <button type="button" onClick={() => action.mutate({ target: batchId, name: "pause" })}>Pause all</button>}{conversionJobs.some((job) => job.status === "paused") && <button type="button" onClick={() => action.mutate({ target: batchId, name: "resume" })}>Resume all</button>}{conversionJobs.some((job) => !["done", "error", "cancelled"].includes(job.status)) && <button type="button" onClick={() => action.mutate({ target: batchId, name: "cancel" })}>Cancel all</button>}{batch.data?.package_ready && <a className="download-link" href={api.batchPackageUrl(batchId)}>Download batch ZIP</a>}</>}</div></div>;
  return <ToolFrame panel={panel} onPanel={onPanel} kind="pdf_to_docx" eyebrow="Conversion workspace" title="PDF to DOCX" description="Convert PDFs into editable documents and compare the result beside the source." canvas={canvas} setup={setup} jobs={historyJobs} selected={restoredJob?.kind === "pdf_to_docx" ? restoredJob : conversionJobs[0]} onOpen={onOpen} />;
}
