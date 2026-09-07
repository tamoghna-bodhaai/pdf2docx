"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

const QUERY = "(max-width: 900px)";

function subscribe(query: string, listener: () => void) {
  if (typeof window.matchMedia !== "function") return () => undefined;
  const media = window.matchMedia(query);
  media.addEventListener("change", listener);
  return () => media.removeEventListener("change", listener);
}

function mobileSnapshot(query: string) {
  return typeof window.matchMedia === "function" && window.matchMedia(query).matches;
}

export function useMobileViewport(query = QUERY) {
  return useSyncExternalStore((listener) => subscribe(query, listener), () => mobileSnapshot(query), () => false);
}

export function useResponsiveDrawer(query = QUERY) {
  const mobile = useMobileViewport(query);
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);

  const close = useCallback((returnFocus = true) => {
    setOpen(false);
    if (returnFocus) requestAnimationFrame(() => (returnFocusRef.current ?? triggerRef.current)?.focus());
  }, []);
  const show = useCallback(() => { returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null; setOpen(true); }, []);

  useEffect(() => {
    if (!mobile || !open) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    panelRef.current?.querySelector<HTMLElement>("button, a, input, summary")?.focus();
    const escape = (event: KeyboardEvent) => {
      if (document.querySelector("dialog[open]")) return;
      if (event.key === "Tab") {
        const nodes = Array.from(panelRef.current?.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input:not(:disabled), summary, [tabindex="0"]') ?? []).filter(node => node.getClientRects().length > 0);
        const first = nodes[0]; const last = nodes[nodes.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
    };
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("keydown", escape); document.body.style.overflow = previousOverflow; };
  }, [close, mobile, open]);

  return {
    open,
    mobile,
    hidden: mobile && !open,
    triggerRef,
    panelRef,
    show,
    close,
    toggle: () => open ? close() : show(),
  };
}
