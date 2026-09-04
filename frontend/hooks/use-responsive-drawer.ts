"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";

const QUERY = "(max-width: 900px)";

function subscribe(listener: () => void) {
  if (typeof window.matchMedia !== "function") return () => undefined;
  const media = window.matchMedia(QUERY);
  media.addEventListener("change", listener);
  return () => media.removeEventListener("change", listener);
}

function mobileSnapshot() {
  return typeof window.matchMedia === "function" && window.matchMedia(QUERY).matches;
}

export function useMobileViewport() {
  return useSyncExternalStore(subscribe, mobileSnapshot, () => false);
}

export function useResponsiveDrawer() {
  const mobile = useMobileViewport();
  const [open, setOpen] = useState(false);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLElement>(null);

  const close = useCallback((returnFocus = true) => {
    setOpen(false);
    if (returnFocus) triggerRef.current?.focus();
  }, []);
  const show = useCallback(() => setOpen(true), []);

  useEffect(() => {
    if (!mobile || !open) return;
    panelRef.current?.querySelector<HTMLElement>("button, a, input, summary")?.focus();
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        close();
      }
    };
    document.addEventListener("keydown", escape);
    return () => document.removeEventListener("keydown", escape);
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
