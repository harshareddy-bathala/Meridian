import { describe, expect, it } from "vitest";

import type { Fetcher } from "./api";
import { decodeReception, describeSignal, fetchRecentReceptions, outcomeLabel } from "./receptions";

const signal = new AbortController().signal;

const decoded = {
  observation_id: "ob_1",
  assignment_id: "as_1",
  revision: 1,
  station_id: "st_1",
  satellite_id: "norad:57166",
  started_at: "2026-09-14T06:10:00Z",
  ended_at: "2026-09-14T06:22:00Z",
  outcome: "decoded",
  signal_detected: true,
  peak_snr_db: 12.34,
  provenance: "reported",
  submitted_at: "2026-09-14T06:23:10Z",
  simulated: true,
  products: [{ kind: "image", sha256: "ab".repeat(32), size_bytes: 1024 }],
};

describe("decodeReception", () => {
  it("reads what the station reported", () => {
    const reception = decodeReception(decoded, "item");

    expect(reception.outcome).toBe("decoded");
    expect(reception.products).toBe(1);
    expect(reception.simulated).toBe(true);
  });

  it("refuses a reception that does not say whether it was simulated", () => {
    const unlabelled: Record<string, unknown> = { ...decoded };
    delete unlabelled.simulated;

    expect(() => decodeReception(unlabelled, "item")).toThrow("item.simulated");
  });

  it("keeps a missing SNR missing rather than zero", () => {
    expect(decodeReception({ ...decoded, peak_snr_db: null }, "item").peakSnrDb).toBeNull();
  });
});

describe("the words for a reception", () => {
  it("says what was heard", () => {
    const reception = decodeReception(decoded, "item");

    expect(describeSignal(reception)).toBe("signal, peak 12.3 dB");
    expect(describeSignal({ ...reception, peakSnrDb: null })).toBe("signal");
    expect(describeSignal({ ...reception, signalDetected: false })).toBe("no signal");
    expect(outcomeLabel("no_signal")).toBe("no signal");
  });
});

describe("fetchRecentReceptions", () => {
  it("asks for one station's newest receptions", async () => {
    const urls: string[] = [];
    const fetcher: Fetcher = (url) => {
      urls.push(url);
      return Promise.resolve(new Response(JSON.stringify({ items: [decoded], next_cursor: null })));
    };

    const found = await fetchRecentReceptions(fetcher, "st 1", signal);

    expect(found).toHaveLength(1);
    expect(urls).toEqual(["/api/v1/observations?limit=10&station_id=st+1"]);
  });
});
