import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import type { ManuscriptInfo, ManuscriptPreview, PageSettings } from "../state/engine";
import { SETTINGS, mockApi } from "../test-utils";
import { ManuscriptPanel } from "./ManuscriptPanel";
import { PageSettingsForm } from "./PageSettingsForm";

const defaults = SETTINGS.page_defaults;

function preview(over: Partial<ManuscriptPreview> = {}): ManuscriptPreview {
  return {
    name: "book.pdf",
    format: "pdf",
    page_source: "pdf",
    page_mode: "pdf",
    page_count: 3,
    estimated: false,
    counts: { chapter: 2, paragraph: 6, sentence: 13 },
    warnings: [],
    settings: defaults,
    pages: [
      { label: "i", physical: 1, front_matter: true, first_text: "Two Fables from Aesop" },
      { label: "1", physical: 2, front_matter: false, first_text: "Chapter One. The Fox" },
    ],
    sentences: [
      {
        kind: "sentence",
        ordinal: 0,
        text: "Two Fables from Aesop",
        char_start: 0,
        char_end: 21,
        page: null,
        page_label: "i",
        page_end: null,
        page_end_label: "i",
        page_estimated: false,
        front_matter: true,
      },
    ],
    ...over,
  };
}

const info: ManuscriptInfo = {
  original_name: "book.pdf",
  original_path: "/m/book.pdf",
  sha256: "abc",
  size_bytes: 3980,
  format: "pdf",
  settings: { ...defaults, skip_front_matter: true },
  page_mode: "pdf",
  page_count: 3,
  estimated: false,
  warnings: [],
  counts: { chapter: 2, paragraph: 6, sentence: 13 },
  imported_at: "",
  mapped_at: "",
  status: "ok",
};

test("preview by path, adjust settings, import", async () => {
  const previews: { source: unknown; settings: PageSettings }[] = [];
  const imports: unknown[] = [];
  mockApi({
    "POST /api/project/manuscript/preview": (body: unknown) => {
      const b = body as { source: unknown; settings: PageSettings };
      previews.push(b);
      return b.settings.mode === "estimate"
        ? preview({ page_mode: "estimate", estimated: true, page_count: 1 })
        : preview();
    },
    "POST /api/project/manuscript": (body: unknown) => {
      imports.push(body);
      return { job_id: "j1" };
    },
  });
  render(<ManuscriptPanel info={null} defaults={defaults} revision={0} onError={() => {}} />);
  fireEvent.change(screen.getByLabelText("Or by path"), { target: { value: "/m/book.pdf" } });
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));

  expect(await screen.findByText("the PDF's pages")).toBeInTheDocument();
  const pages = screen.getByRole("table", { name: "Where each page starts" });
  expect(within(pages).getByText("Two Fables from Aesop…")).toBeInTheDocument();
  expect(screen.getByText("p. i")).toBeInTheDocument();
  expect(previews[0]).toEqual({ source: { path: "/m/book.pdf" }, settings: defaults });

  // Switching to estimate re-previews and shows the approximate page count.
  fireEvent.change(screen.getByLabelText("Take page numbers from"), { target: { value: "estimate" } });
  expect(await screen.findByText("an estimate from word count")).toBeInTheDocument();
  expect(screen.getByLabelText("Words per page")).toHaveValue(250);

  fireEvent.click(screen.getByRole("button", { name: "Import manuscript" }));
  await waitFor(() => expect(imports).toHaveLength(1));
  expect(imports[0]).toEqual({ source: { path: "/m/book.pdf" }, settings: { ...defaults, mode: "estimate" } });
});

test("preview errors are shown and block import", async () => {
  mockApi({
    "POST /api/project/manuscript/preview": new Response(
      JSON.stringify({ detail: "the PDF has no text layer (is it scanned?)" }),
      { status: 400 },
    ),
  });
  render(<ManuscriptPanel info={null} defaults={defaults} revision={0} onError={() => {}} />);
  fireEvent.change(screen.getByLabelText("Or by path"), { target: { value: "/m/scan.pdf" } });
  fireEvent.click(screen.getByRole("button", { name: "Preview" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("no text layer");
  expect(screen.getByRole("button", { name: "Import manuscript" })).toBeDisabled();
});

test("existing manuscript: summary, units, and re-mapping pages from the project copy", async () => {
  const sent: unknown[] = [];
  const remaps: unknown[] = [];
  mockApi({
    "GET /api/project/manuscript/units": {
      total: 13,
      offset: 0,
      units: [{ ...preview().sentences[0], text: "Chapter One. The Fox", page_label: "1", page_end_label: "2" }],
    },
    "POST /api/project/manuscript/preview": (body: unknown) => {
      sent.push(body);
      return preview();
    },
    "PUT /api/project/manuscript/settings": (body: unknown) => {
      remaps.push(body);
      return { job_id: "j2" };
    },
  });
  render(<ManuscriptPanel info={info} defaults={defaults} revision={0} onError={() => {}} />);
  expect(screen.getByText(/3 pages · 2 chapters · 13 sentences/)).toBeInTheDocument();
  expect(await screen.findByText("1–2")).toBeInTheDocument(); // a unit spanning pages

  fireEvent.click(screen.getByRole("button", { name: "Change page settings…" }));
  // Starts from the manuscript's current settings, previews the project's copy.
  expect(screen.getByLabelText("Skip front matter")).toBeChecked();
  await waitFor(() => expect(sent).toHaveLength(1));
  expect(sent[0]).toEqual({ source: { current: true }, settings: info.settings });

  fireEvent.click(screen.getByLabelText("Use the PDF's own page numbers when it has them"));
  await waitFor(() => expect(sent).toHaveLength(2));
  fireEvent.click(await screen.findByRole("button", { name: "Apply page settings" }));
  await waitFor(() => expect(remaps).toHaveLength(1));
  expect(remaps[0]).toEqual({ settings: { ...info.settings, use_pdf_labels: false } });
});

test("a damaged project copy is called out", () => {
  mockApi({ "GET /api/project/manuscript/units": { total: 0, offset: 0, units: [] } });
  render(<ManuscriptPanel info={{ ...info, status: "modified" }} defaults={defaults} revision={0} onError={() => {}} />);
  expect(screen.getByRole("alert")).toHaveTextContent("changed on disk");
});

test("page settings form only offers what applies", () => {
  const onChange = () => {};
  const { rerender } = render(<PageSettingsForm format="txt" value={defaults} onChange={onChange} />);
  expect(screen.getByLabelText(/Page marker pattern/)).toBeInTheDocument();
  expect(screen.queryByLabelText(/PDF's own page numbers/)).not.toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "PDF pages" })).not.toBeInTheDocument();

  rerender(<PageSettingsForm format="docx" value={defaults} onChange={onChange} />);
  expect(screen.queryByLabelText(/Page marker pattern/)).not.toBeInTheDocument();
  expect(screen.queryByRole("option", { name: "Page markers in the text" })).not.toBeInTheDocument();
  expect(screen.getByLabelText("Printed page 1 is physical page")).toBeInTheDocument();

  rerender(<PageSettingsForm format="pdf" value={{ ...defaults, mode: "estimate" }} onChange={onChange} />);
  expect(screen.getByLabelText("Words per page")).toBeInTheDocument();
  expect(screen.queryByLabelText("Printed page 1 is physical page")).not.toBeInTheDocument();
});
