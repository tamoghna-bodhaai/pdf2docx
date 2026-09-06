"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import type { JobDto } from "@/lib/api/types";

const KEY = "pdf2docx-desktop-notifications";
const TERMINAL = new Set<JobDto["status"]>(["done", "error", "cancelled"]);

interface GroupState { activeSeen: boolean; terminal: boolean; notified: boolean }
export interface CompletionNotice { id: number; message: string }
type Capability = "checking" | "available" | "blocked" | "unsupported";
const notificationListeners = new Set<() => void>();

function subscribeNotifications(listener: () => void) {
  notificationListeners.add(listener);
  return () => { notificationListeners.delete(listener); };
}

function emitNotificationChange() {
  notificationListeners.forEach((listener) => listener());
}

function capabilitySnapshot(): Capability {
  if (!("Notification" in window)) return "unsupported";
  return Notification.permission === "denied" ? "blocked" : "available";
}

function enabledSnapshot() {
  return capabilitySnapshot() === "available" && preference() && Notification.permission === "granted";
}

function preference() {
  try { return localStorage.getItem(KEY) === "enabled"; } catch { return false; }
}

function completionSummary(jobs: JobDto[]) {
  const remaining = jobs.filter((job) => job.status !== "cancelled");
  const single = remaining.length === 1 ? remaining[0] : jobs.length === 1 ? jobs[0] : undefined;
  if (single) {
    const job = single;
    const name = job.output_filename || job.filename;
    if (job.status === "done") return `Conversion finished: ${name} is ready.`;
    if (job.status === "error") return `Conversion finished: ${name} failed.`;
    return `Conversion finished: ${name} was cancelled.`;
  }
  const completed = jobs.filter((job) => job.status === "done").length;
  const failed = jobs.filter((job) => job.status === "error").length;
  const cancelled = jobs.filter((job) => job.status === "cancelled").length;
  const parts = [completed && `${completed} completed`, failed && `${failed} failed`, cancelled && `${cancelled} cancelled`].filter(Boolean);
  return `Batch finished: ${parts.join(", ") || "no completed files"}.`;
}

export function useNotifications(jobs: JobDto[], onOpenHistory?: () => void) {
  // Client Components are also prerendered during a static export. Server
  // snapshots keep hydration deterministic, then React reads browser support
  // and the stored preference from this tiny external store.
  const capability = useSyncExternalStore(subscribeNotifications, capabilitySnapshot, () => "checking");
  const enabled = useSyncExternalStore(subscribeNotifications, enabledSnapshot, () => false);
  const [notices, setNotices] = useState<CompletionNotice[]>([]);
  const groupStates = useRef(new Map<string, GroupState>());
  const nextNotice = useRef(0);
  const openHistory = useRef(onOpenHistory);
  useEffect(() => { openHistory.current = onOpenHistory; }, [onOpenHistory]);

  const dismiss = useCallback((id: number) => {
    setNotices((current) => current.filter((notice) => notice.id !== id));
  }, []);

  const show = useCallback((message: string) => {
    const id = ++nextNotice.current;
    setNotices((current) => [...current, { id, message }]);
    window.setTimeout(() => dismiss(id), 7_000);
    const backgrounded = document.hidden || (typeof document.hasFocus === "function" && !document.hasFocus());
    if (!enabled || capability !== "available" || Notification.permission !== "granted" || !backgrounded) return;
    try {
      const notification = new Notification("PDF2DOCX", { body: message });
      notification.onclick = () => { window.focus(); openHistory.current?.(); notification.close(); };
    } catch { /* A completion toast remains available when the platform call fails. */ }
  }, [capability, dismiss, enabled]);

  useEffect(() => {
    const groups = new Map<string, JobDto[]>();
    for (const job of jobs) {
      const key = job.batch_id ? `batch:${job.batch_id}` : `job:${job.id}`;
      groups.set(key, [...(groups.get(key) ?? []), job]);
    }
    for (const [key, members] of groups) {
      const terminal = members.length > 0 && members.every((job) => TERMINAL.has(job.status));
      const previous = groupStates.current.get(key);
      if (!previous) {
        groupStates.current.set(key, { activeSeen: !terminal, terminal, notified: terminal });
        continue;
      }
      if (!terminal) previous.activeSeen = true;
      if (terminal && !previous.terminal && previous.activeSeen && !previous.notified) {
        previous.notified = true;
        show(completionSummary(members));
      }
      previous.terminal = terminal;
    }
  }, [jobs, show]);

  const toggle = useCallback(async () => {
    if (capability !== "available" || Notification.permission === "denied") return;
    if (enabled) {
      try { localStorage.setItem(KEY, "disabled"); } catch { /* Storage is optional. */ }
      emitNotificationChange();
      return;
    }
    let permission: NotificationPermission = Notification.permission;
    if (permission !== "granted") {
      try { permission = await Notification.requestPermission(); } catch { permission = "denied"; }
    }
    const next = permission === "granted";
    try { localStorage.setItem(KEY, next ? "enabled" : "disabled"); } catch { /* Storage is optional. */ }
    emitNotificationChange();
  }, [capability, enabled]);

  const status = capability === "checking" ? "Checking…" : capability === "unsupported" ? "Not supported by this browser" : capability === "blocked" ? "Blocked in browser settings" : enabled ? "On" : "Off";
  return { enabled, status, disabled: capability !== "available", notices, dismiss, toggle };
}
