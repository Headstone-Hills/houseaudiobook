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
}

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
  project: () => request<ProjectSummary>("GET", "/api/project"),
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

/** Stream a dropped or picked file to the engine. XHR rather than fetch because
 * fetch can't report upload progress, and a narration file can be gigabytes. */
export function uploadFile(file: File, onProgress?: (fraction: number) => void): Promise<string> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/project/sources/upload?filename=${encodeURIComponent(file.name)}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded / e.total);
    };
    xhr.onload = () => {
      try {
        const data = JSON.parse(xhr.responseText) as { job_id?: string; detail?: string };
        if (xhr.status === 200 && data.job_id) resolve(data.job_id);
        else reject(new EngineError(data.detail ?? `upload failed (${xhr.status})`));
      } catch {
        reject(new EngineError(`upload failed (${xhr.status})`));
      }
    };
    xhr.onerror = () => reject(new EngineError("upload failed: engine unreachable"));
    xhr.send(file);
  });
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
