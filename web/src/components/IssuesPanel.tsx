import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import {
  api,
  excerptUrl,
  type Issue,
  type IssueKind,
  type IssueList,
  type IssueSettings,
  type IssueSeverity,
  type IssueStatus,
} from "../state/engine";
import {
  KIND_LABELS,
  SEVERITY_LABELS,
  counts,
  filterIssues,
  sortIssues,
  summary,
  where,
  type IssueFilter,
  type SortKey,
} from "../state/issues";
import { plural } from "../state/format";

const CHANGE_LABELS = { new: "New", still_open: "", auto_resolved: "Fixed", regressed: "Back again" } as const;

interface Props {
  /** The project's checking stages (flubs, silences) that are turned on. */
  checks: string[];
  canRun: boolean;
  revision: number; // bumped when an analysis or manuscript job finishes
  onError: (msg: string) => void;
}

/** The action list (SPEC §6.4): every flagged flub, unread line and long pause,
 * with keyboard shortcuts for review. Decisions survive re-runs (§6.7). */
export function IssuesPanel({ checks, canRun, revision, onError }: Props) {
  const [data, setData] = useState<IssueList | null>(null);
  const [filter, setFilter] = useState<IssueFilter>({ kind: "all", status: "open", severity: "all" });
  const [sort, setSort] = useState<SortKey>("page");
  const [currentId, setCurrentId] = useState<number | null>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [editing, setEditing] = useState<{ id: number; draft: string } | null>(null);
  const [playing, setPlaying] = useState<number | null>(null);
  const audio = useRef<HTMLAudioElement>(null);
  const rows = useRef(new Map<number, HTMLTableRowElement>());
  const fail = (e: unknown) => onError(e instanceof Error ? e.message : String(e));

  useEffect(() => {
    let cancelled = false;
    api
      .issues()
      .then((d) => !cancelled && setData(d))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [revision]);

  const shown = useMemo(() => sortIssues(filterIssues(data?.issues ?? [], filter), sort), [data, filter, sort]);
  const current = shown.find((i) => i.id === currentId) ?? null;
  const total = counts(data?.issues ?? []);

  const merge = (updated: Issue[]) =>
    setData((d) => {
      if (!d) return d;
      const by = new Map(updated.map((i) => [i.id, i]));
      return { ...d, issues: d.issues.map((i) => by.get(i.id) ?? i) };
    });

  const decide = (ids: number[], change: { status?: IssueStatus; note?: string }) => {
    if (!ids.length) return;
    api
      .decideIssues(ids, change)
      .then((r) => merge(r.issues))
      .catch(fail);
  };

  // Bulk actions apply to the checked rows, or else to the current one.
  const targets = () => (selected.size ? [...selected] : current ? [current.id] : []);

  const focusRow = (id: number) => {
    setCurrentId(id);
    rows.current.get(id)?.focus();
  };

  const move = (step: number) => {
    if (!shown.length) return;
    const at = current ? shown.indexOf(current) : -1;
    const next = shown[Math.min(shown.length - 1, Math.max(0, at + step))];
    if (next) focusRow(next.id);
  };

  const play = (i: Issue) => {
    const el = audio.current;
    if (!el || i.source_file_id === null || i.start === null || i.end === null) return;
    if (playing === i.id && !el.paused) {
      el.pause();
      return;
    }
    el.src = excerptUrl(i.source_file_id, i.start, i.end);
    setPlaying(i.id);
    el.play()?.catch(() => undefined); // jsdom and autoplay policies may refuse; the controls remain
  };

  const startNote = (i: Issue) => setEditing({ id: i.id, draft: i.note });
  const saveNote = () => {
    if (!editing) return;
    decide([editing.id], { note: editing.draft });
    const id = editing.id;
    setEditing(null);
    focusRow(id);
  };

  const onKeyDown = (e: KeyboardEvent<HTMLElement>) => {
    const tag = (e.target as HTMLElement).tagName;
    if (["INPUT", "TEXTAREA", "SELECT", "BUTTON"].includes(tag) || e.metaKey || e.ctrlKey || e.altKey) return;
    const handled = (() => {
      switch (e.key) {
        case "ArrowDown":
        case "j":
          move(1);
          return true;
        case "ArrowUp":
        case "k":
          move(-1);
          return true;
        case " ":
          if (current) play(current);
          return true;
        case "r":
          decide(targets(), { status: "resolved" });
          return true;
        case "d":
          decide(targets(), { status: "dismissed" });
          return true;
        case "o":
          decide(targets(), { status: "open" });
          return true;
        case "n":
          if (current) startNote(current);
          return true;
        case "x":
          if (current) toggle(current.id);
          return true;
        default:
          return false;
      }
    })();
    if (handled) e.preventDefault();
  };

  const toggle = (id: number) =>
    setSelected((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const rerun = () => {
    api.runAnalysis(checks, null).catch(fail);
  };

  return (
    <section aria-labelledby="issues-heading" className="issues">
      <h2 id="issues-heading">Issues</h2>
      <div className="run-row">
        <button type="button" onClick={rerun} disabled={!canRun || !checks.length}>
          Re-run checks
        </button>
        <span aria-live="polite">
          {data
            ? `${total.open} open · ${total.resolved} resolved · ${total.dismissed} dismissed`
            : "No checks run yet."}
        </span>
      </div>
      {data?.stale && (
        <p role="status" className="warning">
          The manuscript changed since the last comparison. Run Transcribe and compare again to re-check flubs; your
          decisions are kept.
        </p>
      )}

      <div className="issue-controls">
        <Choice
          label="Type"
          value={filter.kind}
          options={[["all", "All types"], ...(Object.entries(KIND_LABELS) as [IssueKind, string][])]}
          onChange={(kind) => setFilter({ ...filter, kind })}
        />
        <Choice
          label="Status"
          value={filter.status}
          options={[
            ["open", "Open"],
            ["resolved", "Resolved"],
            ["dismissed", "Dismissed"],
            ["all", "Any status"],
          ]}
          onChange={(status) => setFilter({ ...filter, status })}
        />
        <Choice
          label="Severity"
          value={filter.severity}
          options={[["all", "Any severity"], ...(Object.entries(SEVERITY_LABELS) as [IssueSeverity, string][])]}
          onChange={(severity) => setFilter({ ...filter, severity })}
        />
        <Choice
          label="Sort by"
          value={sort}
          options={[
            ["page", "Page"],
            ["time", "Recording time"],
            ["severity", "Severity"],
            ["type", "Type"],
          ]}
          onChange={setSort}
        />
      </div>

      <div className="run-row">
        <button type="button" onClick={() => decide(targets(), { status: "resolved" })} disabled={!targets().length}>
          Resolve{selected.size ? ` ${selected.size} selected` : ""}
        </button>
        <button type="button" onClick={() => decide(targets(), { status: "dismissed" })} disabled={!targets().length}>
          Dismiss{selected.size ? ` ${selected.size} selected` : ""}
        </button>
        <button type="button" onClick={() => decide(targets(), { status: "open" })} disabled={!targets().length}>
          Reopen{selected.size ? ` ${selected.size} selected` : ""}
        </button>
        {selected.size > 0 && (
          <button type="button" onClick={() => setSelected(new Set())}>
            Clear selection
          </button>
        )}
        <audio ref={audio} controls aria-label="Issue audio" onEnded={() => setPlaying(null)} />
      </div>
      <p className="muted" id="issue-keys">
        Keys: ↑/↓ or j/k move · Space play (±3 s) · r resolve · d dismiss · o reopen · n note · x select
      </p>

      {data && (
        <p className="muted">
          {plural(shown.length, "issue")} shown
          {shown.length !== data.issues.length ? ` of ${data.issues.length}` : ""}
        </p>
      )}
      {shown.length > 0 && (
        <table className="issue-table" onKeyDown={onKeyDown} aria-describedby="issue-keys">
          <caption className="visually-hidden">Issues</caption>
          <thead>
            <tr>
              <th scope="col">
                <span className="visually-hidden">Selected</span>
              </th>
              <th scope="col">Type</th>
              <th scope="col">Severity</th>
              <th scope="col">Page</th>
              <th scope="col">Where</th>
              <th scope="col">Expected / heard</th>
              <th scope="col">Status</th>
              <th scope="col">Note</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((i) => {
              const change = data?.last_check_run === i.last_run_id ? CHANGE_LABELS[i.change] : "";
              return (
                <tr
                  key={i.id}
                  ref={(el) => {
                    if (el) rows.current.set(i.id, el);
                    else rows.current.delete(i.id);
                  }}
                  tabIndex={i.id === (current?.id ?? shown[0]?.id) ? 0 : -1}
                  aria-current={i.id === current?.id ? "true" : undefined}
                  aria-selected={selected.has(i.id)}
                  className={`sev-${i.severity} st-${i.status}`}
                  onFocus={() => setCurrentId(i.id)}
                  onClick={() => setCurrentId(i.id)}
                >
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Select ${KIND_LABELS[i.kind]} at ${where(i)}`}
                      checked={selected.has(i.id)}
                      onChange={() => toggle(i.id)}
                    />
                  </td>
                  <td>{KIND_LABELS[i.kind]}</td>
                  <td>{SEVERITY_LABELS[i.severity]}</td>
                  <td>{i.page_label ?? "—"}</td>
                  <td>
                    {i.start !== null ? (
                      <button
                        type="button"
                        className="link"
                        onClick={() => play(i)}
                        aria-label={`Play ${where(i)}`}
                      >
                        {playing === i.id ? "■ " : "▶ "}
                        {where(i)}
                      </button>
                    ) : (
                      where(i)
                    )}
                  </td>
                  <td>
                    {i.kind !== "silence" && <div className="expected">{i.expected}</div>}
                    {i.kind === "flub" && <div className="heard">Heard: {i.heard || "—"}</div>}
                    {i.kind === "silence" && i.sentence_text && <div className="muted">After: {i.sentence_text}</div>}
                    <div className="muted">{summary(i)}</div>
                  </td>
                  <td>
                    {i.status === "open" ? "Open" : i.status === "resolved" ? "Resolved" : "Dismissed"}
                    {i.decided_by === "auto" && i.status === "resolved" ? " (auto)" : ""}
                    {change && <span className={`badge change-${i.change}`}> {change}</span>}
                  </td>
                  <td>
                    {editing?.id === i.id ? (
                      <input
                        autoFocus
                        aria-label="Note"
                        value={editing.draft}
                        onChange={(e) => setEditing({ id: i.id, draft: e.target.value })}
                        onKeyDown={(e) => {
                          if (e.key === "Enter") saveNote();
                          if (e.key === "Escape") {
                            setEditing(null);
                            focusRow(i.id);
                          }
                        }}
                        onBlur={saveNote}
                      />
                    ) : (
                      i.note
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
      <SilenceSettings onError={onError} />
    </section>
  );
}

function Choice<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: [T, string][];
  onChange: (v: T) => void;
}) {
  const id = `issue-${label.toLowerCase().replace(/\W+/g, "-")}`;
  return (
    <span>
      <label htmlFor={id}>{label}</label>
      <select id={id} value={value} onChange={(e) => onChange(e.target.value as T)}>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </span>
  );
}

const THRESHOLDS: [keyof IssueSettings, string][] = [
  ["silence_sentence_s", "Within a paragraph"],
  ["silence_paragraph_s", "At a paragraph break"],
  ["silence_chapter_s", "At a chapter break"],
];

/** Long-pause thresholds (SPEC §6.3 step 6); they apply on the next check. */
function SilenceSettings({ onError }: { onError: (msg: string) => void }) {
  const [s, setS] = useState<IssueSettings | null>(null);
  const [saved, setSaved] = useState(false);
  useEffect(() => {
    api
      .issueSettings()
      .then(setS)
      .catch(() => undefined);
  }, []);
  if (!s) return null;
  return (
    <details className="issue-settings">
      <summary>Long-pause thresholds</summary>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          api
            .saveIssueSettings(s)
            .then((r) => {
              setS(r);
              setSaved(true);
            })
            .catch((err: unknown) => onError(err instanceof Error ? err.message : String(err)));
        }}
      >
        {THRESHOLDS.map(([key, label]) => (
          <span key={key}>
            <label htmlFor={`th-${key}`}>{label} (seconds)</label>
            <input
              id={`th-${key}`}
              type="number"
              min={0.5}
              step={0.1}
              value={s[key]}
              onChange={(e) => {
                setSaved(false);
                setS({ ...s, [key]: Number(e.target.value) });
              }}
            />
          </span>
        ))}
        <button type="submit">Save thresholds</button>
        {saved && <span role="status"> Saved. Re-run checks to apply.</span>}
      </form>
    </details>
  );
}
