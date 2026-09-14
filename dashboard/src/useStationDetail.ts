import { useEffect, useState } from "react";

import {
  fetchLatestHeartbeat,
  fetchUpcomingAssignments,
  type Assignment,
  type LatestHeartbeat,
} from "./schedule";
import { REFRESH_MS } from "./useStations";

export interface StationDetailState {
  /** Undefined while loading; null when the station has never reported. */
  heartbeat: LatestHeartbeat | null | undefined;
  assignments: Assignment[] | null;
  error: string | null;
}

const LOADING: StationDetailState = { heartbeat: undefined, assignments: null, error: null };

/** The listening state and upcoming assignments of one station, or of the network. */
export function useStationDetail(stationId: string | null): StationDetailState {
  const [state, setState] = useState<{ key: string | null; value: StationDetailState }>({
    key: stationId,
    value: LOADING,
  });

  useEffect(() => {
    const controller = new AbortController();
    const fetcher = (url: string, init: { signal: AbortSignal }) => fetch(url, init);
    const refresh = () => {
      Promise.all([
        stationId === null ? Promise.resolve(null) : fetchLatestHeartbeat(fetcher, stationId, controller.signal),
        fetchUpcomingAssignments(fetcher, stationId, controller.signal),
      ])
        .then(([heartbeat, assignments]) => {
          setState({ key: stationId, value: { heartbeat, assignments, error: null } });
        })
        .catch((error: unknown) => {
          if (!controller.signal.aborted) {
            setState((previous) => ({
              key: stationId,
              value: { ...previous.value, error: String(error) },
            }));
          }
        });
    };
    refresh();
    const timer = window.setInterval(refresh, REFRESH_MS);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, [stationId]);

  // A panel must never show one station's assignments under another's name.
  return state.key === stationId ? state.value : LOADING;
}
