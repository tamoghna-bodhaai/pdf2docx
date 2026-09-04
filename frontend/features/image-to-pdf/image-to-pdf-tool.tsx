/* eslint-disable @next/next/no-img-element -- local object URLs cannot use the static optimizer */
"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { DownloadMenu } from "@/components/download-menu";
import { Progress } from "@/components/progress";
import { UploadZone } from "@/components/upload-zone";
import { api, ApiError } from "@/lib/api/client";
import type { ConfigDto, JobDto } from "@/lib/api/types";
import styles from "@/features/tools/tools.module.css";

interface ImageItem { id: string; file: File; url: string }

function formatSize(bytes: number) {
  return bytes < 1_048_576 ? `${Math.max(1, Math.round(bytes / 1024))} KB` : `${(bytes / 1_048_576).toFixed(1)} MB`;
}

export function ImageToPdfTool({ config, restoredJob, onCreated }: { config: ConfigDto; restoredJob?: JobDto; onCreated?: (job: JobDto) => void }) {
  const queryClient = useQueryClient();
  const [images, setImages] = useState<ImageItem[]>([]);
  const [jobId, setJobId] = useState(restoredJob?.kind === "images_to_pdf" ? restoredJob.id : "");
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [dragged, setDragged] = useState<number | null>(null);
  const urls = useRef(new Set<string>());
  useEffect(() => () => { urls.current.forEach((url) => URL.revokeObjectURL(url)); urls.current.clear(); }, []);
  const job = useQuery({
    queryKey: ["job", jobId], queryFn: () => api.job(jobId), enabled: Boolean(jobId), initialData: restoredJob?.kind === "images_to_pdf" ? restoredJob : undefined,
    refetchInterval: (query) => query.state.data?.status === "processing" ? 900 : false,
  });
  const create = useMutation({
    mutationFn: () => api.uploadImages(images.map((item) => item.file), setProgress),
    onSuccess: (created) => { setJobId(created.id); setProgress(100); queryClient.setQueryData(["job", created.id], created); queryClient.invalidateQueries({ queryKey: ["history"] }); onCreated?.(created); },
    onError: (cause) => { setError(cause instanceof ApiError ? cause.message : "The PDF could not be created."); setProgress(null); },
  });

  function addFiles(files: File[]) {
    setError("");
    const accepted = files.filter((file) => config.accepted_image_types.includes(file.type) || /\.(jpe?g|png|webp)$/i.test(file.name));
    if (accepted.length !== files.length) { setError("Use JPEG, PNG, or WebP images."); return; }
    if (images.length + accepted.length > config.image_max_files) { setError(`Choose at most ${config.image_max_files} images.`); return; }
    const bytes = images.reduce((sum, item) => sum + item.file.size, 0) + accepted.reduce((sum, file) => sum + file.size, 0);
    if (bytes > config.image_max_upload_mb * 1_048_576) { setError(`Images may total at most ${config.image_max_upload_mb} MB.`); return; }
    const next = accepted.map((file, index) => {
      const url = URL.createObjectURL(file); urls.current.add(url);
      return { id: `${file.name}-${file.lastModified}-${index}-${crypto.randomUUID()}`, file, url };
    });
    setImages((current) => [...current, ...next]);
  }

  function remove(index: number) {
    setImages((current) => {
      const item = current[index];
      if (item) { URL.revokeObjectURL(item.url); urls.current.delete(item.url); }
      return current.filter((_, position) => position !== index);
    });
  }
  function move(from: number, to: number) {
    if (to < 0 || to >= images.length || from === to) return;
    setImages((current) => { const next = [...current]; const [item] = next.splice(from, 1); if (item) next.splice(to, 0, item); return next; });
  }

  const bytes = images.reduce((sum, item) => sum + item.file.size, 0);
  const currentJob = jobId ? job.data : undefined;
  return (
    <>
      <header className={styles.toolHeader}><p className="eyebrow">Local · no charge</p><h1>Turn images into one PDF.</h1><p>Arrange up to {config.image_max_files} images. Each becomes a fitted A4 page in the order shown.</p></header>
      {!jobId && <UploadZone accept="image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp" multiple title="Drop your images here" buttonLabel="Choose images" hint={`JPEG, PNG, or WebP · ${config.image_max_files} images · ${config.image_max_upload_mb} MB combined`} disabled={create.isPending} onFiles={addFiles} />}
      {error && <p className={styles.error} role="alert">{error}</p>}
      {!jobId && images.length > 0 && <><p className={styles.summary}>{images.length} image{images.length === 1 ? "" : "s"} · {formatSize(bytes)} combined</p><ol className={styles.imageList}>{images.map((item, index) => <li className={styles.imageRow} draggable onDragStart={() => setDragged(index)} onDragOver={(event) => event.preventDefault()} onDrop={() => { if (dragged !== null) move(dragged, index); setDragged(null); }} key={item.id}>{/* Object URLs are already local previews and cannot use the Next image optimizer. */}<img className={styles.thumbnail} src={item.url} width={56} height={56} alt={`Preview of ${item.file.name}`} /><span className={styles.imageMeta}><strong>{item.file.name}</strong><small>{formatSize(item.file.size)} · Page {index + 1}</small></span><span className={styles.rowActions}><button type="button" aria-label={`Move ${item.file.name} earlier`} disabled={index === 0} onClick={() => move(index, index - 1)}>↑</button><button type="button" aria-label={`Move ${item.file.name} later`} disabled={index === images.length - 1} onClick={() => move(index, index + 1)}>↓</button><button type="button" aria-label={`Remove ${item.file.name}`} onClick={() => remove(index)}>Remove</button></span></li>)}</ol><div className={styles.primaryRow}><button className="primary" type="button" disabled={create.isPending} onClick={() => create.mutate()}>{create.isPending ? "Creating PDF…" : "Create PDF"}</button></div></>}
      {progress !== null && progress < 100 && <Progress value={progress} label={`Uploading… ${progress}%`} />}
      {currentJob && <section className={styles.resultCard} aria-live="polite"><div className={styles.statusRow}><div><h2>{currentJob.output_filename || "PDF result"}</h2><p className={styles.resultMeta}><span>{currentJob.output_pages || currentJob.pages} {(currentJob.output_pages || currentJob.pages) === 1 ? "page" : "pages"}</span><span>Local · no charge</span><span>{currentJob.status}</span></p></div><span className={`status-badge ${currentJob.status === "done" ? "complete" : currentJob.status === "error" ? "error" : "working"}`}>{currentJob.status}</span></div>{currentJob.error && <p className={styles.error}>{currentJob.error}</p>}<div className={styles.primaryRow}><DownloadMenu job={currentJob} /><button type="button" onClick={() => { setJobId(""); setProgress(null); }}>Create another</button></div></section>}
    </>
  );
}
