import { describe, expect, it } from "vitest";

import { annotations, HASH, imageryBody, sceneBody, sceneItem } from "./fixtures-imagery.test-data";
import { sceneGraticule, sceneStep } from "./graticule";
import {
  citationHref,
  cloudLabel,
  decodeImagery,
  decodeSceneDetail,
  downloads,
  fetchAreaScenes,
  fetchImagery,
  fetchScene,
  formatLat,
  formatLon,
  newlyPublished,
  tileTemplate,
} from "./imagery";

function scene() {
  const read = decodeSceneDetail(sceneBody());
  if (!("scene" in read)) throw new Error("expected a scene");
  return read.scene;
}

describe("the imagery listing", () => {
  it("decodes each scene with what its card needs", () => {
    const read = decodeImagery(imageryBody);
    if (!("scenes" in read)) throw new Error("expected scenes");
    expect(read.scenes[0]).toMatchObject({
      hash: HASH,
      areaLabel: "Bengaluru urban",
      sourceClass: "high_resolution",
      cloudFraction: 0.04,
      display: `/imagery/${HASH}/display.png`,
      simulated: false,
    });
  });

  it("says in words that nothing is published yet", () => {
    expect(decodeImagery({ status: "not_yet_computed", reason: "None yet." })).toEqual({ notYetComputed: "None yet." });
  });

  it("refuses a class it does not know rather than drawing it", () => {
    expect(() => decodeImagery({ status: "computed", items: [sceneItem({ source_class: "received" })] })).toThrow(
      "source_class",
    );
  });

  it("asks the platform's own origin, at the address the front keeps", async () => {
    const asked: string[] = [];
    const fetcher = (url: string) => {
      asked.push(url);
      return Promise.resolve(new Response(JSON.stringify(imageryBody)));
    };
    await fetchImagery(fetcher, new AbortController().signal);
    await fetchScene(
      (url) => {
        asked.push(url);
        return Promise.resolve(new Response(JSON.stringify(sceneBody())));
      },
      HASH,
      new AbortController().signal,
    );
    expect(asked).toEqual(["/api/v1/imagery", `/api/v1/imagery/${HASH}`]);
  });
});

describe("one scene", () => {
  it("numbers its markers from one, in the manifest's order, each with its citations", () => {
    const markers = scene().manifest.markers;
    expect(markers.map((one) => [one.number, one.kind])).toEqual([
      [1, "area"],
      [2, "fire"],
      [3, "place"],
    ]);
    expect(markers.every((one) => one.citations.length > 0)).toBe(true);
    expect(markers[0]?.geometry).toEqual({ type: "box", bbox: [77.58, 12.96, 77.6, 12.98] });
  });

  it("refuses a marker that cites nothing", () => {
    const body = sceneBody();
    const manifest = body.manifest as Record<string, unknown>;
    manifest.annotations = [{ ...annotations[1], citations: [] }];
    expect(() => decodeSceneDetail(body)).toThrow("at least one citation");
  });

  it("serves its tiles and files from its own base, same-origin", () => {
    const one = scene();
    expect(tileTemplate(one)).toBe(`/imagery/${HASH}/tiles/{z}/{x}/{y}.webp`);
    expect(downloads(one).map((file) => [file.path, file.url])).toEqual([
      ["scene.tif", `/imagery/${HASH}/scene.tif`],
      ["scene.png", `/imagery/${HASH}/scene.png`],
      ["scene.pgw", `/imagery/${HASH}/scene.pgw`],
      ["display.png", `/imagery/${HASH}/display.png`],
      ["manifest.json", `/imagery/${HASH}/manifest.json`],
    ]);
  });

  it("links a citation only to https or to the platform's own API", () => {
    const base = { sourceId: "x", recordId: "y", licence: "z" };
    expect(citationHref({ ...base, url: "https://www.geonames.org/1" })).toBe("https://www.geonames.org/1");
    expect(citationHref({ ...base, url: "/api/v1/areas" })).toBe("/api/v1/areas");
    expect(citationHref({ ...base, url: "javascript:alert(1)" })).toBeNull();
  });
});

