import { afterEach, describe, expect, it, vi } from "vitest";

import { getJson, type Fetcher } from "./api";
import { fetchPlatformHealth } from "./health";
import {
  describeSnapshot,
  oldest,
  snapshotClass,
  snapshotOf,
  watchSnapshots,
} from "./snapshot";

const signal = new AbortController().signal;

function answer(cache: string | null, date: string | null = null, body = "{}", status = 200): Response {
  const headers = new Headers();
  if (cache !== null) {
    headers.set("Meridian-Cache", cache);
  }
  if (date !== null) {
    headers.set("Date", date);
  }
  return new Response(body, { status, headers });
}

const EARLY = "Fri, 02 Oct 2026 14:49:12 GMT";
const LATE = "Fri, 02 Oct 2026 14:57:57 GMT";

describe("a response from the front", () => {
  it("is a snapshot only when the front says STALE, made when the platform dated it", () => {
    expect(snapshotOf(answer("STALE", EARLY))).toEqual({ takenAt: new Date(EARLY) });
    for (const live of ["MISS", "EXPIRED", "HIT", null]) {
      expect(snapshotOf(answer(live, EARLY))).toBeNull();
    }
  });

  it("is a snapshot of unknown age when the copy carries no date", () => {
    expect(snapshotOf(answer("STALE"))).toEqual({ takenAt: null });
  });
});

describe("a view drawing on several answers", () => {
  it("is as old as the oldest copy it used", () => {
    const early = { takenAt: new Date(EARLY) };
    const late = { takenAt: new Date(LATE) };
    expect(oldest(late, early)).toBe(early);
    expect(oldest(null, late)).toBe(late);
    expect(oldest(null, null)).toBeNull();
    expect(oldest(early, { takenAt: null })).toEqual({ takenAt: null });
  });

  it("is reported by its watch: live, live and two copies is the older copy", async () => {
    const replies = [answer("EXPIRED"), answer("STALE", LATE), answer("MISS"), answer("STALE", EARLY)];
    const watch = watchSnapshots(() => Promise.resolve(replies.shift() ?? answer(null)));
    for (let i = 0; i < 4; i += 1) {
      await watch.fetcher("/api/v1/reliability", { signal });
    }
    expect(watch.snapshot()).toEqual({ takenAt: new Date(EARLY) });
  });

  it("is live when every answer was the platform's", async () => {
    const watch = watchSnapshots(() => Promise.resolve(answer("EXPIRED", LATE)));
    await watch.fetcher("/", { signal });
    expect(watch.snapshot()).toBeNull();
  });

  it("is not left waiting on a platform that never answers", async () => {
    const never: Fetcher = (_url, init) =>
      new Promise((_resolve, reject) => {
        init.signal.addEventListener("abort", () => {
          reject(init.signal.reason as Error);
        });
      });
    const watch = watchSnapshots(never, 10);
    await expect(watch.fetcher("/api/v1/reliability", { signal })).rejects.toMatchObject({
      name: "TimeoutError",
    });
  });
});

describe("the words for a snapshot", () => {
  it("say when it was taken and how long ago", () => {
    const now = new Date(Date.parse(EARLY) + 7 * 60_000);
    expect(describeSnapshot({ takenAt: new Date(EARLY) }, now)).toBe(
      "a snapshot taken 14:49 UTC, 7 min ago",
    );
    expect(describeSnapshot({ takenAt: null }, now)).toBe("a snapshot of unknown age");
  });

  it("add the snapshot class to a view only when it is one", () => {
    expect(snapshotClass("station-detail", null)).toBe("station-detail");
    expect(snapshotClass("station-detail", { takenAt: null })).toBe("station-detail snapshot");
    expect(snapshotClass("", null)).toBeUndefined();
  });
});

describe("a view the front kept no copy of", () => {
  const gatewayPage = "<html><body><h1>504 Gateway Time-out</h1></body></html>";

  it("says the platform did not answer, not that the page was malformed", async () => {
    const fetcher: Fetcher = () => Promise.resolve(answer(null, null, gatewayPage, 504));
    await expect(getJson(fetcher, "/api/v1/reliability", "the reliability summary", signal)).rejects.toThrow(
      "the reliability summary is unavailable: the platform did not answer (HTTP 504), and no copy of it was kept",
    );
  });

  it("still reports the platform's own refusal in its words", async () => {
    const envelope = JSON.stringify({ error: "unavailable", message: "no reliability settings" });
    const fetcher: Fetcher = () => Promise.resolve(answer(null, null, envelope, 503));
    await expect(getJson(fetcher, "/api/v1/reliability", "the reliability summary", signal)).rejects.toThrow(
      "the reliability summary refused the request: no reliability settings",
    );
  });

  it("is not mistaken for an answer when a 200 is not JSON", async () => {
    const fetcher: Fetcher = () => Promise.resolve(answer(null, null, "<html></html>"));
    await expect(getJson(fetcher, "/api/v1/stations", "the station directory", signal)).rejects.toThrow(
      "not JSON",
    );
  });
});

describe("the platform's health with the platform down", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("is said plainly, since the front never keeps /healthz", async () => {
    vi.stubGlobal("fetch", () => Promise.resolve(answer(null, null, "<html>504</html>", 504)));
    await expect(fetchPlatformHealth(signal)).rejects.toThrow("the platform did not answer (HTTP 504)");
  });
});
