import { useEffect, useRef, useState } from "react";
import { fetchAllPeaks, type PeakSettings, type Source } from "../state/engine";
import { pickLevel, toColumns } from "../state/peaks";
import { formatDuration } from "../state/format";

const WIDTH = 1200;
const HEIGHT = 120;

/** Whole-file overview drawn from the engine's precomputed peaks (SPEC §8). */
export function Waveform({ source, peakSettings }: { source: Source; peakSettings: PeakSettings }) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    const level = pickLevel(source.num_frames, WIDTH, peakSettings);
    fetchAllPeaks(source.id, level)
      .then((p) => {
        if (cancelled) return;
        setError(null);
        const ctx = canvas.current?.getContext("2d");
        if (!ctx) return; // e.g. no canvas support (tests)
        const cols = toColumns(p.min, p.max, WIDTH);
        const style = getComputedStyle(canvas.current!);
        ctx.clearRect(0, 0, WIDTH, HEIGHT);
        ctx.fillStyle = style.getPropertyValue("--wave").trim() || "#3b82f6";
        const mid = HEIGHT / 2;
        for (let x = 0; x < WIDTH; x++) {
          const top = mid - cols.max[x]! * mid;
          const bottom = mid - cols.min[x]! * mid;
          ctx.fillRect(x, top, 1, Math.max(1, bottom - top));
        }
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(String(e instanceof Error ? e.message : e));
      });
    return () => {
      cancelled = true;
    };
  }, [source.id, source.num_frames, peakSettings]);

  return (
    <figure className="waveform">
      <canvas
        ref={canvas}
        width={WIDTH}
        height={HEIGHT}
        role="img"
        aria-label={`Waveform of ${source.label}, ${formatDuration(source.duration)}`}
      />
      <figcaption>
        {source.label} · {formatDuration(source.duration)}
        {error && <span className="error"> · waveform unavailable: {error}</span>}
      </figcaption>
    </figure>
  );
}
