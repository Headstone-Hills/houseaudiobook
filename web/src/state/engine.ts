// Client for the local engine API. The UI never touches audio files directly;
// everything goes through these calls (SPEC §3).

export interface Health {
  status: string;
  version: string;
}

export interface FunctionDef {
  key: string;
  label: string;
  requires: string[];
  needs_text: boolean;
}

export interface Recent {
  path: string;
  name: string;
  opened_at: string;
}

export interface PeakSettings {
  base: number;
  factor: number;
  levels: number;
}

export interface Settings {
  default_location: string;
  recent: Recent[];
  peaks: PeakSettings;
  page_defaults: PageSettings;
}

export type SourceStatus = "ok" | "missing" | "modified" | "checking";

export interface Source {
  id: number;
  mode: "copy" | "reference";
  path: string;
  original_path: string;
  original_name: string;
  sha256: string;
  size_bytes: number;
  format: string;
  codec: string;
  lossless: boolean;
  sample_rate: number;
  channels: number;
  bit_depth: number | null;
  bit_rate: number | null;
  num_frames: number;
  duration: number;
  label: string;
  order_index: number;
  imported_at: string;
  status: SourceStatus;
}

export interface ProjectSummary {
  name: string;
  path: string;
  functions: string[];
  sources: Source[];
  warnings: string[];
  manuscript: ManuscriptInfo | null;
}

export type PageMode = "auto" | "markers" | "breaks" | "pdf" | "estimate";

/** Mirrors engine PageSettings (text/pages.py). */
export interface PageSettings {
  mode: PageMode;
  marker_pattern: string;
  form_feed_breaks: boolean;
  words_per_page: number;
  first_numbered_page: number;
  use_pdf_labels: boolean;
  skip_front_matter: boolean;
}

export type ManuscriptFormat = "txt" | "md" | "docx" | "pdf";
export type UnitCounts = { chapter: number; paragraph: number; sentence: number };

export interface ManuscriptInfo {
  original_name: string;
  original_path: string;
  sha256: string;
  size_bytes: number;
  format: ManuscriptFormat;
  settings: PageSettings;
  page_mode: Exclude<PageMode, "auto">;
  page_count: number;
  estimated: boolean;
  warnings: string[];
  counts: UnitCounts;
  imported_at: string;
  mapped_at: string;
  status: "ok" | "missing" | "modified" | "checking";
}

export interface ManuscriptUnit {
  id?: number;
  kind: "chapter" | "paragraph" | "sentence";
  ordinal: number;
  parent_id?: number | null;
  text: string;
  char_start: number;
  char_end: number;
  page: number | null;
  page_label: string;
  page_end: number | null;
  page_end_label: string;
  page_estimated: boolean;
  front_matter: boolean;
}

export interface ManuscriptPreview {
  name: string;
  format: ManuscriptFormat;
  page_source: "markers" | "breaks" | "pdf" | "none";
  page_mode: Exclude<PageMode, "auto">;
  page_count: number;
  estimated: boolean;
  counts: UnitCounts;
  warnings: string[];
  settings: PageSettings;
  pages: { label: string; physical: number | null; front_matter: boolean; first_text: string }[];
  sentences: ManuscriptUnit[];
}

export interface AsrInfo {
  installed: boolean;
  models: { name: string; size_mb: number }[];
  default: { model: string };
}

export type DiffKind = "missing" | "extra" | "substitution" | "transposition" | "reordered" | "mispronunciation";

export interface ReadingDiff {
  kind: DiffKind;
  expected: string;
  heard: string;
  start: number | null;
  end: number | null;
}

export interface SentenceReading {
  source_id: number;
  source_label: string;
  start: number;
  end: number;
  coverage: number;
  score: number;
  heard: string;
  diffs: ReadingDiff[];
}

export interface SentenceComparison {
  unit_id: number;
  ordinal: number;
  text: string;
  page_label: string;
  page_end_label: string;
  readings: SentenceReading[];
  best_score: number | null;
}

export interface Comparison {
  run_id: number;
  /** The manuscript was re-mapped after this run: results are out of date. */
  stale: boolean;
  sentences: SentenceComparison[];
  unaligned: { source_file_id: number; source_label: string; start: number; end: number; text: string }[];
}

/** Where a manuscript comes from: a local path, an upload, or the project's copy. */
export type ManuscriptSource = { path: string } | { upload_id: string } | { current: true };

export type JobStatus = "queued" | "running" | "done" | "failed" | "cancelled";

export interface Job {
  id: string;
  kind: string;
  label: string;
  status: JobStatus;
  progress: number;
  message: string;
  result: unknown;
  error: string | null;
}

export interface PeaksResponse {
  source_id: number;
  sample_rate: number;
  num_frames: number;
  level: number;
  frames_per_bucket: number;
  total_buckets: number;
  start: number;
  min: number[];
  max: number[];
}

export class EngineError extends Error {}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const resp = await fetch(url, {
    method,
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!resp.ok) {
    let detail = `engine returned ${resp.status}`;
    try {
      const data = (await resp.json()) as { detail?: unknown };
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      // keep the status-code message
    }
    throw new EngineError(detail);
  }
  return (await resp.json()) as T;
}

