import { describe, expect, it } from "vitest";

import type { Fetcher } from "./api";
import {
  decodeExplanation,
  describeValue,
  fetchLatestHeartbeat,
  fetchUpcomingAssignments,
} from "./schedule";

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
  state: null,
  simulated: true,
  explanation: null,
};

const explanation = {
  terms: {
    value: 669.6,
    yield: 0.62,
    yield_source: "model",
    yield_path: "configured",
    yield_reason: "32 settled outcomes, enough for history",
    frames: 720,
    frames_term: "duration",
    priority: 1.5,
    priority_weighted: true,
  },
  weighed_against: [{ pass_id: 7, decision: "scheduled", value: 700.2, assignment_id: null }],
  rule: "overlap",
  alternative: { pass_id: 7, decision: "scheduled", value: 700.2, assignment_id: null },
  run: { status: "optimal", history_as_of: "2026-09-27T06:00:00Z" },
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
      state: null,
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

describe("an explanation", () => {
  it("is read term by term, with what took the slot", async () => {
    const { fetcher } = serve({ items: [{ ...skipped, explanation }] });

    const [assignment] = await fetchUpcomingAssignments(fetcher, null, signal);

    expect(assignment?.explanation).toMatchObject({
      yield: 0.62,
      yieldSource: "model",
      frames: 720,
      priorityWeighted: true,
      alternative: { passId: 7, value: 700.2 },
      runStatus: "optimal",
    });
  });

  it("is described as the product it is", () => {
    const decoded = decodeExplanation(explanation, "e");

    expect(decoded && describeValue(decoded)).toBe(
      "yield 0.62 (model) × 720 s × priority 1.5 = 669.6",
    );
  });

  it("names the elevation proxy, and leaves out terms that do not apply", () => {
    const terms = { ...explanation.terms, yield_source: "elevation_proxy", priority_weighted: false };
    const decoded = decodeExplanation({ ...explanation, terms }, "e");

    expect(decoded && describeValue(decoded)).toBe(
      "yield 0.62 (elevation proxy) × 720 s = 669.6",
    );
  });

  it("refuses a yield source the scheduler does not have", async () => {
    const terms = { ...explanation.terms, yield_source: "guess" };
    const { fetcher } = serve({ items: [{ ...skipped, explanation: { ...explanation, terms } }] });

    await expect(fetchUpcomingAssignments(fetcher, null, signal)).rejects.toThrow(
      "page.items[0].explanation.terms.yield_source",
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
