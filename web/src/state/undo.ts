import { useLayoutEffect } from "react";

/** ⌘/Ctrl+Z undo and Shift+⌘/Ctrl+Z (or ⌘/Ctrl+Y) redo from anywhere on the page,
 * except while typing. A null handler: nothing to undo or redo. A layout effect,
 * so the listener sees the new state in the same commit that shows it: a passive
 * effect left a gap where a quick Shift+Ctrl+Z after an undo was ignored. */
export function useUndoShortcuts(undo: (() => void) | null, redo: (() => void) | null, enabled = true) {
  useLayoutEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName ?? "";
      if (["INPUT", "TEXTAREA", "SELECT"].includes(tag) || !(e.metaKey || e.ctrlKey)) return;
      const key = e.key.toLowerCase();
      if (key === "z" && !e.shiftKey && undo) {
        e.preventDefault();
        undo();
      } else if (((key === "z" && e.shiftKey) || key === "y") && redo) {
        e.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  });
}
