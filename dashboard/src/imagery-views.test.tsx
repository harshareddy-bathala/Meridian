// The imagery pages, drawn to static markup as views.test.tsx draws the others:
// two kinds of picture never mixed, a simulated scene badged and an archive's
// not (rule 5), every marker listed with its sources as text, and the words
// for a map without its tiles (D-377, D-378).

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { imageryBody, sceneBody, sceneItem } from "./fixtures-imagery.test-data";
import { AreaScenes, Gallery, NewImagery } from "./Imagery";
import { MarkerTable, SceneBody } from "./SceneViewer";
import { decodeImagery, decodeSceneDetail, type SceneDetail } from "./imagery";
import liveSource from "./Live.tsx?raw";
import liveFiguresSource from "./LiveFigures.tsx?raw";
import liveNowSource from "./LiveNow.tsx?raw";

// Leaflet reads `window` when it is imported, and these run with no DOM.
vi.mock("./SceneMap", () => ({ SceneMap: () => null }));

const BADGE = 'class="badge badge-simulated"';
const go = () => undefined;

function badges(markup: string): number {
  return markup.split(BADGE).length - 1;
}

const imageryNow = new Date("2026-11-20T06:00:00Z");

function scene(body: Record<string, unknown> = sceneBody()): SceneDetail {
  const read = decodeSceneDetail(body);
  if (!("scene" in read)) throw new Error("expected a scene");
  return read.scene;
}

describe("the imagery gallery", () => {
  const now = imageryNow;

  it("keeps what Meridian received apart from the archive's, and says it has received none", () => {
    const markup = renderToStaticMarkup(<Gallery read={decodeImagery(imageryBody)} now={now} />);
    const received = markup.indexOf("Received by Meridian");
    const archive = markup.indexOf("Public archive imagery, credited");
    const card = markup.indexOf('class="scene-card"');
    expect(received).toBeGreaterThan(-1);
    expect(archive).toBeGreaterThan(received);
    expect(card).toBeGreaterThan(archive);
    expect(markup.slice(received, archive)).toContain("have received no image yet");
    expect(markup.slice(received, archive)).not.toContain("<img");
  });

  it("draws each card with its caption as alt text, its source, cloud and age", () => {
    const markup = renderToStaticMarkup(<Gallery read={decodeImagery(imageryBody)} now={now} />);
    expect(markup).toContain('alt="High-resolution satellite image of Bengaluru urban."');
    expect(markup).toContain('src="/imagery/0123456789ab/display.png"');
    expect(markup).toContain('href="#/imagery/0123456789ab"');
    expect(markup).toContain("Sentinel-2B MSI, sharpest recent view");
    expect(markup).toContain("4% cloud over the area");
    expect(markup).toContain("2 d ago");
  });

  it("badges no archive scene, and badges one the platform says is simulated", () => {
    const archive = renderToStaticMarkup(<Gallery read={decodeImagery(imageryBody)} now={now} />);
    const simulated = renderToStaticMarkup(
      <Gallery read={decodeImagery({ status: "computed", items: [sceneItem({ simulated: true })] })} now={now} />,
    );
    expect(badges(archive)).toBe(0);
    expect(badges(simulated)).toBe(1);
    const viewer = renderToStaticMarkup(
      <SceneBody scene={scene()} selected={null} onSelect={go} tiles="loaded" onTiles={go} />,
    );
    expect(badges(viewer)).toBe(0);
  });

});

