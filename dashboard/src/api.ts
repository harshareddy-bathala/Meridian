// One GET against the platform's own origin, and its error in D-084's words.

export type Fetcher = (url: string, init: { signal: AbortSignal }) => Promise<Response>;

/** The body of a successful response, or an error naming what refused and why. */
export async function getJson(
  fetcher: Fetcher,
  url: string,
  what: string,
  signal: AbortSignal,
): Promise<unknown> {
  const response = await fetcher(url, { signal });
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // Not JSON: the front's own error page, below, or a broken answer.
    if (response.ok) {
      throw new Error(`${what} answered with something that is not JSON`);
    }
  }
  if (!response.ok) {
    if (typeof body === "object" && body !== null && "message" in body) {
      // D-084's envelope: the platform answered, and said why not.
      throw new Error(`${what} refused the request: ${String(body.message)}`);
    }
    if (GATEWAY_STATUSES.has(response.status)) {
      // The front could not reach the platform and holds no copy of this view
      // (D-282): a view nobody asked for before the outage began.
      throw new Error(
        `${what} is unavailable: the platform did not answer (HTTP ${String(response.status)}), and no copy of it was kept`,
      );
    }
    throw new Error(`${what} refused the request: HTTP ${String(response.status)}`);
  }
  return body;
}

/** What the front answers with when the platform does not. */
const GATEWAY_STATUSES = new Set([502, 503, 504]);

/** Checks a page body's shape and hands each item to ``decodeItem``. */
export function decodeItems<T>(
  body: unknown,
  decodeItem: (item: unknown, path: string) => T,
): { items: T[]; nextCursor: string | null } {
  if (typeof body !== "object" || body === null || !("items" in body)) {
    throw new Error("page: expected an object with items");
  }
  const { items } = body;
  if (!Array.isArray(items)) {
    throw new Error("page.items: expected an array");
  }
  const cursor = "next_cursor" in body ? body.next_cursor : null;
  return {
    items: items.map((item, index) => decodeItem(item, `page.items[${String(index)}]`)),
    nextCursor: typeof cursor === "string" ? cursor : null,
  };
}
