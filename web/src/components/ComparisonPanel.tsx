import { useEffect, useState } from "react";
import { api, type AsrInfo, type Comparison } from "../state/engine";
import { bestReading, clock, complete, describe, pages, status, type Status } from "../state/comparison";
import { plural } from "../state/format";
import { hasAudio, hasUnit, type Scope } from "../state/scope";

type Filter = "all" | Status;
const FILTERS: { value: Filter; label: string }[] = [
  { value: "differences", label: "Read differently" },
  { value: "not-read", label: "Skipped" },
  { value: "clean", label: "Read correctly" },
  { value: "not-recorded", label: "Not recorded yet" },
  { value: "all", label: "Every line" },
];

interface Props {
  canRun: boolean;
  why: string | null; // why running isn't possible yet
  revision: number; // bumped when an analysis job finishes
  modelRevision?: number; // bumped when a model download finishes
  scope?: Scope; // a chapter: only its lines (D-059)
  onError: (msg: string) => void;
}

/** Transcribe + Compare to Text (SPEC §6.3 steps 1 and 3): run it (with the
 * project's checks), then read the per-sentence results. What needs fixing is
 * in the Issues panel. */
export function ComparisonPanel({ canRun, why, revision, modelRevision = 0, scope = null, onError }: Props) {
  const [asr, setAsr] = useState<AsrInfo | null>(null);
  const [model, setModel] = useState("");
  const [data, setData] = useState<Comparison | null>(null);
  const [filter, setFilter] = useState<Filter>("differences");

  useEffect(() => {
    api
      .asrInfo()
      .then((info) => {
        setAsr(info);
        setModel((m) => m || info.default.model);
      })
      .catch(() => undefined);
  }, [modelRevision]);

  useEffect(() => {
    let cancelled = false;
    api
      .comparison()
      .then((c) => !cancelled && setData(c))
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [revision]);

  const run = () => {
    api.runAnalysis(null, model).catch((e: unknown) => onError(e instanceof Error ? e.message : String(e)));
  };

  const lines = data?.sentences.filter((s) => hasUnit(scope, s.unit_id)) ?? [];
  const unaligned = data?.unaligned.filter((u) => hasAudio(scope, u.source_file_id, u.start, u.end)) ?? [];
  const counts: Record<Status, number> = { clean: 0, differences: 0, "not-read": 0, "not-recorded": 0 };
  lines.forEach((s) => counts[status(s)]++);
  const shown = lines.filter((s) => filter === "all" || status(s) === filter);
  const chosen = asr?.models.find((m) => m.name === model);
  const size = chosen?.size_mb;
  const notDownloaded = chosen?.downloaded === false;

  return (
    <section aria-labelledby="compare-heading" className="comparison">
      <h2 id="compare-heading">Compare to Text</h2>
      <div className="run-row action-bar">
        <button
          type="button"
          className="primary"
          onClick={run}
          disabled={!canRun || !asr?.installed || notDownloaded}
        >
          {data ? "Check again" : "Transcribe and compare"}
        </button>
        <label htmlFor="asr-model">Speech model</label>
        <select id="asr-model" value={model} onChange={(e) => setModel(e.target.value)}>
          {asr?.models.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
              {m.name === asr.default.model ? " (recommended)" : ""}
            </option>
          ))}
        </select>
      </div>
      <p className="muted">
        {asr && !asr.installed
          ? "Speech recognition isn't installed in this engine."
          : (why ??
            (notDownloaded
              ? `Download the ${model} model first (about ${size} MB, under Speech models at the top).`
              : data
                ? "Runs on this computer; audio never leaves the machine. Transcripts are kept, so checking again is quick."
                : "Runs on this computer; audio never leaves the machine. Long books take a while."))}
      </p>

      {data?.stale && (
        <p role="status" className="warning">
          The manuscript&apos;s pages changed after this check. Check again (transcripts are reused, so it&apos;s
          quick).
        </p>
      )}
      {data && !data.stale && (
        <>
          <ul className="stat-row" aria-label="Summary" aria-live="polite">
            <li>
              <strong>{counts.clean}</strong> read correctly
            </li>
            <li className={counts.differences ? "stat-warn" : ""}>
              <strong>{counts.differences}</strong> read differently
            </li>
            <li className={counts["not-read"] ? "stat-warn" : ""}>
              <strong>{counts["not-read"]}</strong> skipped
            </li>
            {counts["not-recorded"] > 0 && (
              <li>
                <strong>{counts["not-recorded"]}</strong> not recorded yet
              </li>
            )}
            <li>
              <strong>{unaligned.length}</strong> off-script
            </li>
          </ul>
          {counts["not-recorded"] > 0 && (
            <p className="note">
              The recordings cover only part of the {scope ? "chapter" : "manuscript"}.{" "}
              {plural(counts["not-recorded"], "line")} no recording reaches yet {counts["not-recorded"] === 1 ? "isn't" : "aren't"}{" "}
              counted as skipped. Add their recordings when they&apos;re ready.
            </p>
          )}
          <fieldset className="filter segmented">
            <legend className="visually-hidden">Show</legend>
            {FILTERS.filter((f) => f.value !== "not-recorded" || counts["not-recorded"] > 0).map((f) => (
              <label key={f.value} className={filter === f.value ? "on" : ""}>
                <input
                  type="radio"
                  name="cmp-filter"
                  checked={filter === f.value}
                  onChange={() => setFilter(f.value)}
                />
                {f.label}
                {f.value !== "all" && <span className="count">{counts[f.value as Status]}</span>}
              </label>
            ))}
          </fieldset>
          {shown.length === 0 && <p className="empty">No lines here.</p>}
          <ol className="cmp-list" aria-label="Sentences">
            {shown.map((s) => {
              const best = bestReading(s);
              const st = status(s);
              return (
                <li key={s.unit_id} className={`cmp-${st}`}>
                  <p className="cmp-line">
                    <span className="page-tag">p. {pages(s)}</span> {s.text}{" "}
                    <span className="muted">
                      {st === "not-read"
                        ? "· skipped"
                        : st === "not-recorded"
                          ? "· not recorded yet"
                        : `· ${plural(s.readings.filter(complete).length, "take")}${best ? ` · best ${Math.round(best.score * 100)}%` : ""}`}
                      {/* A stutter lowers the score without being a text error; say so. */}
                      {st === "clean" && best?.diffs.some((d) => d.kind === "extra") && " · extra words"}
                    </span>
                  </p>
                  {best && st === "differences" && (
                    <ul className="diffs">
                      {best.diffs
                        .filter((d) => d.kind !== "mispronunciation")
                        .map((d, i) => (
                          <li key={i}>{describe(d)}</li>
                        ))}
                    </ul>
                  )}
                  {s.readings.length > 0 && (
                    <details>
                      <summary>Every reading ({s.readings.length})</summary>
                      <table className="takes">
                        <caption>Every reading of this sentence</caption>
                        <thead>
                          <tr>
                            <th scope="col">Recording</th>
                            <th scope="col">Time</th>
                            <th scope="col">Score</th>
                            <th scope="col">Heard</th>
                          </tr>
                        </thead>
                        <tbody>
                          {s.readings.map((r, i) => (
                            <tr key={i}>
                              <td>{r.source_label}</td>
                              <td>
                                {clock(r.start)}–{clock(r.end)}
                              </td>
                              <td>{complete(r) ? `${Math.round(r.score * 100)}%` : "false start"}</td>
                              <td>
                                {r.heard}
                                {r.diffs.length > 0 && (
                                  <ul className="diffs">
                                    {r.diffs.map((d, j) => (
                                      <li key={j}>{describe(d)}</li>
                                    ))}
                                  </ul>
                                )}
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </details>
                  )}
                </li>
              );
            })}
          </ol>
          {unaligned.length > 0 && (
            <>
              <h3>Off-script speech</h3>
              <p className="muted">Speech that matches no line of the manuscript: asides, comments, retakes.</p>
              <ul className="unaligned">
                {unaligned.map((u, i) => (
                  <li key={i}>
                    {u.source_label} {clock(u.start)}–{clock(u.end)}: “{u.text}”
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </section>
  );
}
