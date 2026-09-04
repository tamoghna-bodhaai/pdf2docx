/**
 * Typed seam around the mature Mathpix-Markdown normalizer.
 *
 * The runtime is the proven parser ported byte-for-byte from the former static
 * client. Marked and KaTeX remain npm dependencies owned by the React viewer;
 * callers only learn this small preparation/restoration interface.
 */
export interface PreparedMmd { markdown: string; math: string[] }
interface MmdRuntime {
  prepare(source: unknown): PreparedMmd;
  restore(html: string, math: string[]): string;
}

// The parser intentionally remains plain JavaScript so its large, battle-tested
// state machine is unchanged by the framework migration. This adapter supplies
// the strict public types used everywhere else.
// @ts-expect-error The adjacent runtime is encapsulated by MmdRuntime.
import runtimeModule from "./mmd-runtime.js";
const runtime = runtimeModule as MmdRuntime;

export function prepareMmd(source: unknown): PreparedMmd { return runtime.prepare(source); }
export function restoreMmd(html: string, math: string[]): string { return runtime.restore(html, math); }
