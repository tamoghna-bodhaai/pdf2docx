"use client";

import { useEffect, useRef } from "react";
import { visibleArtifacts } from "@/lib/api/artifacts";
import { api } from "@/lib/api/client";
import type { JobDto } from "@/lib/api/types";
import { Icon } from "./icons";

export function DownloadMenu({ job }: { job: JobDto }) {
  const menuRef = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    const closeOnOutsideClick = (event: MouseEvent) => {
      if (menuRef.current?.open && !menuRef.current.contains(event.target as Node)) {
        menuRef.current.open = false;
      }
    };

    document.addEventListener("click", closeOnOutsideClick);
    return () => document.removeEventListener("click", closeOnOutsideClick);
  }, []);

  const links: Array<[string, string]> = [];
  if (job.kind === "pdf_to_docx") {
    if (job.has_docx) links.push(["DOCX", api.downloadUrl(job.id, "docx")]);
    if (job.has_md) links.push(["Markdown", api.downloadUrl(job.id, "md")]);
    job.mathpix_formats.filter((name) => name !== "docx").forEach((name) => links.push([name.toUpperCase(), api.downloadUrl(job.id, `mathpix-${name}`)]));
  } else if (job.kind === "split_pdf" && job.artifacts.length) {
    visibleArtifacts(job).forEach((artifact) => links.push([
      artifact.media_type === "application/zip" ? "Download all (.zip)" : artifact.filename,
      api.artifactUrl(job.id, artifact.key),
    ]));
  } else if (job.has_pdf) links.push([job.kind === "merge_pdf" ? job.merge_dirty ? "Download previous result" : "Download merged PDF" : "Download PDF", api.downloadUrl(job.id, "pdf")]);
  if (!links.length) return null;

  return (
    <details ref={menuRef} className="download-menu">
      <summary><Icon name="download" /> Download</summary>
      <div className="download-links">{links.map(([label, href]) => <a href={href} key={href} download>{label}</a>)}</div>
    </details>
  );
}
