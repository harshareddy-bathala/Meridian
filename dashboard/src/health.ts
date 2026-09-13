// The platform's /healthz answer, checked rather than cast.
//
// The body arrives as `unknown`, and a dashboard that trusts a cast shows
// `undefined` the day the shape changes instead of saying the shape changed.

export interface PlatformHealth {
  status: string;
  version: string;
  database: string;
}

function isPlatformHealth(body: unknown): body is PlatformHealth {
  if (typeof body !== "object" || body === null) {
    return false;
  }
  const fields = body as Record<string, unknown>;
  return ["status", "version", "database"].every(
    (key) => typeof fields[key] === "string",
  );
}

export async function fetchPlatformHealth(signal: AbortSignal): Promise<PlatformHealth> {
  // 503 is a real answer here — "degraded", database unreachable — so the body
  // is read whatever the status, and only an unexpected shape is an error.
  const response = await fetch("/healthz", { signal });
  const body: unknown = await response.json();
  if (!isPlatformHealth(body)) {
    throw new Error(`unexpected /healthz body (HTTP ${String(response.status)})`);
  }
  return body;
}
