import { useEffect, useState } from "react";

import type { Fetcher } from "./api";
import { type Snapshot, watchSnapshots } from "./snapshot";
import { REFRESH_MS } from "./useStations";

// One read, refreshed on a timer, for a view of its own. Each refresh runs
// through a new snapshot watch, so a view drawn from the front's kept copy says
// so until the first refresh the platform answers (D-284), and a failed refresh
// keeps the last value on screen beside its reason.

export interface Polled<T> {
  /** The last value that loaded; null until one has. */
  value: T | null;
  /** When `value` was made: the copy's own time for a snapshot. */
  fetchedAt: Date | null;
  snapshot: Snapshot | null;
  /** Why the most recent refresh failed, or null if it succeeded. */
  error: string | null;
}

export type Load<T> = (fetcher: Fetcher, signal: AbortSignal) => Promise<T>;

/**
 * ``load`` every ``intervalMs``, from now. ``key`` names what is loaded: a new
 * key starts again from nothing, so one station's figures are never shown under
 * another's name.
 */
export function usePolled<T>(key: string, load: Load<T>, intervalMs: number = REFRESH_MS): Polled<T> {
  const empty: Polled<T> = { value: null, fetchedAt: null, snapshot: null, error: null };
  const [state, setState] = useState<{ key: string; polled: Polled<T> }>({ key, polled: empty });

  useEffect(() => {
    const controller = new AbortController();
    const refresh = () => {
      const watch = watchSnapshots();
      load(watch.fetcher, controller.signal)
        .then((value) => {
          const snapshot = watch.snapshot();
          setState({
            key,
            polled: { value, fetchedAt: snapshot?.takenAt ?? new Date(), snapshot, error: null },
          });
        })
        .catch((error: unknown) => {
          if (!controller.signal.aborted) {
            setState((previous) => ({
              key,
              polled: { ...(previous.key === key ? previous.polled : empty), error: String(error) },
            }));
          }
        });
    };
    refresh();
    const timer = window.setInterval(refresh, intervalMs);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
    // `load` is a module function bound to `key`; a new key is a new read.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, intervalMs]);

  return state.key === key ? state.polled : empty;
}
