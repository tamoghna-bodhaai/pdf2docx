"use client";

import { useEffect, useId, useRef, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

function subscribeToHydration() { return () => undefined; }

export function ConfirmDialog({
  open, title, description, action = "Delete", onConfirm, onClose,
}: {
  open: boolean; title: string; description: string; action?: string;
  onConfirm: () => void; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const hydrated = useSyncExternalStore(subscribeToHydration, () => true, () => false);
  const titleId = useId();
  const descriptionId = useId();
  useEffect(() => {
    const element = dialog.current;
    if (!element) return;
    if (open && !element.open) {
      trigger.current = document.activeElement as HTMLElement | null;
      element.showModal();
    } else if (!open && element.open) {
      element.close();
      trigger.current?.focus();
    }
  }, [open]);
  if (!hydrated) return null;
  return createPortal(
    <dialog ref={dialog} className="confirm-dialog" aria-labelledby={titleId} aria-describedby={descriptionId} onCancel={onClose} onClose={() => { if (open) onClose(); }}>
      <form method="dialog" onSubmit={(event) => { event.preventDefault(); onConfirm(); }}>
        <h2 id={titleId}>{title}</h2>
        <p id={descriptionId}>{description}</p>
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="submit" className="danger">{action}</button>
        </div>
      </form>
    </dialog>,
    document.body,
  );
}
