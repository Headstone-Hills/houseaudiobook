// Pure waveform math: pick a peak level for a zoom, and fold buckets into pixel columns.

import type { PeakSettings } from "./engine";

/** The coarsest level that still gives at least one bucket per pixel
 * (so the drawing is never blocky), or level 0 when zoomed far in. */
export function pickLevel(numFrames: number, widthPx: number, s: PeakSettings): number {
  for (let level = s.levels - 1; level > 0; level--) {
    const fpb = s.base * s.factor ** level;
    if (Math.ceil(numFrames / fpb) >= widthPx) return level;
  }
  return 0;
}

export interface Columns {
  min: Float32Array; // -1..1
  max: Float32Array;
}

/** Fold int16 min/max buckets into exactly `width` columns, keeping the envelope. */
export function toColumns(min: number[], max: number[], width: number): Columns {
  const out = { min: new Float32Array(width), max: new Float32Array(width) };
  const n = max.length;
  if (n === 0 || width <= 0) return out;
  for (let x = 0; x < width; x++) {
    const a = Math.floor((x * n) / width);
    const b = Math.max(a + 1, Math.floor(((x + 1) * n) / width));
    let lo = Infinity;
    let hi = -Infinity;
    for (let i = a; i < b && i < n; i++) {
      lo = Math.min(lo, min[i]!);
      hi = Math.max(hi, max[i]!);
    }
    out.min[x] = lo / 32767;
    out.max[x] = hi / 32767;
  }
  return out;
}
