/* eslint-disable @next/next/no-img-element -- authenticated generated pages are not static assets */
"use client";

import { useQuery } from "@tanstack/react-query";
import { marked } from "marked";
import DOMPurify from "dompurify";
import renderMathInElement from "katex/contrib/auto-render";
import { KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import { DownloadMenu } from "@/components/download-menu";
import { Icon } from "@/components/icons";
import { api } from "@/lib/api/client";
import type { DetectionBlock, JobDto } from "@/lib/api/types";
import { useMobileViewport } from "@/hooks/use-responsive-drawer";
import { dressMmdLists, prepareMmd, restoreMmd } from "./mmd";

const delimiters = [
  { left: "$$", right: "$$", display: true }, { left: "\\[", right: "\\]", display: true },
  { left: "\\(", right: "\\)", display: false }, { left: "$", right: "$", display: false },
];

function sanitize(html: string): string {
  return DOMPurify.sanitize(html, {
    USE_PROFILES: { html: true },
    FORBID_TAGS: ["style", "form", "input", "button", "textarea", "select"],
    FORBID_ATTR: ["style"],
    ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto):|[^a-z]|[a-z+.-]+(?:[^a-z+.:\-]|$))/i,
  });
}

function renderMarkdown(markdown: string, jobId: string) {
  const prepared = prepareMmd(markdown);
  const local = prepared.markdown.replace(/(!\[[^\]]*\]\()(?!https?:|data:|\/)([^)\s]+)/g, (_whole, head: string, target: string) => `${head}${api.assetUrl(jobId, target)}`);
  const html = marked.parse(local, { gfm: true, breaks: true }) as string;
  return sanitize(restoreMmd(html, prepared.math));
}

