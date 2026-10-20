/** 3725.5 -> "1:02:05.5"; 65 -> "1:05.0". Tenths are enough for a source list. */
export function formatDuration(seconds: number): string {
  const tenths = Math.round(seconds * 10);
  const h = Math.floor(tenths / 36000);
  const m = Math.floor((tenths % 36000) / 600);
  const s = (tenths % 600) / 10;
  const ss = s.toFixed(1).padStart(4, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

export function formatBytes(n: number): string {
  const units = ["B", "KB", "MB", "GB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${i === 0 ? n : n.toFixed(1)} ${units[i]}`;
}

export function describeFormat(s: {
  format: string;
  sample_rate: number;
  channels: number;
  bit_depth: number | null;
  bit_rate: number | null;
}): string {
  const ch = s.channels === 1 ? "mono" : s.channels === 2 ? "stereo" : `${s.channels} ch`;
  const depth = s.bit_depth ? `${s.bit_depth}-bit` : s.bit_rate ? `${Math.round(s.bit_rate / 1000)} kbps` : "";
  return [s.format.toUpperCase(), `${(s.sample_rate / 1000).toString()} kHz`, depth, ch]
    .filter(Boolean)
    .join(" · ");
}
