// The front keeps a copy under the exact path and query a browser asked for
// (D-282), and deploy/tools/front_warm.py asks for each route in
// deploy/front/snapshot-routes.txt so every view has one (D-283). A route the
// dashboard builds differently from that file is a view warmed under a key no
// visitor will ask for, and found empty in the outage it was warmed for. So the
// dashboard's own fetch functions are run here and their requests compared
// with the file, both ways.
//
// A new fetch function the dashboard calls belongs in `requests` below, and its
// route in the file.
import { describe, expect, it } from "vitest";

import routesFile from "../../deploy/front/snapshot-routes.txt?raw";
import type { Fetcher } from "./api";
import { fetchProfiles } from "./profiles";
import { fetchRecentReceptions } from "./receptions";
import { fetchReceiveChains } from "./receiveChain";
import { fetchStationCapture } from "./reliability";
import { fetchLatestHeartbeat, fetchUpcomingAssignments } from "./schedule";
import { fetchAllStations } from "./stations";
import { fetchUptime } from "./uptime";

// What the warmer substitutes; a station id as the platform mints them.
const STATION = "{station}";
const STATION_ID = "st_0123456789ab";

function routes(): string[] {
  return routesFile
    .split("\n")
    .map((line) => (line.split("#", 1)[0] ?? "").trim())
    .filter((line) => line !== "");
}

async function requests(): Promise<string[]> {
  const urls: string[] = [];
  const fetcher: Fetcher = (url) => {
    urls.push(url);
    return Promise.resolve(new Response(JSON.stringify({ items: [], next_cursor: null })));
  };
  const signal = new AbortController().signal;
  // Each is asked for its URL only; a body it cannot decode is not this test's.
  const calls: Promise<unknown>[] = [
    fetchAllStations(fetcher, signal),
    fetchStationCapture(fetcher, STATION_ID, signal),
    fetchUpcomingAssignments(fetcher, null, signal),
    fetchLatestHeartbeat(fetcher, STATION_ID, signal),
    fetchUpcomingAssignments(fetcher, STATION_ID, signal),
    fetchProfiles(fetcher, STATION_ID, signal),
    fetchUptime(fetcher, STATION_ID, signal),
    fetchRecentReceptions(fetcher, STATION_ID, signal),
    fetchReceiveChains(fetcher, STATION_ID, signal),
  ];
  await Promise.allSettled(calls);
  return urls;
}

describe("the routes the front is warmed with", () => {
  it("are exactly the requests the dashboard makes", async () => {
    const warmed = routes().map((route) => route.replace(STATION, STATION_ID));
    expect(new Set(await requests())).toEqual(new Set(warmed));
  });

  it("are listed once each", () => {
    expect(new Set(routes()).size).toBe(routes().length);
  });
});
