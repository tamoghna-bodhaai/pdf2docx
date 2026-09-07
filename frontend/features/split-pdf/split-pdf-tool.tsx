/* eslint-disable @next/next/no-img-element -- authenticated, on-demand page previews cannot use the static optimizer */
"use client";

import { visibleArtifacts } from "@/lib/api/artifacts";
import { FormEvent, KeyboardEvent, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { WorkspaceContent } from "@/components/workspace-content";
import { ToolFrame } from "@/components/tool-frame";
import { DeleteJobButton } from "@/components/delete-job-button";
import { Progress } from "@/components/progress";
import { UploadZone } from "@/components/upload-zone";
import { api, ApiError } from "@/lib/api/client";
import type { DockPanel } from "@/hooks/use-workspace-url";
import type { ConfigDto, JobDto, PageRange } from "@/lib/api/types";
import styles from "@/features/tools/tools.module.css";

interface EditableRange { id: string; start: string; end: string }

function initialRanges(job: JobDto): EditableRange[] {
  const source = job.page_ranges.length ? job.page_ranges : job.page_range ? [job.page_range] : [{ start: 1, end: job.pages }];
  return source.map((range, index) => ({ id: `range-${index}-${range.start}-${range.end}`, start: String(range.start), end: String(range.end) }));
}

function parsed(range: EditableRange): PageRange | null {
  const start = Number(range.start); const end = Number(range.end);
  return Number.isInteger(start) && Number.isInteger(end) ? { start, end } : null;
}

function useSplitController(job: JobDto) {
  const client = useQueryClient();
  const [ranges, setRanges] = useState<EditableRange[]>(() => initialRanges(job));
  const [localMerge, setLocalMerge] = useState(job.merge_ranges);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [serverError, setServerError] = useState("");
  const [dragged, setDragged] = useState<number | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const inputRefs = useRef<Record<string, HTMLInputElement | null>>({});
  const orderSnapshot = useRef<EditableRange[] | null>(null);
  const merge = ranges.length > 1 && localMerge;
  const setMerge = setLocalMerge;
  const [allPages, setAllPages] = useState(false);
  function validate() {
    const next: Record<string, string> = {};
    ranges.forEach((range) => {
      const value = parsed(range);
      if (!value || value.start < 1 || value.start > job.pages) next[`${range.id}-start`] = `Enter a page from 1 to ${job.pages}.`;
      if (!value || value.end < 1 || value.end > job.pages) next[`${range.id}-end`] = `Enter a page from 1 to ${job.pages}.`;
      if (value && value.start > value.end) next[`${range.id}-start`] = "Start page must not come after end page.";
    });
    return next;
  }
  const mutation = useMutation({
    mutationFn: () => api.split(job.id, ranges.map(parsed).filter((range): range is PageRange => Boolean(range)), merge),
    onSuccess: (updated) => { client.setQueryData(["job", job.id], updated); client.invalidateQueries({ queryKey: ["history"] }); setServerError(""); document.dispatchEvent(new CustomEvent("open-settings")); },
    onError: (cause) => setServerError(cause instanceof ApiError ? cause.message : "The ranges could not be extracted."),
  });
  function submit(event: FormEvent) {
    event.preventDefault(); if (mutation.isPending) return; const next = validate(); setErrors(next);
    const [firstKey] = Object.keys(next);
    if (firstKey) { inputRefs.current[firstKey]?.focus(); return; }
    mutation.mutate();
  }
  function update(id: string, field: "start" | "end", value: string) {
    if (mutation.isPending) return;
    setRanges((current) => current.map((range) => range.id === id ? { ...range, [field]: value } : range));
    setErrors((current) => { const next = { ...current }; delete next[`${id}-${field}`]; delete next[`${id}-start`]; return next; });
  }
  function move(from: number, to: number) {
    if (mutation.isPending || to < 0 || to >= ranges.length || from === to) return;
    setRanges((current) => { const next = [...current]; const [item] = next.splice(from, 1); if (item) next.splice(to, 0, item); return next; });
    setAnnouncement(`Range ${from + 1} moved to position ${to + 1}.`);
  }
  function beginReorder(index: number) {
    if (mutation.isPending) return;
    if (!orderSnapshot.current) orderSnapshot.current = ranges;
    setDragged(index);
  }
  function finishReorder() {
    orderSnapshot.current = null;
    setDragged(null);
  }
  function cancelReorder() {
    if (orderSnapshot.current) setRanges(orderSnapshot.current);
    orderSnapshot.current = null;
    setDragged(null);
    setAnnouncement("Range reordering cancelled.");
  }
  function onReorderKey(event: KeyboardEvent, index: number) {
    if (!["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault(); move(index, index + (["ArrowUp", "ArrowLeft"].includes(event.key) ? -1 : 1));
  }
  function addRange() {
    const previous = parsed(ranges[ranges.length - 1]);
    const start = previous && previous.end < job.pages ? previous.end + 1 : 1;
    setRanges((current) => [...current, { id: `range-${Date.now()}-${current.length}`, start: String(start), end: String(job.pages) }]);
  }
  const valid = ranges.map(parsed).filter((range): range is PageRange => Boolean(range && range.start >= 1 && range.end <= job.pages && range.start <= range.end));
  const seen = new Set<number>(); const duplicates = new Set<number>();
  valid.forEach(({ start, end }) => { for (let page = start; page <= end; page += 1) { if (seen.has(page)) duplicates.add(page); seen.add(page); } });
  const total = valid.reduce((sum, range) => sum + Math.max(0, range.end - range.start + 1), 0);
  function thumbnail(page: number) { return <figure className={styles.pdfThumbnail} key={page}><div><img src={api.pageUrl(job.id, page, 160)} loading="lazy" alt={`Page ${page} preview`} onError={(event) => { event.currentTarget.hidden = true; event.currentTarget.parentElement?.classList.add(styles.previewError); }} /></div><figcaption>Page {page}</figcaption></figure>; }
  const canvas = <><div className={styles.boardSummary}><strong>Output previews</strong><button type="button" aria-pressed={allPages} onClick={() => setAllPages(value => !value)}>{allPages ? "Grouped outputs" : "All pages"}</button></div>
    {allPages ? <div className={styles.thumbnailGrid}>{Array.from({length: job.pages}, (_, index) => thumbnail(index + 1))}</div> : <div className={styles.outputGroups}>{ranges.map((range, index) => {
      const value = parsed(range); const valid = value && value.start >= 1 && value.end <= job.pages && value.start <= value.end;
      return <section className={styles.outputGroup} key={range.id}><button type="button" onClick={() => { document.getElementById("context-dock")?.dispatchEvent(new CustomEvent("open-settings", {bubbles: true})); window.setTimeout(() => inputRefs.current[`${range.id}-start`]?.focus(), 50); }}>Range {index + 1}{valid ? ` · Pages ${value.start}–${value.end}` : " · Invalid range"}</button>{valid ? <div className={styles.groupPages}>{thumbnail(value.start)}{value.end !== value.start && <><span aria-label="through">…</span>{thumbnail(value.end)}</>}</div> : <p role="status">Enter a valid range to preview this output.</p>}</section>;
    })}</div>}

  </>;
  const editor = <form id={`split-ranges-${job.id}`} className={styles.rangeWorkbench} onSubmit={submit} noValidate aria-busy={mutation.isPending}><fieldset disabled={mutation.isPending} className={styles.controllerFields}>
    <section className={styles.rangeEditor} aria-labelledby="range-editor-title"><div className={styles.rangeEditorHeading}><div><p className="eyebrow">Output order</p><h2 id="range-editor-title">Page ranges</h2></div><button type="button" onClick={addRange}>Add range</button></div>
      {duplicates.size > 0 && <p className={styles.duplicateWarning} role="status">{duplicates.size} page{duplicates.size === 1 ? " is" : "s are"} selected more than once. Duplicates will be preserved.</p>}
      {ranges.length === 1 && valid[0] && <p className={styles.rangeSummary} aria-live="polite">Pages {valid[0].start}–{valid[0].end} · {total} page{total === 1 ? "" : "s"}</p>}
      <ol className={styles.rangeList} onKeyDown={(event) => { if (event.key === "Escape" && dragged !== null) { event.preventDefault(); cancelReorder(); } }} onPointerMove={(event) => { if (dragged === null) return; const target = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>("[data-range-index]"); const index = Number(target?.dataset.rangeIndex); if (Number.isInteger(index) && index !== dragged) { move(dragged, index); setDragged(index); } }} onPointerUp={finishReorder} onPointerCancel={cancelReorder}>{ranges.map((range, index) => {
        const value = parsed(range); const count = value && value.end >= value.start ? value.end - value.start + 1 : 0;
        const startError = errors[`${range.id}-start`]; const endError = errors[`${range.id}-end`];
        return <li className={styles.rangeRow} data-range-index={index} draggable={!mutation.isPending} onDragStart={() => beginReorder(index)} onDragOver={(event) => event.preventDefault()} onDragEnter={() => { if (dragged !== null) move(dragged, index); setDragged(index); }} onDrop={finishReorder} onDragEnd={() => { if (orderSnapshot.current) cancelReorder(); }} key={range.id}><button className={styles.dragHandle} type="button" aria-label={`Reorder range ${index + 1}. Use arrow keys to move.`} onPointerDown={(event) => { event.currentTarget.setPointerCapture?.(event.pointerId); beginReorder(index); }} onPointerUp={finishReorder} onPointerCancel={cancelReorder} onKeyDown={(event) => onReorderKey(event, index)}>⠿</button><strong>Range {index + 1}</strong><label>Start page<input ref={(node) => { inputRefs.current[`${range.id}-start`] = node; }} aria-label={index === 0 ? "Start page" : `Range ${index + 1} start page`} type="text" inputMode="numeric" value={range.start} aria-invalid={Boolean(startError)} aria-describedby={startError ? `${range.id}-error` : undefined} onChange={(event) => update(range.id, "start", event.target.value)} onBlur={() => setErrors(validate())} /></label><span aria-hidden="true">–</span><label>End page<input ref={(node) => { inputRefs.current[`${range.id}-end`] = node; }} aria-label={index === 0 ? "End page" : `Range ${index + 1} end page`} type="text" inputMode="numeric" value={range.end} aria-invalid={Boolean(endError)} aria-describedby={endError ? `${range.id}-error` : undefined} onChange={(event) => update(range.id, "end", event.target.value)} onBlur={() => setErrors(validate())} /></label><small>{count} page{count === 1 ? "" : "s"}</small><details className={styles.cardMenu}><summary aria-label={`Actions for range ${index + 1}`}>•••</summary><div><button type="button" disabled={index === 0} onClick={() => move(index, index - 1)}>Move earlier</button><button type="button" disabled={index === ranges.length - 1} onClick={() => move(index, index + 1)}>Move later</button><button type="button" disabled={ranges.length === 1} onClick={() => setRanges((current) => current.filter((item) => item.id !== range.id))}>Remove</button></div></details>{(startError || endError) && <span id={`${range.id}-error`} className={styles.fieldError}>{startError || endError}</span>}</li>;
      })}</ol>
      {serverError && <p className={styles.error} role="alert">{serverError}</p>}<p className="sr-only" aria-live="polite">{announcement}</p>
    </section>
    {ranges.length > 1 && <label className={styles.mergeToggle}><input type="checkbox" checked={merge} onChange={(event) => setMerge(event.target.checked)} /><span><strong>Merge ranges into one PDF</strong><small>{merge ? "One PDF in range order" : "Individual PDFs plus a Download All ZIP"}</small></span></label>}
    </fieldset></form>;
  const artifacts = visibleArtifacts(job);
  const pdfArtifacts = artifacts.filter(item => item.media_type === "application/pdf");
  const downloads = [...artifacts].sort((a, b) => Number(b.media_type === "application/zip") - Number(a.media_type === "application/zip"));
  const footer = <><div className="output-downloads">{downloads.map(artifact => <a className={`download-link ${artifacts.length === 1 || artifact.media_type === "application/zip" ? "primary" : ""}`} download href={api.artifactUrl(job.id, artifact.key)} key={artifact.key}>{artifact.media_type === "application/zip" ? "Download All ZIP" : pdfArtifacts.length === 1 ? "Download split PDF" : `Download PDF ${pdfArtifacts.findIndex(item => item.key === artifact.key) + 1}`}</a>)}</div><p>{merge ? 1 : ranges.length} output{!merge && ranges.length !== 1 ? "s" : ""} · {total} pages</p><button className={job.has_pdf ? "secondary" : "primary"} type="submit" form={`split-ranges-${job.id}`} disabled={mutation.isPending}>{mutation.isPending ? "Creating files…" : job.has_pdf ? "Regenerate PDF" : "Split PDF"}</button></>;
  return {canvas, editor, footer};
}

export function SplitRangeForm({job}: {job: JobDto}) {
  const controller = useSplitController(job);
  return <>{controller.canvas}{controller.editor}{controller.footer}</>;
}

function SplitWorkspace({job, panel, jobs, onOpen, onReset}: {job: JobDto; panel: DockPanel; jobs: JobDto[]; onOpen: (job: JobDto) => void; onReset: () => void}) {
  const controller = useSplitController(job);
  return <ToolFrame title="Split PDF" description="Select, reorder, and package as many page ranges as you need." canvas={<WorkspaceContent panel={panel} kind="split_pdf" jobs={jobs} selected={job} onOpen={onOpen}>{controller.canvas}</WorkspaceContent>} settings={<div className={styles.setupPanel}><div><h2>{job.filename}</h2><p>{job.pages} source pages</p></div>{controller.editor}<DeleteJobButton job={job} onDeleted={onReset} /><button type="button" onClick={onReset}>Choose another PDF</button></div>} actionFooter={controller.footer} />;
}

export function SplitPdfTool({ config, restoredJob, onCreated, onDeleted, panel = "setup", jobs = [], onOpen = () => undefined }: {
  config: ConfigDto; restoredJob?: JobDto; onCreated?: (job: JobDto) => void; onDeleted?: () => void;
  panel?: DockPanel; onPanel?: (panel: DockPanel) => void; jobs?: JobDto[]; onOpen?: (job: JobDto) => void;
}) {
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
    if (config.max_upload_mb && file.size > config.max_upload_mb * 1_048_576) { setError(`The PDF must be ${config.max_upload_mb} MB or smaller.`); return; }
    upload.mutate(file);
  }
  const currentJob = job.data;
  if (currentJob) return <SplitWorkspace key={currentJob.id} job={currentJob} panel={panel} jobs={jobs} onOpen={onOpen} onReset={() => {setJobId(""); setProgress(null); onDeleted?.();}} />;
  const canvas = <><UploadZone accept="application/pdf,.pdf" multiple={false} title="Drop your PDF here" buttonLabel="Choose PDF" hint={`One readable, unencrypted PDF · ${config.max_upload_mb ? `up to ${config.max_upload_mb} MB` : "no size limit"}`} disabled={upload.isPending} onFiles={choose} />{progress !== null && progress < 100 && <Progress value={progress} label={`Uploading… ${progress}%`} />}{error && <p className={styles.error} role="alert">{error}</p>}</>;
  return <ToolFrame title="Split PDF" description="Select, reorder, and package as many page ranges as you need." canvas={<WorkspaceContent panel={panel} kind="split_pdf" jobs={jobs} onOpen={onOpen}>{canvas}</WorkspaceContent>} settings={<p>Upload a PDF to build one or more page ranges.</p>} actionFooter={<button className="primary" disabled>Split PDF</button>} />;
}
