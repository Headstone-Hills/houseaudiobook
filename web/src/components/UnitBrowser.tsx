import { useEffect, useState } from "react";
import { api, type ManuscriptUnit } from "../state/engine";

const PAGE_SIZE = 100;

function pages(u: ManuscriptUnit): string {
  return u.page_label === u.page_end_label ? u.page_label : `${u.page_label}–${u.page_end_label}`;
}

/** Browse the imported manuscript's units with their pages. */
export function UnitBrowser({ revision }: { revision: number }) {
  const [kind, setKind] = useState<ManuscriptUnit["kind"]>("sentence");
  const [offset, setOffset] = useState(0);
  const [data, setData] = useState<{ total: number; units: ManuscriptUnit[] } | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    api
      .manuscriptUnits(kind, offset, PAGE_SIZE)
      .then((d) => !cancelled && setData(d))
      .catch((e: unknown) => !cancelled && setError(e instanceof Error ? e.message : String(e)));
    return () => {
      cancelled = true;
    };
  }, [kind, offset, revision]);

  return (
    <div className="units">
      <label htmlFor="unit-kind">Show</label>
      <select
        id="unit-kind"
        value={kind}
        onChange={(e) => {
          setKind(e.target.value as ManuscriptUnit["kind"]);
          setOffset(0);
        }}
      >
        <option value="sentence">Sentences</option>
        <option value="paragraph">Paragraphs</option>
        <option value="chapter">Chapters</option>
      </select>
      {error && <p className="error">{error}</p>}
      {data && (
        <>
          <table className="unit-table">
            <thead>
              <tr>
                <th scope="col">#</th>
                <th scope="col">Page</th>
                <th scope="col">Text</th>
              </tr>
            </thead>
            <tbody>
              {data.units.map((u) => (
                <tr key={`${u.kind}-${u.ordinal}`}>
                  <td>{u.ordinal + 1}</td>
                  <td className="page-cell">{pages(u)}</td>
                  <td>{u.text}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <nav aria-label="Unit pages" className="pager">
            <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
              Previous
            </button>
            <span>
              {data.total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} of {data.total}
            </span>
            <button
              type="button"
              disabled={offset + PAGE_SIZE >= data.total}
              onClick={() => setOffset(offset + PAGE_SIZE)}
            >
              Next
            </button>
          </nav>
        </>
      )}
    </div>
  );
}
