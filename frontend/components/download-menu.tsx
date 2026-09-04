import { api } from "@/lib/api/client";
import type { JobDto } from "@/lib/api/types";
import { Icon } from "./icons";

export function DownloadMenu({ job }: { job: JobDto }) {
  const links: Array<[string, string]> = [];
  if (job.kind === "pdf_to_docx") {
    if (job.has_docx) links.push(["DOCX", api.downloadUrl(job.id, "docx")]);
    if (job.has_md) links.push(["Markdown", api.downloadUrl(job.id, "md")]);
    job.mathpix_formats.filter((name) => name !== "docx").forEach((name) => links.push([name.toUpperCase(), api.downloadUrl(job.id, `mathpix-${name}`)]));
    if (job.has_package) links.push(["Download all (.zip)", api.packageUrl(job.id)]);
  } else if (job.has_pdf) links.push(["Download PDF", api.downloadUrl(job.id, "pdf")]);
  if (!links.length) return null;
  return (
    <details className="download-menu">
      <summary><Icon name="download" /> Download</summary>
      <div className="download-links">{links.map(([label, href]) => <a href={href} key={href} download>{label}</a>)}</div>
    </details>
  );
}