describe("words for a scene", () => {
  it("says cloud as the source counted it, and when it counted none", () => {
    expect(cloudLabel(null)).toBe("No cloud share stated");
    expect(cloudLabel(0)).toBe("No cloud over the area");
    expect(cloudLabel(0.004)).toBe("Under 1% cloud over the area");
    expect(cloudLabel(0.37)).toBe("37% cloud over the area");
  });

  it("writes a coordinate with its hemisphere", () => {
    expect(formatLat(12.97123)).toBe("12.9712°N");
    expect(formatLat(-3.5, 1)).toBe("3.5°S");
    expect(formatLon(-0.12, 2)).toBe("0.12°W");
  });
});

describe("the scene's graticule", () => {
  // Each box's answer is what ingest's interval_hundredths and graticule give,
  // so the map's lines fall where the published picture's do (D-373).
  it("picks the step the published picture's graticule picks", () => {
    expect(sceneStep([77.58, 12.96, 77.6, 12.98])).toBe(1);
    expect(sceneStep([77.46, 12.83, 77.75, 13.14])).toBe(10);
    expect(sceneStep([76.0, 11.6, 77.2, 12.9])).toBe(50);
  });

  it("draws the lines the picture draws, and labels them the same", () => {
    const lines = sceneGraticule([77.46, 12.83, 77.75, 13.14]);
    expect(lines.map((one) => [one.axis, one.value])).toEqual([
      ["lat", 12.9],
      ["lat", 13],
      ["lat", 13.1],
      ["lon", 77.5],
      ["lon", 77.6],
      ["lon", 77.7],
    ]);
    expect(lines.map((one) => (one.axis === "lat" ? formatLat : formatLon)(one.value, one.decimals))).toEqual([
      "12.9°N",
      "13.0°N",
      "13.1°N",
      "77.5°E",
      "77.6°E",
      "77.7°E",
    ]);
  });

  it("keeps a line on the box's edge, as the picture does", () => {
    const lines = sceneGraticule([77.5804, 12.9601, 77.6003, 12.9799]);
    expect(lines.map((one) => one.value)).toEqual([12.97, 77.59, 77.6]);
  });
});

describe("an area's imagery", () => {
  it("asks for the latest scene of each class, and keeps the ones there are", async () => {
    const asked: string[] = [];
    const fetcher = (url: string) => {
      asked.push(url);
      const items = url.includes("daily_wide") ? [] : [sceneItem({ hash: url.includes("older") ? "b".repeat(12) : HASH })];
      return Promise.resolve(new Response(JSON.stringify({ status: "computed", items, next_cursor: null })));
    };
    const read = await fetchAreaScenes(fetcher, 3, new AbortController().signal);
    expect(asked).toEqual([
      "/api/v1/imagery?area=3&source_class=high_resolution&limit=1",
      "/api/v1/imagery?area=3&source_class=older_record&limit=1",
      "/api/v1/imagery?area=3&source_class=daily_wide&limit=1",
    ]);
    expect("scenes" in read && read.scenes.map((one) => one.hash)).toEqual([HASH, "b".repeat(12)]);
  });

  it("says nothing is published when the platform says so", async () => {
    const closed = { status: "not_yet_computed", reason: "No open imagery is published yet." };
    const read = await fetchAreaScenes(
      () => Promise.resolve(new Response(JSON.stringify(closed))),
      3,
      new AbortController().signal,
    );
    expect(read).toEqual({ notYetComputed: "No open imagery is published yet." });
  });
});

describe("newly published imagery", () => {
  it("keeps the last week's, the most recently published first", () => {
    const read = decodeImagery({
      status: "computed",
      items: [
        sceneItem({ hash: "a".repeat(12), published_at: "2026-11-15T06:00:00Z" }),
        sceneItem({ hash: "b".repeat(12), published_at: "2026-11-19T06:00:00Z" }),
        sceneItem({ hash: "c".repeat(12), published_at: "2026-11-01T06:00:00Z" }),
      ],
    });
    if (!("scenes" in read)) throw new Error("expected scenes");
    const fresh = newlyPublished(read.scenes, new Date("2026-11-20T06:00:00Z"));
    expect(fresh.map((one) => one.hash[0])).toEqual(["b", "a"]);
  });
});
