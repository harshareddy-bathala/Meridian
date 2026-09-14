import { describe, expect, it } from "vitest";

import type { Fetcher } from "./api";
import { fetchLatestHeartbeat, fetchUpcomingAssignments } from "./schedule";

const signal = new AbortController().signal;

function serve(body: unknown, status = 200): { fetcher: Fetcher; urls: string[] } {
  const urls: string[] = [];
  const fetcher: Fetcher = (url) => {
    urls.push(url);
    return Promise.resolve(new Response(JSON.stringify(body), { status }));
  };
  return { fetcher, urls };
}

const skipped = {
  assignment_id: "as_1",
  station_id: "st_1",
  satellite_id: "norad:57166",
  start_at: "2026-09-14T06:10:00Z",
  end_at: "2026-09-14T06:22:00Z",
  decision: "skipped",
  reason: "overlaps a higher-scoring pass",
  conflicts_with_assignment_id: "as_2",
  state: "expired",
  simulated: true,
};

describe("fetchUpcomingAssignments", () => {
  it("asks for one station's next assignments and keeps the reason", async () => {
    const { fetcher, urls } = serve({ items: [skipped], next_cursor: null });

    const [assignment] = await fetchUpcomingAssignments(fetcher, "st_1", signal);

    expect(urls).toEqual(["/api/v1/assignments?limit=10&station_id=st_1"]);
    expect(assignment).toMatchObject({
      decision: "skipped",
      reason: "overlaps a higher-scoring pass",
      conflictsWith: "as_2",
    });
  });

  it("asks for the whole network when no station is selected", async () => {
    const { fetcher, urls } = serve({ items: [] });

    await fetchUpcomingAssignments(fetcher, null, signal);

    expect(urls).toEqual(["/api/v1/assignments?limit=10"]);
  });

  it("refuses a decision the scheduler does not make", async () => {
    const { fetcher } = serve({ items: [{ ...skipped, decision: "maybe" }] });

    await expect(fetchUpcomingAssignments(fetcher, null, signal)).rejects.toThrow(
      "page.items[0].decision",
    );
  });
});

describe("fetchLatestHeartbeat", () => {
  const heartbeat = {
    received_at: "2026-09-14T06:11:03Z",
    state: "listening",
    listening: {
      assignment_id: "as_2",
      satellite_id: "norad:57166",
      centre_freq_hz: 137_900_000,
      mode: "lrpt",
    },
    simulated: true,
  };

  it("reads what the station was listening to", async () => {
    const { fetcher, urls } = serve({ items: [heartbeat] });

    const latest = await fetchLatestHeartbeat(fetcher, "st/1", signal);

    expect(urls).toEqual(["/api/v1/stations/st%2F1/heartbeats?limit=1"]);
    expect(latest?.listening?.centreFreqHz).toBe(137_900_000);
  });

  it("keeps 'not listening' distinct from 'never reported'", async () => {
    const idle = serve({ items: [{ ...heartbeat, state: "idle", listening: null }] });
    const silent = serve({ items: [] });

    expect((await fetchLatestHeartbeat(idle.fetcher, "st_1", signal))?.listening).toBeNull();
    expect(await fetchLatestHeartbeat(silent.fetcher, "st_1", signal)).toBeNull();
  });

  it("reports the API's own message for an unknown station", async () => {
    const { fetcher } = serve({ error: "not_found", message: "No station with that id." }, 404);

    await expect(fetchLatestHeartbeat(fetcher, "st_x", signal)).rejects.toThrow(
      "No station with that id.",
    );
  });
});
