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
  /** An extra that repeats neighbouring text (a stutter or restart): cleanup, not a flub. */
  repeat?: boolean;
  /** Mean recognition confidence of the heard words. */
  confidence?: number | null;
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

export type IssueKind = "flub" | "missing_line" | "silence";
export type IssueSeverity = "high" | "medium" | "low";
export type IssueStatus = "open" | "resolved" | "dismissed";
export type IssueChange = "new" | "still_open" | "auto_resolved" | "regressed";

export interface Issue {
  id: number;
  stable_key: string;
  kind: IssueKind;
  severity: IssueSeverity;
  status: IssueStatus;
  decided_by: "auto" | "user" | null;
  note: string;
  change: IssueChange;
  last_run_id: number;
  source_file_id: number | null;
  source_label: string | null;
  start: number | null;
  end: number | null;
  page_label: string | null;
  manuscript_unit_id: number | null;
  sentence_text: string;
  expected: string;
  heard: string;
  detail: {
    diffs?: ReadingDiff[];
    complete?: boolean;
    likely_misheard?: boolean;
    takes?: number;
    seconds?: number;
    boundary?: "sentence" | "paragraph" | "chapter" | "unknown";
    threshold?: number;
    suggested_s?: number;
  };
}

export interface IssueList {
  issues: Issue[];
  /** The latest check run: "change" badges apply to issues it touched. */
  last_check_run: number | null;
  stale: boolean;
}

export interface IssueSettings {
  silence_sentence_s: number;
  silence_paragraph_s: number;
  silence_chapter_s: number;
  similar_spelling: number;
  low_confidence: number;
}

export type RegionKind = "stutter" | "false_start" | "commentary" | "pause" | "cut" | "take";
export type RegionStatus = "removed" | "restored" | "suggested";

/** Material taken out of the timeline (a removed region of one recording). */
export interface Region {
  id: number;
  source_file_id: number;
  source_label: string;
  kind: RegionKind;
  status: RegionStatus;
  origin: "auto" | "user";
  decided_by: "auto" | "user";
  confidence: number | null;
  reason: string;
  text: string;
  start: number; // seconds in the recording
  end: number;
  seconds: number;
  /** Timeline seconds where it sits (or would sit if restored). */
  timeline_at: number | null;
  issue_id: number | null;
}

export interface Edl {
  rate: number;
  duration: number;
  original_duration: number;
  clips: { source_id: number; source_in: number; source_out: number; timeline_start: number; timeline_end: number }[];
  regions: Region[];
  undo: string | null;
  redo: string | null;
}

export interface HistoryEntry {
  seq: number;
  op_type: string;
  origin: "auto" | "user";
  summary: string;
  created_at: string;
  state: "done" | "undone" | "dropped";
}

export interface CleanupSettings {
  auto_apply: number;
  stutters: boolean;
  false_starts: boolean;
  commentary: boolean;
}

export type TakeStatus = "preferred" | "alternate" | "rejected";

export interface Take {
  id: number;
  take_group_id: number;
  source_file_id: number;
  source_label: string;
  start: number;
  end: number;
  span_end: number;
  coverage: number;
  score: number;
  hard_errors: number;
  soft_errors: number;
  extras: number;
  loudness_db: number | null;
  rank: number;
  status: TakeStatus;
  decided_by: "auto" | "user";
  heard: string;
  diffs: ReadingDiff[];
}

export interface TakeGroup {
  id: number;
  manuscript_unit_id: number | null;
  sentence_text: string;
  page_label: string | null;
  state: "suggested" | "confirmed";
  confident: boolean;
  reason: string;
  previous_take_id: number | null;
  takes: Take[]; // in recording order is up to the UI; ranked by the engine
}

export interface TakesResponse {
  groups: TakeGroup[];
  undo: string | null;
  redo: string | null;
}

export interface ClipRef {
  clip_id?: number | null;
  source_id?: number | null;
  source_in?: number | null;
}

export interface TimelineClip {
  ref: ClipRef;
  track_id: number;
  source_id: number;
  source_label: string;
  source_in: number; // seconds in the recording
  source_out: number;
  timeline_start: number; // seconds on the timeline
  timeline_end: number;
  gain_db: number;
  fade_in: number;
  fade_out: number;
  muted: boolean;
}

export interface TimelineTrack {
  id: number;
  name: string;
  kind: "narration" | "audio";
  gain_db: number;
  pan: number;
  mute: boolean;
  solo: boolean;
}

export interface TimelineView {
  rate: number;
  duration: number;
  channels: number;
  tracks: TimelineTrack[];
  clips: TimelineClip[];
  markers: { id: number; label: string; color: string; t: number | null }[];
  issues: { id: number; kind: IssueKind; severity: IssueSeverity; t: number; color: string; label: string }[];
  removed: {
    id: number;
    kind: RegionKind;
    status: RegionStatus;
    t: number | null;
    seconds: number;
    text: string;
    source_id: number;
    start: number;
    end: number;
  }[];
  undo: string | null;
  redo: string | null;
}

