"use client";

import { FormEvent, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DownloadMenu } from "@/components/download-menu";
import { Progress } from "@/components/progress";
import { UploadZone } from "@/components/upload-zone";
import { api, ApiError } from "@/lib/api/client";
import type { ConfigDto, JobDto } from "@/lib/api/types";
import styles from "@/features/tools/tools.module.css";

type Field = "start" | "end";
type Errors = Partial<Record<Field, string>>;

export function SplitRangeForm({ job }: { job: JobDto }) {
  const client = useQueryClient();
  const [start, setStart] = useState(String(job.page_range?.start ?? 1));
  const [end, setEnd] = useState(String(job.page_range?.end ?? job.pages));
  const [errors, setErrors] = useState<Errors>({});
  const [serverError, setServerError] = useState("");
  const startRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLInputElement>(null);
  function validate(): Errors {
    const first = Number(start); const last = Number(end); const next: Errors = {};
    if (!Number.isInteger(first) || first < 1 || first > job.pages) next.start = `Enter a page from 1 to ${job.pages}.`;
    if (!Number.isInteger(last) || last < 1 || last > job.pages) next.end = `Enter a page from 1 to ${job.pages}.`;
    if (!next.start && !next.end && first > last) next.start = "Start page must not come after end page.";
    return next;
  }
  function validateField() { setErrors(validate()); }
  function change(field: Field, value: string) {
    if (field === "start") setStart(value); else setEnd(value);
    setErrors((current) => {
      const next = { ...current };
      delete next[field];
      // Changing either endpoint resolves any previously reported ordering
      // error; the new relationship is checked again on blur and submit.
      if (next.start === "Start page must not come after end page.") delete next.start;
      return next;
    });
  }
  const mutation = useMutation({
    mutationFn: () => api.split(job.id, Number(start), Number(end)),
    onSuccess: (updated) => { client.setQueryData(["job", job.id], updated); client.invalidateQueries({ queryKey: ["history"] }); setServerError(""); },
    onError: (cause) => setServerError(cause instanceof ApiError ? cause.message : "The range could not be extracted."),
  });
  function submit(event: FormEvent) {
    event.preventDefault(); const next = validate(); setErrors(next);
    if (next.start) { startRef.current?.focus(); return; }
    if (next.end) { endRef.current?.focus(); return; }
    mutation.mutate();
  }
  const count = Number.isInteger(Number(start)) && Number.isInteger(Number(end)) && Number(end) >= Number(start) ? Number(end) - Number(start) + 1 : 0;
  return (
    <form className={styles.splitCard} onSubmit={submit} noValidate aria-busy={mutation.isPending}>
      <fieldset className={styles.rangeFields}><legend>Page range</legend><label htmlFor="start-page">Start page<input ref={startRef} id="start-page" name="start-page" type="text" inputMode="numeric" pattern="[0-9]*" value={start} aria-invalid={Boolean(errors.start)} aria-describedby={errors.start ? "start-error" : undefined} onChange={(event) => change("start", event.target.value)} onBlur={validateField} />{errors.start && <span id="start-error" className={styles.fieldError}>{errors.start}</span>}</label><label htmlFor="end-page">End page<input ref={endRef} id="end-page" name="end-page" type="text" inputMode="numeric" pattern="[0-9]*" value={end} aria-invalid={Boolean(errors.end)} aria-describedby={errors.end ? "end-error" : undefined} onChange={(event) => change("end", event.target.value)} onBlur={validateField} />{errors.end && <span id="end-error" className={styles.fieldError}>{errors.end}</span>}</label></fieldset>
      <p className={styles.rangeSummary} aria-live="polite">{count ? `Pages ${start}–${end} · ${count} page${count === 1 ? "" : "s"}` : `Source · ${job.pages} pages`}</p>
      {serverError && <p className={styles.error} role="alert">{serverError}</p>}
      <div className={styles.primaryRow}><button className="primary" type="submit" disabled={mutation.isPending}>{mutation.isPending ? "Extracting…" : job.has_pdf ? "Regenerate PDF" : "Extract pages"}</button>{job.has_pdf && <DownloadMenu job={job} />}</div>
    </form>
  );
}

export function SplitPdfTool({ config, restoredJob, onCreated }: { config: ConfigDto; restoredJob?: JobDto; onCreated?: (job: JobDto) => void }) {
  const queryClient = useQueryClient();
  const [jobId, setJobId] = useState(restoredJob?.kind === "split_pdf" ? restoredJob.id : "");
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState("");
  const job = useQuery({ queryKey: ["job", jobId], queryFn: () => api.job(jobId), enabled: Boolean(jobId), initialData: restoredJob?.kind === "split_pdf" ? restoredJob : undefined });
  const upload = useMutation({
    mutationFn: (file: File) => api.uploadSplitPdf(file, setProgress),
    onSuccess: (created) => { setJobId(created.id); setProgress(100); queryClient.setQueryData(["job", created.id], created); queryClient.invalidateQueries({ queryKey: ["history"] }); onCreated?.(created); },
    onError: (cause) => { setError(cause instanceof ApiError ? cause.message : "The PDF could not be uploaded."); setProgress(null); },
  });
  function choose(files: File[]) {
    const file = files[0]; setError("");
    if (!file || !(file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf"))) { setError("Choose one PDF file."); return; }
    if (file.size > config.max_upload_mb * 1_048_576) { setError(`The PDF must be ${config.max_upload_mb} MB or smaller.`); return; }
    upload.mutate(file);
  }
  return (
    <>
      <header className={styles.toolHeader}><p className="eyebrow">Local · no charge</p><h1>Extract pages from a PDF.</h1><p>Upload a PDF, then choose one inclusive range. The source stays available for another range.</p></header>
      {!jobId && <UploadZone accept="application/pdf,.pdf" multiple={false} title="Drop your PDF here" buttonLabel="Choose PDF" hint={`One readable, unencrypted PDF · up to ${config.max_upload_mb} MB`} disabled={upload.isPending} onFiles={choose} />}
      {progress !== null && progress < 100 && <Progress value={progress} label={`Uploading… ${progress}%`} />}
      {error && <p className={styles.error} role="alert">{error}</p>}
      {jobId && job.data && <><section className={styles.resultCard}><h2>{job.data.filename}</h2><p className={styles.resultMeta}><span>{job.data.pages} source {job.data.pages === 1 ? "page" : "pages"}</span>{job.data.output_pages > 0 && <span>{job.data.output_pages} output {job.data.output_pages === 1 ? "page" : "pages"}</span>}<span>Local · no charge</span></p></section><SplitRangeForm job={job.data} /><div className={styles.primaryRow}><button type="button" onClick={() => { setJobId(""); setProgress(null); }}>Choose another PDF</button></div></>}
    </>
  );
}
