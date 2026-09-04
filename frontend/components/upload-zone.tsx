"use client";

import { DragEvent, useRef, useState } from "react";
import { Icon } from "./icons";

export function UploadZone({
  accept, multiple, title, buttonLabel, hint, disabled = false, onFiles,
}: {
  accept: string; multiple: boolean; title: string; buttonLabel: string; hint: string;
  disabled?: boolean; onFiles: (files: File[]) => void;
}) {
  const picker = useRef<HTMLInputElement>(null);
  const [hot, setHot] = useState(false);
  function drop(event: DragEvent<HTMLButtonElement>) {
    event.preventDefault(); setHot(false);
    if (!disabled) onFiles([...event.dataTransfer.files]);
  }
  return (
    <section className="upload-zone-card" aria-labelledby="upload-title">
      <button id="drop" className={hot ? "hot" : ""} type="button" disabled={disabled} aria-describedby="hint" onClick={() => picker.current?.click()} onDragEnter={(event) => { event.preventDefault(); setHot(true); }} onDragOver={(event) => event.preventDefault()} onDragLeave={() => setHot(false)} onDrop={drop}>
        <span className="upload-icon" aria-hidden="true"><Icon name="upload" /></span>
        <span className="drop-copy"><strong id="upload-title">{title}</strong><span>or choose {multiple ? "files" : "a file"} from your device</span></span>
        <span className="choose-file">{buttonLabel}</span>
      </button>
      <input ref={picker} id="picker" type="file" accept={accept} multiple={multiple} disabled={disabled} onChange={(event) => { if (event.target.files) onFiles([...event.target.files]); event.target.value = ""; }} />
      <div className="upload-constraints" id="hint"><span>{hint}</span></div>
    </section>
  );
}