describe("one scene's viewer", () => {
  it("lists every marker with the records it cites, as text, never as markup", () => {
    const markers = scene().manifest.markers;
    const markup = renderToStaticMarkup(<MarkerTable markers={markers} />);
    for (const marker of markers) {
      expect(markup).toContain(`<td>${String(marker.number)}</td>`);
      for (const citation of marker.citations) {
        expect(markup).toContain(citation.recordId);
      }
    }
    expect(markup).toContain("Aranpur &lt;b&gt;");
    expect(markup).not.toContain("Aranpur <b>");
    expect(markup).toContain('href="https://www.geonames.org/9100001"');
  });

  it("says in words when the scene's tiles could not be loaded", () => {
    const failed = renderToStaticMarkup(
      <SceneBody scene={scene()} selected={null} onSelect={go} tiles="failed" onTiles={go} />,
    );
    const loaded = renderToStaticMarkup(
      <SceneBody scene={scene()} selected={null} onSelect={go} tiles="loaded" onTiles={go} />,
    );
    expect(failed).toContain("map tiles could not be loaded");
    expect(loaded).not.toContain("map tiles could not be loaded");
  });

  it("offers every file to keep, under the scene's name, and the chosen marker's sources", () => {
    const markup = renderToStaticMarkup(
      <SceneBody scene={scene()} selected={2} onSelect={go} tiles="loaded" onTiles={go} />,
    );
    for (const path of ["scene.tif", "scene.png", "scene.pgw", "display.png", "manifest.json"]) {
      expect(markup).toContain(`download="0123456789ab-${path}"`);
    }
    expect(markup).toContain("2. Fire");
    expect(markup).toContain("0123456789ab row 1");
  });
});

describe("imagery on the other pages", () => {
  it("shows an area its latest scenes, each a card linked to the viewer", () => {
    const markup = renderToStaticMarkup(<AreaScenes read={decodeImagery(imageryBody)} now={imageryNow} />);
    expect(markup).toContain('href="#/imagery/0123456789ab"');
    expect(markup).toContain("Public archive imagery, credited");
  });

  it("lists this week's scenes on the changes page, badging a simulated one", () => {
    const read = decodeImagery({
      status: "computed",
      items: [sceneItem({ simulated: true, published_at: "2026-11-19T06:00:00Z" })],
    });
    const markup = renderToStaticMarkup(<NewImagery read={read} now={imageryNow} />);
    expect(markup).toContain('href="#/imagery/0123456789ab"');
    expect(markup).toContain("published 1 d ago");
    expect(badges(markup)).toBe(1);
  });

  it("leaves the live page without imagery: it is about the network now", () => {
    for (const source of [liveSource, liveNowSource, liveFiguresSource]) {
      expect(source).not.toMatch(/from "\.\/(imagery|Imagery)"/);
    }
  });
});

describe("a simulated scene", () => {
  const simulated = decodeImagery({ status: "computed", items: [sceneItem({ simulated: true })] });

  it("has a section of its own in the gallery, never the archive's", () => {
    const markup = renderToStaticMarkup(<Gallery read={simulated} now={imageryNow} />);
    const archive = markup.indexOf("Public archive imagery, credited");
    const own = markup.indexOf("Simulated, for the demonstration");
    const card = markup.indexOf('class="scene-card"');
    expect(own).toBeGreaterThan(archive);
    expect(card).toBeGreaterThan(own);
    expect(markup.slice(archive, own)).toContain("No archive scene is published yet.");
  });

  it("is worded as synthetic wherever a source would be named", () => {
    const viewer = renderToStaticMarkup(
      <SceneBody
        scene={scene(sceneBody({ simulated: true }))}
        selected={null}
        onSelect={go}
        tiles="loaded"
        onTiles={go}
      />,
    );
    const changes = renderToStaticMarkup(
      <NewImagery
        read={decodeImagery({
          status: "computed",
          items: [sceneItem({ simulated: true, published_at: "2026-11-19T06:00:00Z" })],
        })}
        now={imageryNow}
      />,
    );
    for (const markup of [viewer, changes]) {
      expect(markup).toContain("Synthetic pixels for the demonstration, not a satellite&#x27;s");
      expect(markup).not.toContain("Sentinel-2B MSI");
    }
    expect(viewer).not.toContain("Public archive imagery, credited.");
    expect(renderToStaticMarkup(<AreaScenes read={simulated} now={imageryNow} />)).not.toContain(
      "Public archive imagery",
    );
  });
});
