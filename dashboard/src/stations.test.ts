import { describe, expect, it } from "vitest";

import { decodeStationPage, fetchAllStations, MAX_PAGES, type Fetcher } from "./stations";

function stationBody(stationId: string, overrides: Record<string, unknown> = {}) {
  return {
    station_id: stationId,
    name: `Station ${stationId}`,
    operator: "tests",
    location: { lat_deg: 12.97, lon_deg: 77.59, alt_m: 920 },
    location_precision_decimals: 2,
    simulated: true,
    liveness: "online",
    last_heartbeat_at: "2026-09-13T10:00:00Z",
    registered_at: "2026-09-01T00:00:00Z",
    ...overrides,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status });
}

const signal = new AbortController().signal;

describe("decodeStationPage", () => {
  it("reads the API's snake_case body into a station", () => {
    const page = decodeStationPage({ items: [stationBody("st_1")], next_cursor: "abc" });

    expect(page.nextCursor).toBe("abc");
    expect(page.stations[0]).toMatchObject({
      stationId: "st_1",
      latDeg: 12.97,
      simulated: true,
      liveness: "online",
    });
  });

  it("treats an absent next_cursor as the last page", () => {
    expect(decodeStationPage({ items: [] }).nextCursor).toBeNull();
  });

  it("refuses a station that does not say whether it is simulated", () => {
    // CLAUDE.md rule 5: never infer that a result is measured.
    const withoutFlag: Record<string, unknown> = stationBody("st_1");
    delete withoutFlag.simulated;

    expect(() => decodeStationPage({ items: [withoutFlag] })).toThrow(
      "page.items[0].simulated: expected a boolean",
    );
  });

  it("refuses a liveness value the registry does not define", () => {
    const body = { items: [stationBody("st_1", { liveness: "up" })] };

    expect(() => decodeStationPage(body)).toThrow("page.items[0].liveness");
  });

  it("accepts a station that has never sent a heartbeat", () => {
    const body = {
      items: [stationBody("st_1", { liveness: "never_seen", last_heartbeat_at: null })],
    };

    expect(decodeStationPage(body).stations[0]?.lastHeartbeatAt).toBeNull();
  });
});

describe("fetchAllStations", () => {
  it("follows the cursor until the API stops returning one", async () => {
    const requested: string[] = [];
    const fetcher: Fetcher = (url) => {
      requested.push(url);
      const page = url.includes("cursor=")
        ? { items: [stationBody("st_2")], next_cursor: null }
        : { items: [stationBody("st_1")], next_cursor: "c1" };
      return Promise.resolve(json(page));
    };

    const stations = await fetchAllStations(fetcher, signal);

    expect(stations.map((station) => station.stationId)).toEqual(["st_1", "st_2"]);
    expect(requested).toEqual([
      "/api/v1/stations?limit=200",
      "/api/v1/stations?limit=200&cursor=c1",
    ]);
  });

  it("reports the envelope's message when the API refuses", async () => {
    const fetcher: Fetcher = () =>
      Promise.resolve(json({ error: "invalid_query", message: "cursor is not valid." }, 400));

    await expect(fetchAllStations(fetcher, signal)).rejects.toThrow("cursor is not valid.");
  });

  it("gives up on a cursor that never ends", async () => {
    let calls = 0;
    const fetcher: Fetcher = () => {
      calls += 1;
      return Promise.resolve(json({ items: [], next_cursor: "again" }));
    };

    await expect(fetchAllStations(fetcher, signal)).rejects.toThrow("did not end");
    expect(calls).toBe(MAX_PAGES);
  });
});