export interface Lanes {
  words: { text: string; t0: number; t1: number }[];
  sentences: { ordinal: number; t0: number; t1: number }[];
  pauses: { t0: number; t1: number }[];
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
  peaks: (sourceId: number, level: number, start = 0, count?: number) =>
    request<PeaksResponse>(
      "GET",
      `/api/project/sources/${sourceId}/peaks?level=${level}&start=${start}${count ? `&count=${count}` : ""}`,
    ),
  cancelJob: (jobId: string) => request<Job>("POST", `/api/jobs/${jobId}/cancel`),
  previewManuscript: (source: ManuscriptSource, settings: Partial<PageSettings>) =>
    request<ManuscriptPreview>("POST", "/api/project/manuscript/preview", { source, settings }),
  importManuscript: (source: ManuscriptSource, settings: Partial<PageSettings>) =>
    request<{ job_id: string }>("POST", "/api/project/manuscript", { source, settings }),
  remapManuscript: (settings: Partial<PageSettings>) =>
    request<{ job_id: string }>("PUT", "/api/project/manuscript/settings", { settings }),
  asrInfo: () => request<AsrInfo>("GET", "/api/asr"),
  /** model: null when no stage transcribes (checks only). */
  runAnalysis: (stages: string[] | null, model: string | null) =>
    request<{ job_id: string }>("POST", "/api/project/analysis", { stages, asr: model ? { model } : {} }),
  comparison: () => request<Comparison | null>("GET", "/api/project/comparison"),
  issues: () => request<IssueList>("GET", "/api/project/issues"),
  edl: () => request<Edl>("GET", "/api/project/edl"),
  takes: () => request<TakesResponse>("GET", "/api/project/takes"),
  timeline: () => request<TimelineView>("GET", "/api/project/timeline"),
  lanes: (start: number, end: number) =>
    request<Lanes>("GET", `/api/project/timeline/lanes?start=${start.toFixed(3)}&end=${end.toFixed(3)}`),
  tl: <T extends object>(path: string, body: T) =>
    request<TimelineView>("POST", `/api/project/timeline/${path}`, body),
  tlPatch: <T extends object>(path: string, body: T) =>
    request<TimelineView>("PATCH", `/api/project/timeline/${path}`, body),
  tlDelete: (path: string) => request<TimelineView>("DELETE", `/api/project/timeline/${path}`),
  chooseTake: (groupId: number, takeId: number) =>
    request<TakesResponse>("POST", `/api/project/takes/${groupId}/choose`, { take_id: takeId }),
  takeBack: (groupId: number) => request<TakesResponse>("POST", `/api/project/takes/${groupId}/back`),
  confirmTakes: (ids: number[]) => request<TakesResponse>("POST", "/api/project/takes/confirm", { ids }),
  markTake: (takeId: number, status: "alternate" | "rejected") =>
    request<TakesResponse>("POST", `/api/project/takes/take/${takeId}/mark`, { status }),
  setRegionStatus: (ids: number[], status: "removed" | "restored") =>
    request<Edl>("POST", "/api/project/edl/regions/status", { ids, status }),
  editRegion: (id: number, bounds: { start?: number; end?: number }) =>
    request<Edl>("PATCH", `/api/project/edl/regions/${id}`, bounds),
  rollRegion: (id: number, delta: number) => request<Edl>("POST", `/api/project/edl/regions/${id}/roll`, { delta }),
  undo: () => request<Edl>("POST", "/api/project/edl/undo"),
  redo: () => request<Edl>("POST", "/api/project/edl/redo"),
  history: () =>
    request<{ entries: HistoryEntry[]; undo: string | null; redo: string | null }>("GET", "/api/project/edl/history"),
  shortenPause: (issueId: number, keep?: number) =>
    request<Edl>("POST", `/api/project/issues/${issueId}/shorten`, keep === undefined ? {} : { keep }),
  cleanupSettings: () => request<CleanupSettings>("GET", "/api/project/cleanup-settings"),
  saveCleanupSettings: (s: Partial<CleanupSettings>) =>
    request<CleanupSettings>("PUT", "/api/project/cleanup-settings", s),
  decideIssues: (ids: number[], change: { status?: IssueStatus; note?: string }) =>
    request<{ issues: Issue[] }>("PATCH", "/api/project/issues", { ids, ...change }),
  issueSettings: () => request<IssueSettings>("GET", "/api/project/issue-settings"),
  saveIssueSettings: (s: Partial<IssueSettings>) =>
    request<IssueSettings>("PUT", "/api/project/issue-settings", s),
  manuscriptUnits: (kind: ManuscriptUnit["kind"], offset: number, limit: number) =>
    request<{ total: number; offset: number; units: ManuscriptUnit[] }>(
      "GET",
      `/api/project/manuscript/units?kind=${kind}&offset=${offset}&limit=${limit}`,
    ),
};

/** All buckets of one level. The engine pages large responses, and a 10-hour
 * hi-res file can exceed one page even at the coarsest level. */
/** The audio around [start, end] (the engine adds ±3 s of context), as WAV. */
export function excerptUrl(sourceId: number, start: number, end: number, context?: number): string {
  const ctx = context === undefined ? "" : `&context=${context}`;
  return `/api/project/sources/${sourceId}/excerpt?start=${start.toFixed(3)}&end=${end.toFixed(3)}${ctx}`;
}

/** The edited timeline [start, end) seconds, rendered from the sources, as WAV. */
export function renderUrl(start: number, end: number): string {
  return `/api/project/edl/render?start=${Math.max(0, start).toFixed(3)}&end=${end.toFixed(3)}`;
}

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
