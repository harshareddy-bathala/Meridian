import { useState } from "react";

import type { Fetcher } from "./api";

// Pages older than the newest, loaded when a reader asks and never polled.
//
// An older page is not warmed through the front (D-283), so it is fetched
// directly: in an outage it fails and says so, rather than being served as a
// copy. The newest page is the view's own polled read; this only continues it.

export interface Page<T> {
  items: T[];
  nextCursor: string | null;
}

export type LoadPage<T> = (fetcher: Fetcher, cursor: string, signal: AbortSignal) => Promise<Page<T>>;

const direct: Fetcher = (url, init) => fetch(url, init);

/**
 * The older items loaded so far, whether there are more, and a way to load the
 * next page after ``firstCursor``, the newest page's own cursor.
 */
export function useOlderPages<T>(load: LoadPage<T>, firstCursor: string | null) {
  const [older, setOlder] = useState<Page<T> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const cursor = older === null ? firstCursor : older.nextCursor;
  const more = () => {
    if (cursor === null) return;
    load(direct, cursor, new AbortController().signal)
      .then((page) => {
        setOlder((held) => ({ items: [...(held?.items ?? []), ...page.items], nextCursor: page.nextCursor }));
        setError(null);
      })
      .catch((reason: unknown) => {
        setError(String(reason));
      });
  };
  return { older: older?.items ?? [], hasMore: cursor !== null, more, error };
}

/** The newest page and the older ones, each item once by ``key``. */
export function mergeOnce<T>(newest: T[], older: T[], key: (item: T) => string): T[] {
  const seen = new Set(newest.map(key));
  return [...newest, ...older.filter((one) => !seen.has(key(one)))];
}