export function ComparisonViewer({ job, onBack }: { job: JobDto; onBack: () => void }) {
  const [page, setPage] = useState(1);
  const [zoom, setZoom] = useState(1);
  const [tab, setTab] = useState<"rendered" | "text">("rendered");
  const [scope, setScope] = useState<"page" | "whole">("page");
  const [pane, setPane] = useState<"source" | "converted">("source");
  const [hiddenKinds, setHiddenKinds] = useState(new Set<string>());
  const [sourceFailed, setSourceFailed] = useState(false);
  const [sourceAttempt, setSourceAttempt] = useState(0);
  const rendered = useRef<HTMLDivElement>(null);
  const sourceTab = useRef<HTMLButtonElement>(null);
  const convertedTab = useRef<HTMLButtonElement>(null);
  const renderedTab = useRef<HTMLButtonElement>(null);
  const textTab = useRef<HTMLButtonElement>(null);
  const mobile = useMobileViewport();
  const detection = useQuery({ queryKey: ["detection", job.id], queryFn: () => api.detection(job.id), enabled: job.has_detection, retry: false });
  const whole = useQuery({ queryKey: ["markdown", job.id], queryFn: () => api.markdown(job.id), enabled: scope === "whole" || !job.has_detection, retry: false });
  const pageData = detection.data?.pages[page - 1];
  const markdown = scope === "whole" ? whole.data?.markdown ?? "" : pageData?.markdown ?? whole.data?.markdown ?? "";
  const html = useMemo(() => renderMarkdown(markdown, job.id), [job.id, markdown]);
  useEffect(() => {
    if (tab === "rendered" && rendered.current) {
      dressMmdLists(rendered.current);
      renderMathInElement(rendered.current, { delimiters, throwOnError: false });
    }
  }, [html, tab]);
  const pageCount = detection.data?.pages.length || job.pages;
  function showPage(next: number) { setPage(Math.max(1, Math.min(pageCount, next))); }
  function tabKeys(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const next = pane === "source" ? "converted" : "source";
    setPane(next);
    (next === "source" ? sourceTab : convertedTab).current?.focus();
    event.preventDefault();
    event.stopPropagation();
  }
  function contentTabKeys(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
    const next = tab === "rendered" ? "text" : "rendered";
    setTab(next);
    (next === "rendered" ? renderedTab : textTab).current?.focus();
    event.preventDefault();
    event.stopPropagation();
  }
  useEffect(() => {
    function navigate(event: globalThis.KeyboardEvent) {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      const target = event.target;
      if (target instanceof HTMLElement && target.closest("input, select, textarea, button, a, summary, [contenteditable='true']")) return;
      setPage((current) => Math.max(1, Math.min(pageCount, current + (event.key === "ArrowRight" ? 1 : -1))));
      event.preventDefault();
    }
    document.addEventListener("keydown", navigate);
    return () => document.removeEventListener("keydown", navigate);
  }, [pageCount]);
  function overlay(blocks: DetectionBlock[]) {
    if (job.layout === "mathpix" || !pageData) return null;
    return <svg viewBox={`0 0 ${pageData.width} ${pageData.height}`} aria-label="Detected content overlay">{blocks.filter((block) => !hiddenKinds.has(block.kind)).map((block) => { const [x0, y0, x1, y1] = block.bbox; return <rect key={block.index} x={x0} y={y0} width={x1 - x0} height={y1 - y0} fill={`var(--k-${block.kind}, var(--brand))`} fillOpacity="0.11" stroke={`var(--k-${block.kind}, var(--brand))`} strokeWidth="1"><title>{block.text ? `${block.kind} — ${block.text}` : block.kind}</title></rect>; })}</svg>;
  }
  const kinds = [...new Set(pageData?.blocks.map((block) => block.kind) ?? [])];
  return (
    <section className="comparison-view" aria-labelledby="comparison-file">
      <header className="comparison-header"><button className="back-button" type="button" onClick={onBack}><Icon name="back" />Back to files</button><div className="comparison-document"><h1 id="comparison-file">{job.filename}</h1><p>{job.pages} pages · {job.status} · {job.requested_formats.map((value) => value.toUpperCase()).join(" · ")}</p></div><DownloadMenu job={job} /></header>
      <div className="comparison-tabs" role="tablist" aria-label="Comparison pane" onKeyDown={tabKeys}><button ref={sourceTab} type="button" role="tab" tabIndex={pane === "source" ? 0 : -1} aria-selected={pane === "source"} aria-controls="source-pane" onClick={() => setPane("source")}>Source</button><button ref={convertedTab} type="button" role="tab" tabIndex={pane === "converted" ? 0 : -1} aria-selected={pane === "converted"} aria-controls="converted-pane" onClick={() => setPane("converted")}>Converted</button></div>
      <div className="comparison-grid">
        <section id="source-pane" className={`viewer-pane ${pane !== "source" ? "mobile-hidden" : ""}`} aria-label="Source PDF" aria-hidden={mobile && pane !== "source" || undefined} inert={mobile && pane !== "source" || undefined}><header className="pane-header"><div><p className="eyebrow">Source</p><h2>PDF page</h2></div><div className="pane-tools"><button type="button" aria-label="Zoom out" onClick={() => setZoom((value) => Math.max(.25, value / 1.25))}>−</button><button type="button" onClick={() => setZoom(1)}>Fit</button><button type="button" aria-label="Zoom in" onClick={() => setZoom((value) => Math.min(4, value * 1.25))}>+</button></div></header><div className="pane-body">{!job.has_source ? <div className="viewer-placeholder"><strong>Source unavailable</strong><span>This PDF is no longer available for preview.</span></div> : sourceFailed ? <div className="viewer-placeholder" role="alert"><strong>Couldn’t load this source page</strong><button type="button" onClick={() => { setSourceFailed(false); setSourceAttempt((value) => value + 1); }}>Try again</button></div> : <div className="stage-wrap"><div className="stage" style={{ width: `${zoom * 100}%`, maxWidth: "none" }}>{/* This authenticated, generated page cannot use static image optimization. */}<img key={`${job.id}-${page}-${sourceAttempt}`} src={api.pageUrl(job.id, page)} alt={`Source PDF page ${page} of ${pageCount}`} width={1000} height={1414} onError={() => setSourceFailed(true)} />{overlay(pageData?.blocks ?? [])}</div></div>}</div><footer className="pane-footer"><div className="pager"><button type="button" aria-label="Previous page" disabled={page === 1} onClick={() => showPage(page - 1)}>←</button><input aria-label="Page number" inputMode="numeric" value={page} onChange={(event) => showPage(Number(event.target.value) || page)} /><span>of {pageCount}</span><button type="button" aria-label="Next page" disabled={page === pageCount} onClick={() => showPage(page + 1)}>→</button></div><div className="legend">{kinds.map((kind) => <button type="button" aria-pressed={!hiddenKinds.has(kind)} key={kind} onClick={() => setHiddenKinds((current) => { const next = new Set(current); if (next.has(kind)) next.delete(kind); else next.add(kind); return next; })}><span className="legend-swatch" style={{ background: `var(--k-${kind}, var(--brand))` }} />{kind}</button>)}</div></footer></section>
        <section id="converted-pane" className={`viewer-pane ${pane !== "converted" ? "mobile-hidden" : ""}`} aria-label="Converted document" aria-hidden={mobile && pane !== "converted" || undefined} inert={mobile && pane !== "converted" || undefined}><header className="pane-header output-header"><div><p className="eyebrow">Converted</p><h2>Document preview</h2></div><div className="content-tabs" role="tablist" aria-label="Converted content" onKeyDown={contentTabKeys}><button ref={renderedTab} type="button" role="tab" tabIndex={tab === "rendered" ? 0 : -1} aria-selected={tab === "rendered"} onClick={() => setTab("rendered")}>Rendered</button><button ref={textTab} type="button" role="tab" tabIndex={tab === "text" ? 0 : -1} aria-selected={tab === "text"} onClick={() => setTab("text")}>Text</button></div><div className="pane-tools"><button type="button" onClick={() => setScope((value) => value === "page" ? "whole" : "page")}>{scope === "page" ? "This page" : "Whole document"}</button><button type="button" disabled={!markdown.trim()} onClick={async () => navigator.clipboard.writeText(markdown)}>Copy</button></div></header><div className="pane-body">{(scope === "page" && job.has_detection && detection.isLoading) || ((scope === "whole" || !job.has_detection) && whole.isLoading) ? <div className="viewer-placeholder"><strong>Loading converted content…</strong><span>Preparing the page-aligned preview.</span></div> : whole.isError ? <div className="viewer-placeholder" role="alert"><strong>Couldn’t load converted content</strong><button type="button" onClick={() => whole.refetch()}>Try again</button></div> : detection.isError && scope === "page" ? <div className="viewer-placeholder" role="alert"><strong>Couldn’t load page preview</strong><button type="button" onClick={() => detection.refetch()}>Try again</button></div> : !markdown.trim() ? <div className="viewer-placeholder"><strong>Converted content unavailable</strong><span>No preview content was saved for this document.</span></div> : tab === "rendered" ? <div ref={rendered} className="rendered" dangerouslySetInnerHTML={{ __html: html }} /> : <pre className="plain">{markdown}</pre>}</div></section>
      </div>
    </section>
  );
}
