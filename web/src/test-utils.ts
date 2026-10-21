import { vi } from "vitest";
import type { FunctionDef, ProjectSummary, Settings, Source } from "./state/engine";

type Handler = unknown | ((body: unknown, url: string) => unknown);

/** Route fetch calls by "METHOD /path" (query string ignored). Unrouted calls 404.
 * Returns the mock so tests can assert on calls. */
export function mockApi(routes: Record<string, Handler>) {
  const fn = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input), "http://ui.test");
    const key = `${init?.method ?? "GET"} ${url.pathname}`;
    if (!(key in routes)) {
      return Promise.resolve(new Response(JSON.stringify({ detail: `unrouted ${key}` }), { status: 404 }));
    }
    const h = routes[key];
    const body = init?.body ? (JSON.parse(String(init.body)) as unknown) : undefined;
    const value = typeof h === "function" ? (h as (b: unknown, u: string) => unknown)(body, url.toString()) : h;
    // A handler may answer later (return a Promise) to test edits racing a slow engine.
    const respond = (v: unknown) => (v instanceof Response ? v : new Response(JSON.stringify(v)));
    return value instanceof Promise ? value.then(respond) : Promise.resolve(respond(value));
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

export const FUNCTIONS: FunctionDef[] = [
  { key: "transcribe", label: "Transcribe", requires: [], needs_text: false },
  { key: "compare_text", label: "Compare to Text", requires: ["transcribe"], needs_text: true },
  { key: "organize", label: "Organize and Label Files", requires: [], needs_text: false },
  { key: "cleanup", label: "Clean up", requires: ["transcribe"], needs_text: false },
  { key: "flubs", label: "Mark flubbed lines", requires: ["compare_text"], needs_text: false },
  { key: "silences", label: "Mark overlong silences", requires: [], needs_text: false },
  { key: "takes", label: "Compare takes", requires: ["compare_text"], needs_text: false },
];

export const SETTINGS: Settings = {
  default_location: "/Users/me/HillAudiobook Projects",
  recent: [{ path: "/p/Old Book.hillproj", name: "Old Book", opened_at: "2026-10-01T00:00:00+00:00" }],
  peaks: { base: 512, factor: 4, levels: 4 },
  page_defaults: {
    mode: "auto",
    marker_pattern: String.raw`\[\s*p(?:age|g)?\.?\s*(\d+)\s*\]`,
    form_feed_breaks: true,
    words_per_page: 250,
    first_numbered_page: 1,
    use_pdf_labels: true,
    skip_front_matter: false,
  },
};

export function source(overrides: Partial<Source> = {}): Source {
  return {
    id: 1,
    mode: "copy",
    path: "sources/abc_ch01.wav",
    original_path: "/rec/ch01.wav",
    original_name: "ch01.wav",
    sha256: "abc",
    size_bytes: 1_048_576,
    format: "wav",
    codec: "pcm_s24le",
    lossless: true,
    sample_rate: 48000,
    channels: 1,
    bit_depth: 24,
    bit_rate: null,
    num_frames: 48000 * 65,
    duration: 65,
    label: "ch01",
    order_index: 0,
    imported_at: "2026-10-20T00:00:00+00:00",
    status: "ok",
    ...overrides,
  };
}

export function project(overrides: Partial<ProjectSummary> = {}): ProjectSummary {
  return {
    name: "Book",
    path: "/p/Book.hillproj",
    functions: FUNCTIONS.map((f) => f.key),
    sources: [],
    warnings: [],
    manuscript: null,
    ...overrides,
  };
}
