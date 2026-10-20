import { useRef, useState, type DragEvent, type FormEvent } from "react";
import { api, uploadFile } from "../state/engine";

const ACCEPT = ".wav,.wave,.mp3,audio/wav,audio/mpeg";

/** Three ways in (SPEC §6.1 step 3): file picker and drag-and-drop upload the
 * bytes (browsers don't expose paths); the path box imports in place or by copy
 * without an upload, which matters for multi-gigabyte files. */
export function ImportPanel({ onError }: { onError: (msg: string) => void }) {
  const picker = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploads, setUploads] = useState<Record<string, number>>({});
  const [paths, setPaths] = useState("");
  const [mode, setMode] = useState<"copy" | "reference">("copy");

  const upload = async (files: FileList | File[]) => {
    for (const file of Array.from(files)) {
      try {
        await uploadFile(file, (f) => setUploads((u) => ({ ...u, [file.name]: f })));
      } catch (e) {
        onError(`${file.name}: ${e instanceof Error ? e.message : String(e)}`);
      } finally {
        setUploads((u) => {
          const rest = { ...u };
          delete rest[file.name];
          return rest;
        });
      }
    }
  };

  const onDrop = (e: DragEvent) => {
    e.preventDefault();
    setDragging(false);
    void upload(e.dataTransfer.files);
  };

  const submitPaths = async (e: FormEvent) => {
    e.preventDefault();
    const list = paths.split("\n").map((p) => p.trim()).filter(Boolean);
    if (!list.length) return;
    try {
      await api.importPaths(list, mode);
      setPaths("");
    } catch (err) {
      onError(err instanceof Error ? err.message : String(err));
    }
  };

  return (
    <section aria-label="Import audio" className="import">
      <h2>Import audio</h2>
      <div
        className={`dropzone${dragging ? " dragging" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
      >
        <p>Drop .wav or .mp3 files here, or</p>
        <button type="button" onClick={() => picker.current?.click()}>
          Choose files…
        </button>
        <input
          ref={picker}
          type="file"
          multiple
          accept={ACCEPT}
          hidden
          data-testid="file-picker"
          onChange={(e) => {
            if (e.target.files) void upload(e.target.files);
            e.target.value = "";
          }}
        />
        {Object.entries(uploads).map(([name, f]) => (
          <p key={name}>
            Uploading {name} <progress value={f} max={1} aria-label={`Uploading ${name}`} />
          </p>
        ))}
      </div>

      <form onSubmit={(e) => void submitPaths(e)} className="path-import">
        <label htmlFor="import-paths">Or import by path (one per line)</label>
        <textarea
          id="import-paths"
          rows={2}
          value={paths}
          onChange={(e) => setPaths(e.target.value)}
          placeholder="/Users/me/Recordings/Chapter 01.wav"
        />
        <fieldset>
          <legend>Store</legend>
          <label>
            <input type="radio" name="mode" checked={mode === "copy"} onChange={() => setMode("copy")} />
            Copy into project
          </label>
          <label>
            <input
              type="radio"
              name="mode"
              checked={mode === "reference"}
              onChange={() => setMode("reference")}
            />
            Reference in place
          </label>
        </fieldset>
        <button type="submit">Import</button>
      </form>
    </section>
  );
}
