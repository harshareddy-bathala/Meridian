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
  const body: unknown = await response.json();
  if (!response.ok) {
    // D-084's envelope; fall back to the status if even that is missing.
    const message =
      typeof body === "object" && body !== null && "message" in body
        ? String(body.message)
        : `HTTP ${String(response.status)}`;
    throw new Error(`${what} refused the request: ${message}`);
  }
  return body;
}

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
