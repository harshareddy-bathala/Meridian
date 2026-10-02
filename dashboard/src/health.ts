// The platform's /healthz answer, checked rather than cast.
//
// The body arrives as `unknown`, and a dashboard that trusts a cast shows
// `undefined` the day the shape changes instead of saying the shape changed.

import { REQUEST_TIMEOUT_MS } from "./snapshot";

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
  const timed = AbortSignal.any([signal, AbortSignal.timeout(REQUEST_TIMEOUT_MS)]);
  // 503 is a real answer here — "degraded", database unreachable — so the body
  // is read whatever the status, and only an unexpected shape is an error.
  const response = await fetch("/healthz", { signal: timed });
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    // The front never keeps /healthz (D-282): with the platform down, this is
    // the front's own error page, and the plain fact is all it says.
    throw new Error(`the platform did not answer (HTTP ${String(response.status)})`);
  }
  if (!isPlatformHealth(body)) {
    throw new Error(`unexpected /healthz body (HTTP ${String(response.status)})`);
  }
  return body;
}
