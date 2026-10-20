import { useState, type KeyboardEvent } from "react";
import { api, type ProjectSummary, type Source } from "../state/engine";
import { describeFormat, formatBytes, formatDuration } from "../state/format";

const STATUS_TEXT: Record<Source["status"], string> = {
  ok: "OK",
  checking: "Checking…",
  missing: "Missing",
  modified: "Changed on disk",
};

interface Props {
  sources: Source[];
  selectedId: number | null;
  onSelect: (id: number) => void;
  onRelinked: (p: ProjectSummary) => void;
  onError: (msg: string) => void;
}

/** Source list. Arrow keys move the selection; Enter on a row selects it. */
export function SourceTable({ sources, selectedId, onSelect, onRelinked, onError }: Props) {
  if (sources.length === 0) return <p>No audio imported yet.</p>;

  const onKeyDown = (e: KeyboardEvent<HTMLTableSectionElement>) => {
    if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
    if (e.target instanceof HTMLInputElement) return; // typing in the relink box
    e.preventDefault();
    const i = sources.findIndex((s) => s.id === selectedId);
    const next = sources[Math.max(0, Math.min(sources.length - 1, i + (e.key === "ArrowDown" ? 1 : -1)))];
    if (next) {
      onSelect(next.id);
      document.getElementById(`source-row-${next.id}`)?.focus();
    }
  };

  return (
    <table className="sources">
      <thead>
        <tr>
          <th scope="col">#</th>
          <th scope="col">Label</th>
          <th scope="col">Length</th>
          <th scope="col">Format</th>
          <th scope="col">Size</th>
          <th scope="col">Stored</th>
          <th scope="col">Status</th>
        </tr>
      </thead>
      <tbody onKeyDown={onKeyDown}>
        {sources.map((s, i) => (
          <tr
            key={s.id}
            id={`source-row-${s.id}`}
            tabIndex={s.id === selectedId || (selectedId === null && i === 0) ? 0 : -1}
            aria-selected={s.id === selectedId}
            onClick={() => onSelect(s.id)}
            onKeyDown={(e) => e.key === "Enter" && onSelect(s.id)}
            className={s.id === selectedId ? "selected" : undefined}
          >
            <td>{i + 1}</td>
            <th scope="row" title={s.original_name}>
              {s.label}
            </th>
            <td>{formatDuration(s.duration)}</td>
            <td>{describeFormat(s)}</td>
            <td>{formatBytes(s.size_bytes)}</td>
            <td title={s.mode === "reference" ? s.path : undefined}>
              {s.mode === "copy" ? "Copied" : "Referenced"}
            </td>
            <td className={`status status-${s.status}`}>
              {STATUS_TEXT[s.status]}
              {(s.status === "missing" || s.status === "modified") && (
                <RelinkForm source={s} onRelinked={onRelinked} onError={onError} />
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function RelinkForm({
  source,
  onRelinked,
  onError,
}: {
  source: Source;
  onRelinked: (p: ProjectSummary) => void;
  onError: (msg: string) => void;
}) {
  const [path, setPath] = useState("");
  return (
    <form
      className="relink"
      onSubmit={(e) => {
        e.preventDefault();
        api
          .relink(source.id, path.trim())
          .then(onRelinked)
          .catch((err: unknown) => onError(err instanceof Error ? err.message : String(err)));
      }}
    >
      <label>
        Relink to the same file at
        <input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/new/location.wav" />
      </label>
      <button type="submit" disabled={!path.trim()}>
        Relink
      </button>
    </form>
  );
}
