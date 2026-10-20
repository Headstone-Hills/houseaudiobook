import { useEffect, useState } from "react";
import { api, type AsrInfo, type Comparison } from "../state/engine";
import { bestReading, clock, complete, describe, pages, status, type Status } from "../state/comparison";
import { plural } from "../state/format";

type Filter = "all" | Status;
const FILTERS: { value: Filter; label: string }[] = [
  { value: "all", label: "All sentences" },
  { value: "differences", label: "With differences" },
  { value: "not-read", label: "Not read" },
  { value: "clean", label: "Clean" },
];

interface Props {
  canRun: boolean;
  why: string | null; // why running isn't possible yet
  revision: number; // bumped when an analysis job finishes
  onError: (msg: string) => void;
}

/** Transcribe + Compare to Text (SPEC §6.3 steps 1 and 3): run it, then read the
 * per-sentence results. The full Issues panel (filters, resolve/dismiss, jump to
 * audio) is M4; this view shows what the comparison found. */
export function ComparisonPanel({ canRun, why, revision, onError }: Props) {
  const [asr, setAsr] = useState<AsrInfo | null>(null);
  const [model, setModel] = useState("");
  const [data, setData] = useState<Comparison | null>(null);
  const [filter, setFilter] = useState<Filter>("differences");

  useEffect(() => {
    api
      .asrInfo()
      .then((info) => {
        setAsr(info);
        setModel(info.default.model);
      })
      .catch(() => undefined);
  }, []);

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

  const counts = { clean: 0, differences: 0, "not-read": 0 };
  data?.sentences.forEach((s) => counts[status(s)]++);
  const shown = data?.sentences.filter((s) => filter === "all" || status(s) === filter) ?? [];
  const size = asr?.models.find((m) => m.name === model)?.size_mb;

  return (
    <section aria-labelledby="compare-heading" className="comparison">
      <h2 id="compare-heading">Compare to Text</h2>
      <div className="run-row">
        <label htmlFor="asr-model">Speech model</label>
        <select id="asr-model" value={model} onChange={(e) => setModel(e.target.value)}>
          {asr?.models.map((m) => (
            <option key={m.name} value={m.name}>
              {m.name}
              {m.name === asr.default.model ? " (recommended)" : ""}
            </option>
          ))}
        </select>
        <button type="button" onClick={run} disabled={!canRun || !asr?.installed}>
          Transcribe and compare
        </button>
      </div>
      <p className="muted">
        {asr && !asr.installed
          ? "Speech recognition isn't installed in this engine."
          : (why ??
            `Runs on this computer. The first use downloads the ${model} model${size ? ` (about ${size} MB)` : ""}; audio never leaves the machine.`)}
      </p>

      {data?.stale && (
        <p role="status" className="warning">
          The manuscript&apos;s pages changed after this comparison. Run Transcribe and compare again (transcripts are
          reused, so it&apos;s quick).
        </p>
      )}
      {data && !data.stale && (
        <>
          <p aria-live="polite">
            {counts.clean} clean · {counts.differences} with differences · {counts["not-read"]} not read ·{" "}
            {plural(data.unaligned.length, "off-script passage")}
          </p>
          <fieldset className="filter">
            <legend>Show</legend>
            {FILTERS.map((f) => (
              <label key={f.value}>
                <input
                  type="radio"
                  name="cmp-filter"
                  checked={filter === f.value}
                  onChange={() => setFilter(f.value)}
                />
                {f.label}
              </label>
            ))}
          </fieldset>
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
                        ? "· not read"
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
                      <summary>Show all takes</summary>
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
          {data.unaligned.length > 0 && (
            <>
              <h3>Off-script speech</h3>
              <ul className="unaligned">
                {data.unaligned.map((u, i) => (
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
