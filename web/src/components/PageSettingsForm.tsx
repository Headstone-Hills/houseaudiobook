import type { ManuscriptFormat, PageMode, PageSettings } from "../state/engine";

const MODES: { value: PageMode; label: string; formats: ManuscriptFormat[] }[] = [
  { value: "auto", label: "Automatic", formats: ["txt", "md", "docx", "pdf"] },
  { value: "markers", label: "Page markers in the text", formats: ["txt", "md"] },
  { value: "breaks", label: "Page breaks", formats: ["txt", "md", "docx", "pdf"] },
  { value: "pdf", label: "PDF pages", formats: ["pdf"] },
  { value: "estimate", label: "Estimate from word count (approx.)", formats: ["txt", "md", "docx", "pdf"] },
];

interface Props {
  format: ManuscriptFormat;
  value: PageSettings;
  onChange: (s: PageSettings) => void;
}

/** Where page numbers come from (SPEC §6.2). Only the options that apply to the
 * file's format and the chosen mode are shown. */
export function PageSettingsForm({ format, value, onChange }: Props) {
  const set = <K extends keyof PageSettings>(key: K, v: PageSettings[K]) => onChange({ ...value, [key]: v });
  const text = format === "txt" || format === "md";
  const counted = value.mode === "breaks" || value.mode === "pdf" || value.mode === "auto";

  return (
    <fieldset className="page-settings">
      <legend>Page numbers</legend>
      <label htmlFor="page-mode">Take page numbers from</label>
      <select id="page-mode" value={value.mode} onChange={(e) => set("mode", e.target.value as PageMode)}>
        {MODES.filter((m) => m.formats.includes(format)).map((m) => (
          <option key={m.value} value={m.value}>
            {m.label}
          </option>
        ))}
      </select>

      {text && (value.mode === "auto" || value.mode === "markers") && (
        <>
          <label htmlFor="marker-pattern">Page marker pattern (regular expression; one group = the number)</label>
          <input
            id="marker-pattern"
            value={value.marker_pattern}
            onChange={(e) => set("marker_pattern", e.target.value)}
            spellCheck={false}
          />
        </>
      )}

      {value.mode === "estimate" && (
        <>
          <label htmlFor="wpp">Words per page</label>
          <input
            id="wpp"
            type="number"
            min={50}
            max={2000}
            value={value.words_per_page}
            onChange={(e) => set("words_per_page", Number(e.target.value))}
          />
        </>
      )}

      {format === "pdf" && (value.mode === "auto" || value.mode === "pdf") && (
        <label className="check">
          <input
            type="checkbox"
            checked={value.use_pdf_labels}
            onChange={(e) => set("use_pdf_labels", e.target.checked)}
          />
          Use the PDF's own page numbers when it has them
        </label>
      )}

      {counted && value.mode !== "markers" && (
        <>
          <label htmlFor="first-page">Printed page 1 is physical page</label>
          <input
            id="first-page"
            type="number"
            min={1}
            value={value.first_numbered_page}
            onChange={(e) => set("first_numbered_page", Math.max(1, Number(e.target.value)))}
            aria-describedby="first-page-help"
          />
          <span id="first-page-help" className="muted">
            {" "}
            Pages before it are front matter.
            {format === "pdf" && value.use_pdf_labels && " Used only if the PDF has no page numbers of its own."}
          </span>
        </>
      )}

      <label className="check">
        <input
          type="checkbox"
          checked={value.skip_front_matter}
          onChange={(e) => set("skip_front_matter", e.target.checked)}
        />
        Skip front matter
      </label>
    </fieldset>
  );
}
