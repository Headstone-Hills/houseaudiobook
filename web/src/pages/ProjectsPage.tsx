import { useState, type FormEvent } from "react";
import { EngineStatus } from "../components/EngineStatus";
import { api, type FunctionDef, type ProjectSummary, type Settings } from "../state/engine";
import { explain, toggle } from "../state/functions";

interface Props {
  settings: Settings;
  functionDefs: FunctionDef[];
  onOpened: (p: ProjectSummary) => void;
}

/** Launch screen (SPEC §6.1): recent projects, open existing, new project. */
export function ProjectsPage({ settings, functionDefs, onOpened }: Props) {
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [location, setLocation] = useState(settings.default_location);
  const [selected, setSelected] = useState<string[]>(functionDefs.map((f) => f.key));
  const [openPath, setOpenPath] = useState("");

  const run = (p: Promise<ProjectSummary>) =>
    p.then(onOpened).catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));

  const create = (e: FormEvent) => {
    e.preventDefault();
    void run(api.createProject(location, name, selected));
  };

  return (
    <main>
      <h1>HillAudiobook</h1>
      <EngineStatus />
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      <section aria-labelledby="recent-heading">
        <h2 id="recent-heading">Recent projects</h2>
        {settings.recent.length === 0 ? (
          <p>No recent projects.</p>
        ) : (
          <ul className="recent">
            {settings.recent.map((r) => (
              <li key={r.path}>
                <button type="button" onClick={() => void run(api.openProject(r.path))}>
                  {r.name}
                </button>{" "}
                <span className="muted">{r.path}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="open-heading">
        <h2 id="open-heading">Open existing</h2>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void run(api.openProject(openPath.trim()));
          }}
        >
          <label htmlFor="open-path">Project folder (.hillproj)</label>
          <input id="open-path" value={openPath} onChange={(e) => setOpenPath(e.target.value)} />
          <button type="submit" disabled={!openPath.trim()}>
            Open
          </button>
        </form>
      </section>

      <section aria-labelledby="new-heading">
        <h2 id="new-heading">New project</h2>
        <form onSubmit={create}>
          <label htmlFor="new-name">Name</label>
          <input id="new-name" value={name} onChange={(e) => setName(e.target.value)} required />
          <label htmlFor="new-location">Location</label>
          <input id="new-location" value={location} onChange={(e) => setLocation(e.target.value)} />
          <fieldset>
            <legend>Automated functions</legend>
            {functionDefs.map((f) => {
              const note = explain(functionDefs, f.key);
              return (
                <label key={f.key} className="function">
                  <input
                    type="checkbox"
                    checked={selected.includes(f.key)}
                    onChange={(e) => setSelected(toggle(functionDefs, selected, f.key, e.target.checked))}
                    aria-describedby={note ? `fn-note-${f.key}` : undefined}
                  />
                  {f.label}
                  {note && (
                    <span id={`fn-note-${f.key}`} className="muted">
                      {" "}
                      ({note})
                    </span>
                  )}
                </label>
              );
            })}
          </fieldset>
          <button type="submit" disabled={!name.trim()}>
            Create project
          </button>
        </form>
      </section>
    </main>
  );
}
