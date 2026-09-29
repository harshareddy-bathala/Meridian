import { useEffect, useState } from "react";

import { fetchProfiles, type StationProfiles } from "./profiles";
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
  /** Null for the whole network, while loading, or when the read failed. */
  profiles: StationProfiles | null;
  error: string | null;
}

const LOADING: StationDetailState = { heartbeat: undefined, assignments: null, profiles: null, error: null };

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
        // A horizon that cannot be read is left out; it must not hide the
        // schedule, which is what this panel is for.
        stationId === null
          ? Promise.resolve(null)
          : fetchProfiles(fetcher, stationId, controller.signal).catch(() => null),
      ])
        .then(([heartbeat, assignments, profiles]) => {
          setState({ key: stationId, value: { heartbeat, assignments, profiles, error: null } });
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
