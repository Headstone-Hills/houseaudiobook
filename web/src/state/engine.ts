// Thin client for the local engine API. The UI never touches audio files
// directly; everything goes through these calls (SPEC §3).

export interface Health {
  status: string;
  version: string;
}

export async function fetchHealth(signal?: AbortSignal): Promise<Health> {
  const resp = await fetch("/api/health", { signal });
  if (!resp.ok) throw new Error(`engine returned ${resp.status}`);
  return (await resp.json()) as Health;
}
