// The public API's reference, from the description the platform serves of
// itself at /openapi.json (D-369).
//
// Only the public read routes are listed: every GET under /api/v1, in the order
// the platform declares them, each with its first line of description. MSP's
// routes are a protocol with a specification of its own (docs/MSP-SPEC.md), and
// are left to it. Nothing here is written by the page: a route the platform
// stops serving leaves this list with the build that stopped serving it.

import { getJson, type Fetcher } from "./api";
import { asObject, asString, type Fields } from "./decode";

export interface ApiRoute {
  path: string;
  summary: string;
  description: string;
}

export interface ApiReference {
  version: string;
  routes: ApiRoute[];
}

const PUBLIC = "/api/v1/";

/** A docstring's first paragraph, joined onto one line, its ``literal`` marks dropped. */
function firstParagraph(text: string): string {
  return (text.split(/\n\s*\n/, 1)[0] ?? "")
    .replace(/``([^`]+)``/g, "$1")
    .replace(/\s+/g, " ")
    .trim();
}

function route(path: string, operation: unknown): ApiRoute | null {
  if (typeof operation !== "object" || operation === null) return null;
  const fields = operation as Fields;
  const summary = typeof fields.summary === "string" ? fields.summary : "";
  const description = typeof fields.description === "string" ? firstParagraph(fields.description) : "";
  return { path, summary, description };
}

export function decodeApiReference(body: unknown): ApiReference {
  const fields = asObject(body, "openapi");
  const info = asObject(fields.info, "openapi.info");
  const paths = asObject(fields.paths, "openapi.paths");
  const routes: ApiRoute[] = [];
  for (const [path, item] of Object.entries(paths)) {
    if (!path.startsWith(PUBLIC) || typeof item !== "object" || item === null) continue;
    const one = route(path, (item as Fields).get);
    if (one !== null) routes.push(one);
  }
  return { version: asString(info, "version", "openapi.info"), routes };
}

export async function fetchApiReference(fetcher: Fetcher, signal: AbortSignal): Promise<ApiReference> {
  return decodeApiReference(await getJson(fetcher, "/openapi.json", "the API's description", signal));
}
