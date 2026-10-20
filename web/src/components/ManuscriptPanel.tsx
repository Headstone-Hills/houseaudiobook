import { useEffect, useRef, useState } from "react";
import {
  api,
  uploadManuscript,
  type ManuscriptFormat,
  type ManuscriptInfo,
  type ManuscriptPreview,
  type ManuscriptSource,
  type PageSettings,
} from "../state/engine";
import { PageSettingsForm } from "./PageSettingsForm";
import { UnitBrowser } from "./UnitBrowser";
import { plural } from "../state/format";

const ACCEPT = ".txt,.text,.md,.markdown,.docx,.pdf";
const PREVIEW_DEBOUNCE_MS = 300;
const PREVIEW_PAGE_ROWS = 12;

function formatOf(name: string): ManuscriptFormat {
  const ext = name.toLowerCase().split(".").pop() ?? "";
  if (ext === "pdf" || ext === "docx") return ext;
  return ext === "md" || ext === "markdown" ? "md" : "txt";
}

interface Props {
  info: ManuscriptInfo | null;
  /** The engine's default page settings, for a fresh import. */
  defaults: PageSettings;
  /** Bumped when a manuscript job finishes, so the unit list reloads. */
  revision: number;
  onError: (msg: string) => void;
}

/** Import a manuscript (SPEC §6.2): pick it, see how pages will be numbered
 * before committing, adjust, import. Later, re-map pages the same way. */
