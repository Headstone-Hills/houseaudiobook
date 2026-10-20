import { useEffect, useState } from "react";
import { api, type Health } from "../state/engine";

type Status = { kind: "checking" } | { kind: "ok"; health: Health } | { kind: "down"; error: string };

export function EngineStatus() {
  const [status, setStatus] = useState<Status>({ kind: "checking" });

  useEffect(() => {
    let cancelled = false;
    api
      .health()
      .then((health) => !cancelled && setStatus({ kind: "ok", health }))
      .catch((err: unknown) => !cancelled && setStatus({ kind: "down", error: String(err) }));
    return () => {
      cancelled = true;
    };
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
