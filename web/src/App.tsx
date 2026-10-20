import { useCallback, useEffect, useRef, useState } from "react";
import { ProjectPage } from "./pages/ProjectPage";
import { ProjectsPage } from "./pages/ProjectsPage";
import { api, subscribeJobs, type FunctionDef, type Job, type ProjectSummary, type Settings } from "./state/engine";

export function App() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [functionDefs, setFunctionDefs] = useState<FunctionDef[]>([]);
  const [project, setProject] = useState<ProjectSummary | null>(null);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [error, setError] = useState<string | null>(null);
  const finished = useRef(new Set<string>());

  const loadHome = useCallback(() => {
    Promise.all([api.settings(), api.functions()])
      .then(([s, f]) => {
        setSettings(s);
        setFunctionDefs(f);
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
  }, []);

  useEffect(loadHome, [loadHome]);

  // When any job finishes, the project's sources or statuses may have changed.
  useEffect(
    () =>
      subscribeJobs((next) => {
        setJobs(next);
        const newlyDone = next.filter((j) => j.status !== "running" && j.status !== "queued" && !finished.current.has(j.id));
        if (newlyDone.length) {
          newlyDone.forEach((j) => finished.current.add(j.id));
          api.project().then(setProject).catch(() => undefined);
        }
      }),
    [],
  );

  if (error) {
    return (
      <main>
        <h1>HillAudiobook</h1>
        <p role="alert">Engine unavailable: {error}</p>
      </main>
    );
  }
  if (!settings) return <p role="status">Connecting to engine…</p>;
  if (!project) return <ProjectsPage settings={settings} functionDefs={functionDefs} onOpened={setProject} />;
  return (
    <ProjectPage
      key={project.path}
      project={project}
      jobs={jobs}
      peakSettings={settings.peaks}
      onChanged={setProject}
      onClose={() => {
        api
          .closeProject()
          .then(() => {
            setProject(null);
            loadHome();
          })
          .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)));
      }}
    />
  );
}
