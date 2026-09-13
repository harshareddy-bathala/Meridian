import { useEffect, useState } from "react";

import { fetchAllStations, type Station } from "./stations";

/**
 * D-030's heartbeat interval. Polling faster cannot show anything new, because
 * liveness only changes when a heartbeat arrives or a threshold passes.
 */
export const REFRESH_MS = 30_000;

export interface StationsState {
  /** The last directory that loaded, kept on screen through a failed refresh. */
  stations: Station[] | null;
  /** When `stations` was fetched: liveness was derived at about this instant. */
  fetchedAt: Date | null;
  /** Why the most recent refresh failed, or null if it succeeded. */
  error: string | null;
}

export function useStations(): StationsState {
  const [state, setState] = useState<StationsState>({
    stations: null,
    fetchedAt: null,
    error: null,
  });

  useEffect(() => {
    const controller = new AbortController();
    const refresh = () => {
      fetchAllStations((url, init) => fetch(url, init), controller.signal)
        .then((stations) => {
          setState({ stations, fetchedAt: new Date(), error: null });
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