export function ManuscriptPanel({ info, defaults, revision, onError }: Props) {
  const picker = useRef<HTMLInputElement>(null);
  const [path, setPath] = useState("");
  const [source, setSource] = useState<{ src: ManuscriptSource; name: string } | null>(null);
  const [settings, setSettings] = useState<PageSettings>(defaults);
  const [preview, setPreview] = useState<ManuscriptPreview | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const editing = source !== null;
  const remapping = source?.src && "current" in source.src;

  // Re-preview whenever the source or settings change (debounced while typing).
  useEffect(() => {
    if (!source) return;
    let cancelled = false;
    const t = setTimeout(() => {
      api
        .previewManuscript(source.src, settings)
        .then((p) => {
          if (cancelled) return;
          setPreview(p);
          setPreviewError(null);
        })
        .catch((e: unknown) => {
          if (cancelled) return;
          setPreview(null);
          setPreviewError(e instanceof Error ? e.message : String(e));
        });
    }, PREVIEW_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [source, settings]);

  const choose = (src: ManuscriptSource, name: string, initial: PageSettings = defaults) => {
    setSettings(initial);
    setPreview(null);
    setPreviewError(null);
    setSource({ src, name });
  };

  const onFile = async (file: File) => {
    try {
      choose({ upload_id: await uploadManuscript(file) }, file.name);
    } catch (e) {
      onError(`${file.name}: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const commit = async () => {
    if (!source) return;
    setBusy(true);
    try {
      if (remapping) await api.remapManuscript(settings);
      else await api.importManuscript(source.src, settings);
      setSource(null);
      setPreview(null);
    } catch (e) {
      onError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section aria-labelledby="manuscript-heading" className="manuscript">
      <h2 id="manuscript-heading">Manuscript</h2>

      {info && !editing && (
        <div className="manuscript-summary">
          <p>
            <strong>{info.original_name}</strong> · {info.format.toUpperCase()} ·{" "}
            {info.estimated ? `about ${plural(info.page_count, "page")} (estimated)` : plural(info.page_count, "page")}{" "}
            · {plural(info.counts.chapter, "chapter")} · {plural(info.counts.sentence, "sentence")}
          </p>
          {info.status !== "ok" && info.status !== "checking" && (
            <p role="alert" className="error">
              The project's copy of the manuscript is {info.status === "missing" ? "missing" : "changed on disk"}.
              Import it again.
            </p>
          )}
          {info.warnings.map((w) => (
            <p key={w} className="warning">
              {w}
            </p>
          ))}
          <button type="button" onClick={() => choose({ current: true }, info.original_name, info.settings)}>
            Change page settings…
          </button>
        </div>
      )}

      {!editing && (
        <div className="manuscript-pick">
          <p>{info ? "Replace the manuscript:" : "Import the book's text (.txt, .md, .docx or text-layer .pdf)."}</p>
          <button type="button" onClick={() => picker.current?.click()}>
            Choose manuscript…
          </button>
          <input
            ref={picker}
            type="file"
            accept={ACCEPT}
            hidden
            data-testid="manuscript-picker"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void onFile(f);
              e.target.value = "";
            }}
          />
          <form
            onSubmit={(e) => {
              e.preventDefault();
              const p = path.trim();
              if (p) choose({ path: p }, p.split(/[\\/]/).pop() ?? p);
            }}
          >
            <label htmlFor="manuscript-path">Or by path</label>
            <input id="manuscript-path" value={path} onChange={(e) => setPath(e.target.value)} />
            <button type="submit" disabled={!path.trim()}>
              Preview
            </button>
          </form>
        </div>
      )}

      {editing && (
        <div className="manuscript-edit">
          <h3>{remapping ? `Page settings for ${source.name}` : `Preview: ${source.name}`}</h3>
          <PageSettingsForm format={preview?.format ?? formatOf(source.name)} value={settings} onChange={setSettings} />
          {previewError && (
            <p role="alert" className="error">
              {previewError}
            </p>
          )}
          {preview && <PreviewView preview={preview} />}
          <div className="actions">
            <button type="button" onClick={() => void commit()} disabled={!preview || busy}>
              {remapping ? "Apply page settings" : "Import manuscript"}
            </button>
            <button type="button" onClick={() => setSource(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {info && !editing && <UnitBrowser revision={revision} />}
    </section>
  );
}

function PreviewView({ preview }: { preview: ManuscriptPreview }) {
  const [allPages, setAllPages] = useState(false);
  const modeText: Record<ManuscriptPreview["page_mode"], string> = {
    markers: "page markers in the text",
    breaks: "page breaks",
    pdf: "the PDF's pages",
    estimate: "an estimate from word count",
  };
  const pages = allPages ? preview.pages : preview.pages.slice(0, PREVIEW_PAGE_ROWS);
  return (
    <div className="preview" aria-live="polite">
      <p>
        Pages from <strong>{modeText[preview.page_mode]}</strong>:{" "}
        {preview.estimated ? "about " : ""}
        {plural(preview.page_count, "page")}, {plural(preview.counts.chapter, "chapter")},{" "}
        {plural(preview.counts.sentence, "sentence")}.
      </p>
      {preview.warnings.map((w) => (
        <p key={w} className="warning">
          {w}
        </p>
      ))}
      <table className="preview-pages">
        <caption>Where each page starts</caption>
        <thead>
          <tr>
            <th scope="col">Page</th>
            <th scope="col">Physical</th>
            <th scope="col">Begins with</th>
          </tr>
        </thead>
        <tbody>
          {pages.map((p, i) => (
            <tr key={`${i}-${p.label}`} className={p.front_matter ? "muted" : undefined}>
              <td>{p.label}</td>
              <td>{p.physical ?? "—"}</td>
              <td>{p.first_text}…</td>
            </tr>
          ))}
        </tbody>
      </table>
      {preview.pages.length > PREVIEW_PAGE_ROWS && (
        <button type="button" onClick={() => setAllPages((v) => !v)}>
          {allPages ? "Show fewer pages" : `Show all ${preview.pages.length} pages`}
        </button>
      )}
      <h4>First sentences</h4>
      <ol className="preview-sentences">
        {preview.sentences.map((s) => (
          <li key={s.ordinal}>
            <span className="page-tag">p. {s.page_label}</span> {s.text}
          </li>
        ))}
      </ol>
    </div>
  );
}
