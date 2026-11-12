import { describe, expect, it } from "vitest";

import { weeklyBody } from "./fixtures-reports.test-data";
import { blocksOf, decodeWeekly, fetchWeekOf } from "./reports";

describe("the network's weekly report", () => {
  it("decodes the body the platform serves", () => {
    const week = decodeWeekly(weeklyBody);
    expect(week.template).toBe("network-weekly-1");
    expect(week.previousWeek).toBe("2026-10-26");
    expect(week.includesSimulated).toBe(true);
    expect(decodeWeekly({ ...weeklyBody, previous_week: null }).previousWeek).toBeNull();
  });

  it("is refused whole when a field is missing", () => {
    const partial: Record<string, unknown> = { ...weeklyBody };
    delete partial.subject;
    expect(() => decodeWeekly(partial)).toThrow("weekly.subject");
  });

  it("splits into the blocks its blank lines separate, the simulated one marked", () => {
    const blocks = blocksOf(weeklyBody.body);
    expect(blocks.map((one) => one.lines[0])).toEqual([
      "Week: 2026-11-02 to 2026-11-09, Monday to Monday UTC. A pass belongs to the week its window closed in.",
      "MEASURED STATIONS",
      "SIMULATED STATIONS — not real receptions.",
      "Written from Meridian's stored results by template network-weekly-1; no part of it was generated.",
    ]);
    expect(blocks.map((one) => one.simulated)).toEqual([false, false, true, false]);
    expect(blocks.map((one) => one.headed)).toEqual([false, true, true, false]);
  });

  it("asks for an earlier week by its Monday", async () => {
    const urls: string[] = [];
    await fetchWeekOf(
      (url) => {
        urls.push(url);
        return Promise.resolve(new Response(JSON.stringify(weeklyBody)));
      },
      "2026-10-26",
      new AbortController().signal,
    );
    expect(urls).toEqual(["/api/v1/reports/weekly?week=2026-10-26"]);
  });
});
