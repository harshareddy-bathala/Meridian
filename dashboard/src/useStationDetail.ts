import { useEffect, useState } from "react";

import { fetchProfiles, type StationProfiles } from "./profiles";
import { fetchRecentReceptions, type Reception } from "./receptions";
import { fetchStationCapture, type StationCapture } from "./reliability";
import { fetchUptime, type StationUptime } from "./uptime";
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
  /** Null for the whole network, while loading, or when the read failed. */
  uptime: StationUptime | null;
  /** Undefined while loading; null for the whole network or when the read failed. */
  receptions: Reception[] | null | undefined;
  /** Undefined until read; null when the station has nothing counted yet. */
  capture: StationCapture | null | undefined;
  error: string | null;
}

const LOADING: StationDetailState = {
  heartbeat: undefined,
  assignments: null,
  profiles: null,
  uptime: null,
  receptions: undefined,
  capture: undefined,
  error: null,
};

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
        stationId === null
          ? Promise.resolve(null)
          : fetchUptime(fetcher, stationId, controller.signal).catch(() => null),
        stationId === null
          ? Promise.resolve(null)
          : fetchRecentReceptions(fetcher, stationId, controller.signal).catch(() => null),
        // A deployment with no reliability settings answers 503 here; that
        // leaves the figure out, it does not stop the schedule refreshing.
        stationId === null
          ? Promise.resolve(undefined)
          : fetchStationCapture(fetcher, stationId, controller.signal).catch(() => undefined),
      ])
        .then(([heartbeat, assignments, profiles, uptime, receptions, capture]) => {
          setState({
            key: stationId,
            value: { heartbeat, assignments, profiles, uptime, receptions, capture, error: null },
          });
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
