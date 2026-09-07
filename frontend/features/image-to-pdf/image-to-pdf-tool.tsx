/* eslint-disable @next/next/no-img-element -- local object URLs cannot use the static optimizer */
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyboardEvent, useEffect, useRef, useState } from "react";
import { WorkspaceContent } from "@/components/workspace-content";
import { ToolFrame } from "@/components/tool-frame";
import { DeleteJobButton } from "@/components/delete-job-button";
import { Progress } from "@/components/progress";
import { UploadZone } from "@/components/upload-zone";
import { api, ApiError } from "@/lib/api/client";
import type { DockPanel } from "@/hooks/use-workspace-url";
import type { ConfigDto, JobDto, PageOrientation } from "@/lib/api/types";
import styles from "@/features/tools/tools.module.css";

interface ImageItem { id: string; file: File; url: string; landscape: boolean }

function compactName(name: string) {
  if (name.length <= 24) return name;
  const number = name.match(/\d+(?!.*\d)/)?.[0];
  const tail = name.slice(-16);
  return number && !tail.includes(number) ? `${name.slice(0, 6)}…${number}…${name.slice(-12)}` : `${name.slice(0, 7)}…${tail}`;
}

function formatSize(bytes: number) {
  return bytes < 1_048_576 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1_048_576).toFixed(1)} MB`;
}

export function ImageToPdfTool({ config, restoredJob, selectedJobId, onCreated, onDeleted, panel = "setup", jobs = [], onOpen = () => undefined }: {
  config: ConfigDto; restoredJob?: JobDto; onCreated?: (job: JobDto) => void; onDeleted?: () => void;
  selectedJobId?: string | null;
  panel?: DockPanel; onPanel?: (panel: DockPanel) => void; jobs?: JobDto[]; onOpen?: (job: JobDto) => void;
}) {
  const queryClient = useQueryClient();
  const [images, setImages] = useState<ImageItem[]>([]);
  const [orientation, setOrientation] = useState<PageOrientation>(restoredJob?.page_orientation ?? "auto");
  const [jobId, setJobId] = useState(restoredJob?.kind === "images_to_pdf" ? restoredJob.id : "");
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [dragged, setDragged] = useState<number | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const urls = useRef(new Set<string>());
  const orderSnapshot = useRef<ImageItem[] | null>(null);
  useEffect(() => () => { urls.current.forEach((url) => URL.revokeObjectURL(url)); urls.current.clear(); }, []);
  useEffect(() => {
    if (selectedJobId === undefined) return;
    // The URL is the external source of truth when navigating between saved jobs.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setJobId(selectedJobId ?? "");
  }, [selectedJobId]);
  const activeJobId = jobId;
  function discardPreviews() {
    urls.current.forEach((url) => URL.revokeObjectURL(url));
    urls.current.clear();
    setImages([]);
  }
  const job = useQuery({
    queryKey: ["job", activeJobId], queryFn: () => api.job(activeJobId), enabled: Boolean(activeJobId), initialData: restoredJob?.kind === "images_to_pdf" && restoredJob.id === activeJobId ? restoredJob : undefined,
    refetchInterval: (query) => query.state.data?.status === "processing" ? 900 : false,
  });
  useEffect(() => {
    if (job.data?.status !== "done") return;
    const timer = window.setTimeout(discardPreviews, 0);
    return () => window.clearTimeout(timer);
  }, [job.data?.status]);
  const create = useMutation({
    mutationFn: () => api.uploadImages(images.map((item) => item.file), orientation, setProgress),
    onSuccess: (created) => { setJobId(created.id); setProgress(100); queryClient.setQueryData(["job", created.id], created); queryClient.invalidateQueries({ queryKey: ["history"] }); onCreated?.(created); },
    onError: (cause) => { setError(cause instanceof ApiError ? cause.message : "The PDF could not be created."); setProgress(null); },
  });

  function addFiles(files: File[]) {
    if (create.isPending) return;
    setError("");
    const accepted = files.filter((file) => config.accepted_image_types.includes(file.type) || /\.(jpe?g|png|webp)$/i.test(file.name));
    if (accepted.length !== files.length) { setError("Use JPEG, PNG, or WebP images."); return; }
    if (images.length + accepted.length > config.image_max_files) { setError(`Choose at most ${config.image_max_files} images.`); return; }
    const bytes = images.reduce((sum, item) => sum + item.file.size, 0) + accepted.reduce((sum, file) => sum + file.size, 0);
    if (config.image_max_upload_mb && bytes > config.image_max_upload_mb * 1_048_576) { setError(`Images may total at most ${config.image_max_upload_mb} MB.`); return; }
    const next = accepted.map((file, index) => {
      const url = URL.createObjectURL(file); urls.current.add(url);
      return { id: `${file.name}-${file.lastModified}-${index}-${crypto.randomUUID()}`, file, url, landscape: false };
    });
    setImages((current) => [...current, ...next]);
  }
  function remove(index: number) {
    if (create.isPending) return;
    setImages((current) => { const item = current[index]; if (item) { URL.revokeObjectURL(item.url); urls.current.delete(item.url); } return current.filter((_, position) => position !== index); });
  }
  function move(from: number, to: number) {
    if (create.isPending || to < 0 || to >= images.length || from === to) return;
    const name = images[from]?.file.name;
    setImages((current) => { const next = [...current]; const [item] = next.splice(from, 1); if (item) next.splice(to, 0, item); return next; });
    setAnnouncement(`${name} moved to page ${to + 1}.`);
  }
  function beginReorder(index: number) {
    if (create.isPending) return;
    if (!orderSnapshot.current) orderSnapshot.current = images;
    setDragged(index);
  }
  function finishReorder() {
    orderSnapshot.current = null;
    setDragged(null);
  }
  function cancelReorder() {
    if (orderSnapshot.current) setImages(orderSnapshot.current);
    orderSnapshot.current = null;
    setDragged(null);
    setAnnouncement("Image reordering cancelled.");
  }
  function reorderKey(event: KeyboardEvent, index: number) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowUp" && event.key !== "ArrowRight" && event.key !== "ArrowDown") return;
    event.preventDefault();
    move(index, index + (event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 1));
  }
  const bytes = images.reduce((sum, item) => sum + item.file.size, 0);
  const currentJob = activeJobId ? job.data : undefined;
  const canvas = <>
    {!activeJobId && images.length === 0 && <UploadZone accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" multiple title="Drop your images here" buttonLabel="Choose images" hint={`JPEG, PNG, or WebP · ${config.image_max_files} images · ${config.image_max_upload_mb ? `${config.image_max_upload_mb} MB combined` : "no size limit"}`} disabled={create.isPending} onFiles={addFiles} />}
    {error && <p className={styles.error} role="alert">{error}</p>}
    {!activeJobId && images.length > 0 && <><div className={styles.boardSummary}><span>{images.length} image{images.length === 1 ? "" : "s"}</span><span>{formatSize(bytes)} combined</span></div><ol className={styles.imageBoard} aria-label="PDF pages" onKeyDown={(event) => { if (event.key === "Escape" && dragged !== null) { event.preventDefault(); cancelReorder(); } }} onPointerMove={(event) => { if (dragged === null) return; const target = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>("[data-image-index]"); const index = Number(target?.dataset.imageIndex); if (Number.isInteger(index) && index !== dragged) { move(dragged, index); setDragged(index); } }} onPointerUp={finishReorder} onPointerCancel={cancelReorder}>{images.map((item, index) => {
      const pageOrientation = orientation === "auto" ? item.landscape ? "landscape" : "portrait" : orientation;
      return <li className={styles.imageCard} data-image-index={index} draggable={!create.isPending} onDragStart={() => beginReorder(index)} onDragOver={(event) => event.preventDefault()} onDragEnter={() => { if (dragged !== null) move(dragged, index); setDragged(index); }} onDrop={finishReorder} onDragEnd={() => { if (orderSnapshot.current) cancelReorder(); }} key={item.id}>
        <span className={styles.pageNumber}>{index + 1}</span><button className={styles.dragHandle} type="button" disabled={create.isPending} aria-label={`Reorder ${item.file.name}. Use arrow keys to move.`} onKeyDown={(event) => reorderKey(event, index)} onPointerDown={(event) => { event.currentTarget.setPointerCapture?.(event.pointerId); beginReorder(index); }} onPointerUp={finishReorder} onPointerCancel={cancelReorder}>⠿</button>
        <div className={styles.previewArea}><div className={`${styles.pageSilhouette} ${styles[pageOrientation]}`}><img src={item.url} alt={`Preview of ${item.file.name}`} onLoad={(event) => { const landscape = event.currentTarget.naturalWidth > event.currentTarget.naturalHeight; setImages((current) => current.map((entry) => entry.id === item.id ? { ...entry, landscape } : entry)); }} /></div></div>
        <strong tabIndex={0} aria-label={item.file.name} title={item.file.name}>{compactName(item.file.name)}<span className={styles.fullName}>{item.file.name}</span></strong><small>{formatSize(item.file.size)} · Page {index + 1}</small>
        <details className={styles.cardMenu}><summary aria-label={`Actions for ${item.file.name}`}>•••</summary><div><p>{item.file.name}</p><button type="button" disabled={create.isPending || index === 0} onClick={() => move(index, index - 1)}>Move earlier</button><button type="button" disabled={create.isPending || index === images.length - 1} onClick={() => move(index, index + 1)}>Move later</button><button type="button" disabled={create.isPending} onClick={() => remove(index)}>Remove</button></div></details>
      </li>;
    })}<li className={styles.addTile}><label><input type="file" disabled={create.isPending} accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" multiple onChange={(event) => { addFiles(Array.from(event.target.files || [])); event.target.value = ""; }} /><span>＋</span><strong>Add images</strong></label></li></ol></>}
    <p className="sr-only" aria-live="polite">{announcement}</p>
    {progress !== null && progress < 100 && <Progress value={progress} label={`Uploading… ${progress}%`} />}
    {currentJob && <section className={styles.canvasResult} aria-live="polite"><span className={styles.resultDocument}>PDF</span><h2>{currentJob.output_filename || "PDF result"}</h2><p>{currentJob.output_pages || currentJob.pages} pages · {currentJob.page_orientation} orientation</p>{currentJob.has_pdf && <a className="primary download-link" href={api.downloadUrl(currentJob.id, "pdf")} download>Download PDF</a>}{currentJob.error && <p className={styles.error}>{currentJob.error}</p>}</section>}
  </>;
  const visibleOrientation = currentJob?.page_orientation ?? orientation;
  const setup = <div className={styles.setupPanel}><div><p className="eyebrow">Page setup</p><h2>Orientation</h2><p>Every image is proportionally fitted to A4 with 36-point margins and no cropping.</p></div><fieldset className={styles.segmented}><legend>Page orientation</legend>{(["auto", "portrait", "landscape"] as PageOrientation[]).map((value) => <label key={value}><input type="radio" name="orientation" value={value} checked={visibleOrientation === value} disabled={Boolean(activeJobId) || create.isPending} onChange={() => setOrientation(value)} /><span>{value[0].toUpperCase() + value.slice(1)}</span></label>)}</fieldset>{currentJob && <><div className={styles.setupFacts}><span>Status<strong>{currentJob.status}</strong></span></div><DeleteJobButton job={currentJob} onDeleted={() => { setJobId(""); setProgress(null); onDeleted?.(); }} /><button type="button" onClick={() => { setJobId(""); setProgress(null); onDeleted?.(); }}>Create another</button></>}</div>;
  const actionFooter = !activeJobId ? <button className="primary" type="button" disabled={!images.length || create.isPending} onClick={() => create.mutate()}>{create.isPending ? "Creating PDF…" : `Create PDF${images.length ? ` · ${images.length} pages` : ""}`}</button> : currentJob?.has_pdf ? <a className="primary download-link" href={api.downloadUrl(currentJob.id, "pdf")} download>Download PDF</a> : null;
  return <ToolFrame title="Images to PDF" description="Arrange images on a visual page board, then export one polished A4 document." canvas={<WorkspaceContent panel={panel} kind="images_to_pdf" jobs={jobs} selected={currentJob} onOpen={onOpen}>{canvas}</WorkspaceContent>} settings={setup} actionFooter={actionFooter} />;
}
