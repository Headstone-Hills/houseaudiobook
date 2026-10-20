import { useEffect, useState } from "react";
import { fetchHealth, type Health } from "../state/engine";

type Status = { kind: "checking" } | { kind: "ok"; health: Health } | { kind: "down"; error: string };

export function EngineStatus() {
  const [status, setStatus] = useState<Status>({ kind: "checking" });

  useEffect(() => {
    const ctrl = new AbortController();
    fetchHealth(ctrl.signal)
      .then((health) => setStatus({ kind: "ok", health }))
      .catch((err: unknown) => {
        if (!ctrl.signal.aborted) setStatus({ kind: "down", error: String(err) });
      });
    return () => ctrl.abort();
  }, []);

  // role="status" so screen readers announce engine availability changes.
  return (
    <p role="status">
      {status.kind === "checking" && "Connecting to engine…"}
      {status.kind === "ok" && `Engine ${status.health.version} ready`}
      {status.kind === "down" && `Engine unavailable: ${status.error}`}
    </p>
  );
}
