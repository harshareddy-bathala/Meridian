import { useEffect, useState } from "react";

import { fetchStationCapture, type StationCapture } from "./reliability";
import { type Snapshot, watchSnapshots } from "./snapshot";

export const CAPTURE_REFRESH_MS = 300_000;
/**
 * Five minutes, not the panel's thirty seconds. `/api/v1/reliability` counts
 * the whole network over the SLO window on every request, and a station's
 * capture moves only when one of its passes settles, a day after its window
 * (D-182). Asking every thirty seconds would recount it for nothing.
 */

export interface CaptureState {
  /** Undefined until read, null if nothing is counted. */
  capture: StationCapture | null | undefined;
  /** Set when the figure is the front's kept copy rather than the platform's answer. */
  snapshot: Snapshot | null;
}

const UNREAD: CaptureState = { capture: undefined, snapshot: null };

/** One station's capture rate, and whether it is live. */
export function useStationCapture(stationId: string | null): CaptureState {
  const [state, setState] = useState<{ key: string | null; value: CaptureState }>({
    key: stationId,
    value: UNREAD,
  });

  useEffect(() => {
    if (stationId === null) {
      return undefined;
    }
    const controller = new AbortController();
    const refresh = () => {
      const watch = watchSnapshots();
      // A deployment with no reliability settings answers 503; the figure is
      // then left out, and nothing else on the panel waits for it.
      fetchStationCapture(watch.fetcher, stationId, controller.signal)
        .then((capture) => {
          setState({ key: stationId, value: { capture, snapshot: watch.snapshot() } });
        })
        .catch(() => undefined);
    };
    refresh();
    const timer = window.setInterval(refresh, CAPTURE_REFRESH_MS);
    return () => {
      window.clearInterval(timer);
      controller.abort();
    };
  }, [stationId]);

  // A panel must never show one station's capture under another's name.
  return state.key === stationId ? state.value : UNREAD;
}
