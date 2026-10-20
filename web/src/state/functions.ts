// Wizard checkbox logic (SPEC §6.1). The rules come from the engine
// (/api/functions); this only applies them.

import type { FunctionDef } from "./engine";

/** Everything `key` needs, transitively, not including itself. */
export function prerequisites(defs: FunctionDef[], key: string): string[] {
  const byKey = new Map(defs.map((d) => [d.key, d]));
  const out = new Set<string>();
  const stack = [...(byKey.get(key)?.requires ?? [])];
  while (stack.length) {
    const k = stack.pop()!;
    if (!out.has(k)) {
      out.add(k);
      stack.push(...(byKey.get(k)?.requires ?? []));
    }
  }
  return defs.map((d) => d.key).filter((k) => out.has(k));
}

/** Everything that needs `key`, transitively. */
export function dependents(defs: FunctionDef[], key: string): string[] {
  return defs.map((d) => d.key).filter((k) => prerequisites(defs, k).includes(key));
}

/** Turning a function on also turns on what it needs; turning one off also turns
 * off what needs it. Result is in the engine's canonical order. */
export function toggle(defs: FunctionDef[], selected: string[], key: string, on: boolean): string[] {
  const next = new Set(selected);
  if (on) {
    next.add(key);
    prerequisites(defs, key).forEach((k) => next.add(k));
  } else {
    next.delete(key);
    dependents(defs, key).forEach((k) => next.delete(k));
  }
  return defs.map((d) => d.key).filter((k) => next.has(k));
}

/** A one-line explanation shown next to a checkbox, e.g. "Requires Transcribe". */
export function explain(defs: FunctionDef[], key: string): string {
  const labels = new Map(defs.map((d) => [d.key, d.label]));
  const parts: string[] = [];
  const pre = prerequisites(defs, key).map((k) => labels.get(k) ?? k);
  if (pre.length) parts.push(`Requires ${pre.join(", ")}`);
  if (defs.find((d) => d.key === key)?.needs_text) parts.push("needs an imported manuscript");
  return parts.join("; ");
}
