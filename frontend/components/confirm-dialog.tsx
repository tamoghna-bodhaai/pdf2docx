"use client";

import { useEffect, useRef } from "react";

export function ConfirmDialog({
  open, title, description, action = "Delete", onConfirm, onClose,
}: {
  open: boolean; title: string; description: string; action?: string;
  onConfirm: () => void; onClose: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
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
  return (
    <dialog ref={dialog} className="confirm-dialog" aria-labelledby="confirm-title" aria-describedby="confirm-description" onCancel={onClose} onClose={() => { if (open) onClose(); }}>
      <form method="dialog" onSubmit={(event) => { event.preventDefault(); onConfirm(); }}>
        <h2 id="confirm-title">{title}</h2>
        <p id="confirm-description">{description}</p>
        <div className="dialog-actions">
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="submit" className="danger">{action}</button>
        </div>
      </form>
    </dialog>
  );
}
