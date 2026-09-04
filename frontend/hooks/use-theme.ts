"use client";

import { useCallback, useSyncExternalStore } from "react";

export type Theme = "light" | "dark";
const KEY = "pdf2docx-theme";
const listeners = new Set<() => void>();

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

function currentTheme(): Theme {
  return document.documentElement.dataset.theme === "dark" ? "dark" : "light";
}

export function useTheme() {
  // The server snapshot matches the exported HTML. The inline layout script
  // applies the stored theme before paint; React reads that DOM state only
  // after hydration, avoiding both a light flash and a hydration mismatch.
  const theme = useSyncExternalStore(subscribe, currentTheme, () => "light");
  const setTheme = useCallback((next: Theme) => {
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem(KEY, next); } catch { /* Theme still applies for this page. */ }
    listeners.forEach((listener) => listener());
  }, []);
  const toggle = useCallback(() => setTheme(theme === "light" ? "dark" : "light"), [setTheme, theme]);
  return { theme, setTheme, toggle };
}
