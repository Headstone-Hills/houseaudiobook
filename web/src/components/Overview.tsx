import type { Chapter, Source } from "../state/engine";
import { CHAPTER_STATUS, chapterLabel, chapterProgress, type Scope } from "../state/scope";
import { STEPS, nextUp, stepDef, type StepKey, type StepStatus } from "../state/steps";
import { clock } from "../state/comparison";

interface Props {
  scope: Scope;
  chapters: Chapter[];
  sources: Source[];
  statuses: Record<StepKey, StepStatus>;
  onStep: (key: StepKey) => void;
  onChapter: (id: number | null) => void;
}

/** The book (or chapter) at a glance: the one thing to do next, every step's
 * state, and the chapters to pick from. */
export function Overview({ scope, chapters, sources, statuses, onStep, onChapter }: Props) {
  const next = nextUp(statuses);
  return (
    <div className="overview">
      <section className="card next-up" aria-labelledby="next-heading">
        <h2 id="next-heading">{next ? "Next" : "All caught up"}</h2>
        {next ? (
          <>
            <p className="next-what">
              <strong>{stepDef(next).title}</strong>: {statuses[next].note}
            </p>
            <p className="muted">{stepDef(next).blurb}</p>
            <button type="button" className="primary" onClick={() => onStep(next)}>
              Go to {stepDef(next).title}
            </button>
          </>
        ) : (
          <>
            <p>Nothing is waiting on you{scope ? " in this chapter" : ""}. Fine-tune it on the timeline, or export it.</p>
            <button type="button" className="primary" onClick={() => onStep("export")}>
              Go to Export
            </button>
          </>
        )}
      </section>

      {scope && <ChapterCard chapter={scope} sources={sources} onWhole={() => onChapter(null)} />}

      <section className="card" aria-labelledby="progress-heading">
        <h2 id="progress-heading">Progress</h2>
        <ol className="progress-list">
          {STEPS.filter((s) => s.key !== "overview").map((s) => {
            const st = statuses[s.key];
            return (
              <li key={s.key} className={`state-${st.state}`}>
                <button type="button" className="link" onClick={() => onStep(s.key)}>
                  {s.title}
                </button>
                <span className="muted"> · {st.note}</span>
              </li>
            );
          })}
        </ol>
      </section>

      {!scope && chapters.length > 0 && (
        <section className="card" aria-labelledby="chapters-heading">
          <h2 id="chapters-heading">Chapters</h2>
          <p className="muted">Work on one chapter at a time: every step then shows only that chapter.</p>
          <table className="chapter-table">
            <thead>
              <tr>
                <th scope="col">Chapter</th>
                <th scope="col">Page</th>
                <th scope="col">Status</th>
                <th scope="col">
                  <span className="visually-hidden">Open</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {chapters.map((c) => (
                <tr key={c.id} className={`ch-${c.status}`}>
                  <th scope="row">{chapterLabel(c)}</th>
                  <td>{c.page_label || "—"}</td>
                  <td>
                    <span className={`chip chip-${c.status}`}>{CHAPTER_STATUS[c.status]}</span>
                    {c.status !== "not_compared" && c.status !== "not_recorded" && (
                      <span className="muted"> {chapterProgress(c).replace(/^Recorded · /, "")}</span>
                    )}
                  </td>
                  <td>
                    <button type="button" onClick={() => onChapter(c.id)} aria-label={`Work on ${chapterLabel(c)}`}>
                      Work on this
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </div>
  );
}

function ChapterCard({ chapter, sources, onWhole }: { chapter: Chapter; sources: Source[]; onWhole: () => void }) {
  const label = (id: number) => sources.find((s) => s.id === id)?.label ?? `recording ${id}`;
  return (
    <section className="card" aria-labelledby="chapter-heading">
      <h2 id="chapter-heading">{chapterLabel(chapter)}</h2>
      <p>
        <span className={`chip chip-${chapter.status}`}>{CHAPTER_STATUS[chapter.status]}</span>{" "}
        {chapter.page_label && <span className="muted">from page {chapter.page_label} · </span>}
        <span className="muted">
          {chapter.read} of {chapter.sentences} lines recorded
        </span>
      </p>
      {chapter.status === "partial" && (
        <p className="muted">
          {chapter.not_recorded} line{chapter.not_recorded === 1 ? " isn't" : "s aren't"} in any recording yet. They
          aren&apos;t flagged as skipped: add the rest of the chapter when it&apos;s recorded.
        </p>
      )}
      {chapter.status === "not_recorded" && (
        <p className="muted">No recording has this chapter in it yet. Add it under Recordings, then check the reading.</p>
      )}
      {chapter.spans.length > 0 && (
        <ul className="spans">
          {chapter.spans.map((s) => (
            <li key={s.source_id}>
              {label(s.source_id)}: {clock(s.start)}–{clock(s.end)}
            </li>
          ))}
        </ul>
      )}
      <button type="button" onClick={onWhole}>
        Back to the whole book
      </button>
    </section>
  );
}
