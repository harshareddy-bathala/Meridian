import { useEffect, useState } from "react";

import { fetchStationCapture, type StationCapture } from "./reliability";

export const CAPTURE_REFRESH_MS = 300_000;
/**
 * Five minutes, not the panel's thirty seconds. `/api/v1/reliability` counts
 * the whole network over the SLO window on every request, and a station's
 * capture moves only when one of its passes settles, a day after its window
 * (D-182). Asking every thirty seconds would recount it for nothing.
 */

/** One station's capture rate: undefined until read, null if nothing is counted. */
export function useStationCapture(stationId: string | null): StationCapture | null | undefined {
  const [state, setState] = useState<{ key: string | null; value: StationCapture | null | undefined }>({
    key: stationId,
    value: undefined,
  });

  useEffect(() => {
    if (stationId === null) {
      return undefined;
    }
    const controller = new AbortController();
    const fetcher = (url: string, init: { signal: AbortSignal }) => fetch(url, init);
    const refresh = () => {
      // A deployment with no reliability settings answers 503; the figure is
      // then left out, and nothing else on the panel waits for it.
      fetchStationCapture(fetcher, stationId, controller.signal)
        .then((capture) => {
          setState({ key: stationId, value: capture });
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
  return state.key === stationId ? state.value : undefined;
}
