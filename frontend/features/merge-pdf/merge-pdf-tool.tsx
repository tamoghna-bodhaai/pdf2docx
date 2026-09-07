/* eslint-disable @next/next/no-img-element -- authenticated local PDF previews */
"use client";

import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { WorkspaceContent } from "@/components/workspace-content";
import { ToolFrame } from "@/components/tool-frame";
import { UploadZone } from "@/components/upload-zone";
import { DeleteJobButton } from "@/components/delete-job-button";
import { Progress } from "@/components/progress";
import { api } from "@/lib/api/client";
import type { ConfigDto, JobDto, MergeSource } from "@/lib/api/types";
import type { DockPanel } from "@/hooks/use-workspace-url";
import styles from "@/features/tools/tools.module.css";

function Preview({ jobId, source }: { jobId: string; source: MergeSource }) {
  const [failed, setFailed] = useState(false);
  return <div className={styles.previewArea}><div className={`${styles.pageSilhouette} ${styles.portrait}`}>{failed ? <span>PDF · Preview unavailable</span> : <img src={api.mergePreviewUrl(jobId, source.id)} alt={`First page of ${source.filename}`} onError={() => setFailed(true)} />}</div></div>;
}

export function MergePdfTool({ config, restoredJob, onCreated, onDeleted = () => undefined, panel = "setup", jobs = [], onOpen = () => undefined }: {
  config: ConfigDto; restoredJob?: JobDto; onCreated?: (job: JobDto) => void; onDeleted?: () => void;
  panel?: DockPanel; jobs?: JobDto[]; onOpen?: (job: JobDto) => void;
}) {
  const cache = useQueryClient();
  const [job, setJob] = useState(restoredJob);
  const [sources, setSources] = useState(restoredJob?.merge_sources ?? []);
  const [pending, setPending] = useState("");
  const busy = useRef(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [announcement, setAnnouncement] = useState("");
  const [dragging, setDragging] = useState(false);
  const drag = useRef<{ id: string; before: MergeSource[]; current: MergeSource[] } | null>(null);
  const handles = useRef(new Map<string, HTMLButtonElement>());
  const limit = config.merge_max_files ?? 30;
  const sizeLimit = config.merge_max_upload_mb ?? 50;
  const processing = job?.status === "processing";
  const disabled = Boolean(pending) || dragging || processing;
  const recovery = useQuery({
    queryKey: ["merge-recovery", job?.id],
    enabled: Boolean(job && processing && !pending),
    queryFn: async () => { const updated = await api.job(job!.id); accept(updated); return updated; },
    refetchInterval: 900,
  });

  function accept(updated: JobDto) {
    setJob(updated); setSources(updated.merge_sources ?? []);
    cache.setQueryData(["job", updated.id], updated);
    void cache.invalidateQueries({ queryKey: ["history"] });
  }
  async function run(label: string, operation: () => Promise<JobDto>, rollback?: MergeSource[], focusId?: string) {
    if (busy.current || processing) return;
    busy.current = true; setPending(label); setError("");
    try { const updated = await operation(); accept(updated); if (!job) onCreated?.(updated); }
    catch (cause) { if (rollback) setSources(rollback); setError(cause instanceof Error ? cause.message : "Could not save changes. Please try again."); setAnnouncement("Changes could not be saved. Previous order restored."); }
    finally { busy.current = false; setPending(""); setProgress(null); if (focusId) requestAnimationFrame(() => handles.current.get(focusId)?.focus()); }
  }
  function add(files: File[]) {
    if (busy.current || processing || drag.current || !files.length) return;
    const bytes = sources.reduce((sum, source) => sum + source.size_bytes, 0) + files.reduce((sum, file) => sum + file.size, 0);
    if (files.some(file => !/\.pdf$/i.test(file.name) || !file.size)) { setError("Choose non-empty PDF files."); return; }
    if (sources.length + files.length > limit) { setError(`Choose at most ${limit} PDFs.`); return; }
    if ((sizeLimit && bytes > sizeLimit * 1048576) || files.some(file => config.max_upload_mb && file.size > config.max_upload_mb * 1048576)) { setError("The PDFs exceed the upload size limit."); return; }
    setProgress(0); void run("Uploading PDFs…", () => api.uploadMergePdfs(files, job?.id, setProgress));
  }
  function save(next: MergeSource[], before = sources, focusId?: string) {
    if (!job || busy.current || processing) return;
    setSources(next);
    void run("Saving order…", () => api.orderMergeSources(job.id, next.map(source => source.id)), before, focusId);
  }
  function reordered(current: MergeSource[], id: string, to: number) {
    const from = current.findIndex(source => source.id === id);
    if (from < 0 || to < 0 || to >= current.length || from === to) return current;
    const next = [...current]; const [source] = next.splice(from, 1); next.splice(to, 0, source);
    setAnnouncement(`${source.filename} moved to position ${to + 1} of ${next.length}.`);
    return next;
  }
  function move(id: string, to: number) {
    if (disabled || busy.current) return;
    const next = reordered(sources, id, to);
    if (next !== sources) save(next, sources, id);
  }
  function begin(id: string) {
    if (busy.current || processing || drag.current) return;
    drag.current = { id, before: sources, current: sources }; setDragging(true);
  }
  function hover(index: number) {
    if (!drag.current || !Number.isInteger(index)) return;
    drag.current.current = reordered(drag.current.current, drag.current.id, index);
    setSources(drag.current.current);
  }
  function finish(cancel = false) {
    const active = drag.current; if (!active) return;
    drag.current = null; setDragging(false);
    if (cancel) { setSources(active.before); setAnnouncement("PDF reordering cancelled."); handles.current.get(active.id)?.focus(); }
    else if (active.current !== active.before) save(active.current, active.before, active.id);
  }
  const instructions = "Drag the grip, use its arrow keys, or choose Move earlier/later. Entire PDFs merge in the displayed order.";
  const canvas = <>
    {!sources.length && <UploadZone accept="application/pdf,.pdf" multiple title="Drop your PDFs here" buttonLabel="Choose PDFs" hint={`${limit} PDFs · ${sizeLimit ? `${sizeLimit} MB combined` : "no combined size limit"}`} disabled={disabled} onFiles={add} />}
    {recovery.error && <p role="alert" className={styles.error}>Could not refresh this merge. <button type="button" onClick={() => void recovery.refetch()}>Try again</button></p>}
    {error && <p role="alert" className={styles.error}>{error}</p>}
    {sources.length > 0 && <><div className={styles.boardSummary}><span>{sources.length} PDFs · {sources.reduce((sum, source) => sum + source.pages, 0)} pages</span><span>{(sources.reduce((sum, source) => sum + source.size_bytes, 0) / 1048576).toFixed(1)} MB combined</span></div><p id="merge-order-help">{instructions}</p>
      <ol className={styles.imageBoard} aria-label="PDF source order" onKeyDown={event => { if (event.key === "Escape") finish(true); }} onPointerMove={event => { if (!drag.current) return; const target = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>("[data-merge-index]"); if (target) hover(Number(target.dataset.mergeIndex)); }} onPointerUp={() => finish()} onPointerCancel={() => finish(true)} onLostPointerCapture={() => finish(true)}>
        {sources.map((source, index) => <li key={source.id} className={styles.imageCard} data-merge-index={index} onDragOver={event => { event.preventDefault(); hover(index); }} onDrop={event => { event.preventDefault(); finish(); }}>
          <span className={styles.pageNumber}>{index + 1}</span>
          <button type="button" className={styles.dragHandle} ref={element => { if (element) handles.current.set(source.id, element); else handles.current.delete(source.id); }} disabled={Boolean(pending) || processing} aria-label={`Reorder ${source.filename}, position ${index + 1}`} aria-describedby="merge-order-help" onKeyDown={event => { if (["ArrowLeft", "ArrowUp", "ArrowRight", "ArrowDown"].includes(event.key)) { event.preventDefault(); move(source.id, index + (["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 1)); } }} onPointerDown={event => { if (event.button !== 0) return; event.currentTarget.focus(); event.currentTarget.closest("ol")?.setPointerCapture?.(event.pointerId); begin(source.id); }}>⠿</button>
          {job && <Preview jobId={job.id} source={source} />}<strong style={{ whiteSpace: "normal", overflowWrap: "anywhere" }} title={source.filename}>{source.filename}</strong><small>{source.pages} {source.pages === 1 ? "page" : "pages"}</small>
          <details className={styles.cardMenu}><summary aria-label={`Actions for ${source.filename}, position ${index + 1}`}>•••</summary><div><button type="button" disabled={disabled || index === 0} onClick={() => move(source.id, index - 1)}>Move earlier</button><button type="button" disabled={disabled || index === sources.length - 1} onClick={() => move(source.id, index + 1)}>Move later</button><button type="button" disabled={disabled} onClick={() => save(sources.filter(item => item.id !== source.id), sources, sources[index + 1]?.id ?? sources[index - 1]?.id)}>Remove</button></div></details>
        </li>)}
        <li className={styles.addTile}><label><input type="file" accept="application/pdf,.pdf" multiple disabled={disabled} onChange={event => { add(Array.from(event.target.files ?? [])); event.target.value = ""; }} /><span>＋</span><strong>Add PDFs</strong></label></li>
      </ol></>}
    <p className="sr-only" aria-live="polite">{announcement}</p>
    {progress !== null && <Progress value={progress} label={progress === 100 ? "Validating PDFs…" : `Uploading… ${progress}%`} />}
    {pending && progress === null && <p role="status">{pending}</p>}
  </>;
  const footer = <><button type="button" className="primary" disabled={disabled || sources.length < 2} onClick={() => job && void run("Merging PDFs…", () => api.merge(job.id))}>{pending || (processing ? "Merging PDFs…" : "Merge PDF")}</button>{job?.has_pdf && <a className="download-link" href={api.downloadUrl(job.id, "pdf")} download>{job.merge_dirty ? "Download previous result" : "Download merged PDF"}</a>}</>;
  return <ToolFrame title="Merge PDF" description="Arrange complete documents and combine them into one PDF." canvas={<WorkspaceContent panel={panel} kind="merge_pdf" jobs={jobs} selected={job} onOpen={onOpen}>{canvas}</WorkspaceContent>} settings={<div className={styles.setupPanel}><h2>Document order</h2><p>{instructions}</p><p>Choose at least two PDFs. Sources stay saved so you can edit and merge again.</p>{job?.has_pdf && <p>{job.output_filename}{job.merge_dirty ? " · Previous result; merge again to include your changes." : ` · ${job.output_pages} pages`}</p>}{job && !disabled && <><DeleteJobButton job={job} onDeleted={onDeleted} /><button type="button" onClick={onDeleted}>Start another merge</button></>}</div>} actionFooter={footer} />;
}
