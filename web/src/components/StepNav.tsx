import { STEPS, type StepKey, type StepStatus } from "../state/steps";

const MARK: Record<StepStatus["state"], string> = {
  done: "✓",
  todo: "",
  attention: "",
  blocked: "",
  idle: "",
};

interface Props {
  current: StepKey;
  statuses: Record<StepKey, StepStatus>;
  onSelect: (key: StepKey) => void;
}

/** The workflow down the side: one entry per step, with where it stands. */
export function StepNav({ current, statuses, onSelect }: Props) {
  return (
    <nav aria-label="Steps" className="step-nav">
      <ol>
        {STEPS.map((s, i) => {
          const st = statuses[s.key];
          return (
            <li key={s.key}>
              <button
                type="button"
                className={`step-link state-${st.state}`}
                aria-current={s.key === current ? "step" : undefined}
                title={st.note || undefined}
                onClick={() => onSelect(s.key)}
              >
                <span className="step-num" aria-hidden="true">
                  {MARK[st.state] || (i === 0 ? "◆" : i)}
                </span>
                <span className="step-title">{s.title}</span>
                {st.badge !== undefined && st.badge > 0 && (
                  <span className="step-badge" aria-label={st.note}>
                    {st.badge}
                  </span>
                )}
                {st.state === "done" && <span className="visually-hidden"> (done)</span>}
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
