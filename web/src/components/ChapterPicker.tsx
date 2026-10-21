import type { Chapter } from "../state/engine";
import { CHAPTER_STATUS, chapterLabel } from "../state/scope";

interface Props {
  chapters: Chapter[];
  value: number | null; // chapter id; null = the whole book
  onChange: (id: number | null) => void;
}

/** Work on the whole book or one chapter at a time (D-059). Every step follows
 * the choice. Hidden until the manuscript has chapters. */
export function ChapterPicker({ chapters, value, onChange }: Props) {
  if (!chapters.length) return null;
  return (
    <div className="chapter-picker">
      <label htmlFor="chapter-pick">Working on</label>
      <select
        id="chapter-pick"
        value={value === null ? "book" : String(value)}
        onChange={(e) => onChange(e.target.value === "book" ? null : Number(e.target.value))}
      >
        <option value="book">The whole book</option>
        {chapters.map((c) => (
          <option key={c.id} value={c.id}>
            {chapterLabel(c)}
            {c.status === "not_recorded" ? ` (${CHAPTER_STATUS.not_recorded.toLowerCase()})` : ""}
            {c.open_issues ? ` · ${c.open_issues} to review` : ""}
          </option>
        ))}
      </select>
    </div>
  );
}
