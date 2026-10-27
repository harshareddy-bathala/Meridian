import { useEffect, useState } from "react";

import { type Snapshot, watchSnapshots } from "./snapshot";
import { fetchAllStations, type Station } from "./stations";

/**
 * D-030's heartbeat interval. Polling faster cannot show anything new, because
 * liveness only changes when a heartbeat arrives or a threshold passes.
 */
export const REFRESH_MS = 30_000;

export interface StationsState {
  /** The last directory that loaded, kept on screen through a failed refresh. */
  stations: Station[] | null;
  /**
   * When `stations` was made: liveness was derived at about this instant. For
   * a snapshot, the instant the platform made it, not the instant it arrived.
   */
  fetchedAt: Date | null;
  /** Set when the list is the front's kept copy rather than the platform's answer. */
  snapshot: Snapshot | null;
  /** Why the most recent refresh failed, or null if it succeeded. */
  error: string | null;
}

export function useStations(): StationsState {
  const [state, setState] = useState<StationsState>({
    stations: null,
    fetchedAt: null,
    snapshot: null,
    error: null,
  });

  useEffect(() => {
    const controller = new AbortController();
    const refresh = () => {
      const watch = watchSnapshots();
      fetchAllStations(watch.fetcher, controller.signal)
        .then((stations) => {
          const snapshot = watch.snapshot();
          const fetchedAt = snapshot?.takenAt ?? new Date();
          setState({ stations, fetchedAt, snapshot, error: null });
        })
        .catch((error: unknown) => {
          if (!controller.signal.aborted) {
            setState((previous) => ({ ...previous, error: String(error) }));
          }
        });
    };
    refresh();
    const timer = window.setInterval(refresh, REFRESH_MS);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, []);

  return state;
}