export const api = {
  health: () => request<Health>("GET", "/api/health"),
  settings: () => request<Settings>("GET", "/api/settings"),
  functions: () => request<FunctionDef[]>("GET", "/api/functions"),
  createProject: (location: string, name: string, functions: string[]) =>
    request<ProjectSummary>("POST", "/api/projects", { location, name, functions }),
  openProject: (path: string) => request<ProjectSummary>("POST", "/api/projects/open", { path }),
  closeProject: () => request<{ closed: boolean }>("POST", "/api/project/close"),
  /** The open project, or null if none is open. */
  project: () => request<ProjectSummary | null>("GET", "/api/project"),
  importPaths: (paths: string[], mode: "copy" | "reference") =>
    request<{ job_id: string }>("POST", "/api/project/sources/import", { paths, mode }),
  relink: (sourceId: number, path: string) =>
    request<ProjectSummary>("POST", `/api/project/sources/${sourceId}/relink`, { path }),
  peaks: (sourceId: number, level: number, start = 0) =>
    request<PeaksResponse>(
      "GET",
      `/api/project/sources/${sourceId}/peaks?level=${level}&start=${start}`,
    ),
  cancelJob: (jobId: string) => request<Job>("POST", `/api/jobs/${jobId}/cancel`),
  previewManuscript: (source: ManuscriptSource, settings: Partial<PageSettings>) =>
    request<ManuscriptPreview>("POST", "/api/project/manuscript/preview", { source, settings }),
  importManuscript: (source: ManuscriptSource, settings: Partial<PageSettings>) =>
    request<{ job_id: string }>("POST", "/api/project/manuscript", { source, settings }),
  remapManuscript: (settings: Partial<PageSettings>) =>
    request<{ job_id: string }>("PUT", "/api/project/manuscript/settings", { settings }),
  asrInfo: () => request<AsrInfo>("GET", "/api/asr"),
  runAnalysis: (stages: string[] | null, model: string) =>
    request<{ job_id: string }>("POST", "/api/project/analysis", { stages, asr: { model } }),
  comparison: () => request<Comparison | null>("GET", "/api/project/comparison"),
  manuscriptUnits: (kind: ManuscriptUnit["kind"], offset: number, limit: number) =>
    request<{ total: number; offset: number; units: ManuscriptUnit[] }>(
      "GET",
      `/api/project/manuscript/units?kind=${kind}&offset=${offset}&limit=${limit}`,
    ),
};

/** All buckets of one level. The engine pages large responses, and a 10-hour
 * hi-res file can exceed one page even at the coarsest level. */
export async function fetchAllPeaks(sourceId: number, level: number): Promise<PeaksResponse> {
  const first = await api.peaks(sourceId, level);
  const min = [...first.min];
  const max = [...first.max];
  while (max.length < first.total_buckets) {
    const page = await api.peaks(sourceId, level, max.length);
    if (page.max.length === 0) break;
    min.push(...page.min);
    max.push(...page.max);
  }
  return { ...first, min, max };
}

/** Stream a file to the engine with a PUT. XHR rather than fetch because fetch
 * can't report upload progress, and a narration file can be gigabytes. */
function uploadTo<T>(url: string, file: File, onProgress?: (fraction: number) => void): Promise<T> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `${url}?filename=${encodeURIComponent(file.name)}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total);
    };
    xhr.onload = () => {
      let data: (T & { detail?: string }) | undefined;
      try {
        data = JSON.parse(xhr.responseText) as T & { detail?: string };
      } catch {
        // fall through to the status-code message
      }
      if (xhr.status === 200 && data) resolve(data);
      else reject(new EngineError(data?.detail ?? `upload failed (${xhr.status})`));
    };
    xhr.onerror = () => reject(new EngineError("upload failed: engine unreachable"));
    xhr.send(file);
  });
}

/** Upload an audio file; resolves to the import job's id. */
export async function uploadFile(file: File, onProgress?: (fraction: number) => void): Promise<string> {
  const r = await uploadTo<{ job_id: string }>("/api/project/sources/upload", file, onProgress);
  return r.job_id;
}

/** Upload a manuscript for preview/import; resolves to its upload id. */
export async function uploadManuscript(file: File): Promise<string> {
  const r = await uploadTo<{ upload_id: string }>("/api/project/manuscript/upload", file);
  return r.upload_id;
}

/** Subscribe to job snapshots; reconnects if the engine restarts. Returns an unsubscribe. */
export function subscribeJobs(onJobs: (jobs: Job[]) => void): () => void {
  let ws: WebSocket | null = null;
  let stopped = false;
  let retry: ReturnType<typeof setTimeout> | undefined;

  const connect = () => {
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    ws = new WebSocket(`${proto}//${location.host}/api/jobs/ws`);
    ws.onmessage = (e: MessageEvent<string>) => {
      const msg = JSON.parse(e.data) as { type: string; jobs: Job[] };
      if (msg.type === "jobs") onJobs(msg.jobs);
    };
    ws.onclose = () => {
      if (!stopped) retry = setTimeout(connect, 1000);
    };
  };
  connect();
  return () => {
    stopped = true;
    clearTimeout(retry);
    ws?.close();
  };
}
