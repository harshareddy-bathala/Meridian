import { useState } from "react";

import { formatSize } from "./datasets";
import { formatMinute } from "./format";
import {
  ARCHIVE_KIND,
  citationHref,
  cloudLabel,
  downloads,
  fetchScene,
  type SceneDetail,
  type SceneMarker,
  type SceneRead,
  SIMULATED_KIND,
  sourceLine,
} from "./imagery";
import type { Route } from "./route";
import { SceneMap, type TileStatus } from "./SceneMap";
import { SimBadge } from "./SimBadge";
import { SnapshotNote } from "./SnapshotNote";
import { snapshotClass } from "./snapshot";
import { TableScroll } from "./TableScroll";
import { usePolled } from "./usePolled";

// One published scene (D-377): its picture and caption, its facts, a map of its
// own tiles with every marker as a layer, every marker in a table with the
// records it cites, and the files to keep. A simulated scene says so in place
// of the archive's words (rule 5, D-378).

/** A scene is published once and never rewritten; its listing, daily. */
const SCENE_REFRESH_MS = 300_000;

const KIND_LABEL: Record<SceneMarker["kind"], string> = {
  area: "Area",
  change: "Change",
  fire: "Fire",
  place: "Place",
};

function Citations({ marker }: { marker: SceneMarker }) {
  return (
    <ul className="citations">
      {marker.citations.map((one, index) => {
        const href = citationHref(one);
        return (
          <li key={index}>
            {href === null ? one.sourceId : <a href={href}>{one.sourceId}</a>}, {one.recordId}{" "}
            <span className="dim">({one.licence})</span>
          </li>
        );
      })}
    </ul>
  );
}

export function MarkerDetail({ marker }: { marker: SceneMarker | undefined }) {
  if (marker === undefined) {
    return <p className="dim">Choose a marker on the map, or read them all in the table below.</p>;
  }
  return (
    <div className="figure marker-detail" aria-live="polite">
      <h4>
        {marker.number}. {marker.label}
      </h4>
      <p>{marker.text}</p>
      <p>From:</p>
      <Citations marker={marker} />
    </div>
  );
}

export function MarkerTable({ markers }: { markers: SceneMarker[] }) {
  return (
    <TableScroll label="Every marker on the scene">
      <table>
        <caption>Every marker on the scene, numbered as on the picture, and the records it cites</caption>
        <thead>
          <tr>
            <th scope="col">No.</th>
            <th scope="col">Kind</th>
            <th scope="col">What it marks</th>
            <th scope="col">From</th>
          </tr>
        </thead>
        <tbody>
          {markers.map((one) => (
            <tr key={one.number}>
              <td>{one.number}</td>
              <td>{KIND_LABEL[one.kind]}</td>
              <td>{one.text}</td>
              <td>
                <Citations marker={one} />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

export function Downloads({ scene }: { scene: SceneDetail }) {
  return (
    <ul className="downloads">
      {downloads(scene).map((one) => (
        <li key={one.path}>
          <a href={one.url} download={`${scene.hash}-${one.path}`}>
            {one.path}
          </a>
          : {one.label}
          {one.sizeBytes !== null && <span className="dim"> ({formatSize(one.sizeBytes)})</span>}
        </li>
      ))}
    </ul>
  );
}

const TILE_FAILED =
  "The scene's map tiles could not be loaded, so the map shows its graticule and markers without the picture. The picture above and the downloads may still be there.";

export function SceneFacts({ scene, tiles }: { scene: SceneDetail; tiles: TileStatus }) {
  const manifest = scene.manifest;
  return (
    <>
      <p>{manifest.caption}</p>
      {tiles === "failed" && <p role="alert">{TILE_FAILED}</p>}
      <dl className="figure">
        <dt>Source</dt>
        <dd>{sourceLine(manifest.platform, manifest.instrument, manifest.sourceClass, scene.simulated)}</dd>
        <dt>Taken</dt>
        <dd>{formatMinute(manifest.acquiredAt)}</dd>
        <dt>Pixels</dt>
        <dd>{Math.round(manifest.resolutionM)} m, as the archive's own</dd>
        <dt>Cloud</dt>
        <dd>
          {cloudLabel(manifest.cloudFraction)}: {manifest.cloudMethod}
        </dd>
        <dt>Colour</dt>
        <dd>{manifest.rendering}</dd>
        <dt>Credit</dt>
        <dd>{manifest.credit}</dd>
        <dt>Licence</dt>
        <dd>{manifest.licence}</dd>
      </dl>
    </>
  );
}

export function Viewer({ hash, go }: { hash: string; go: (route: Route) => void }) {
  const scene = usePolled<SceneRead>(
    `scene-${hash}`,
    (fetcher, signal) => fetchScene(fetcher, hash, signal),
    SCENE_REFRESH_MS,
  );
  const [selected, setSelected] = useState<number | null>(null);
  const [tiles, setTiles] = useState<TileStatus>("loading");
  const read = scene.value;
  return (
    <section aria-labelledby="scene-heading" className={snapshotClass("view", scene.snapshot)}>
      <p>
        <a
          href="#/imagery"
          onClick={(event) => {
            event.preventDefault();
            go({ view: "imagery", stationId: null });
          }}
        >
          All imagery
        </a>
      </p>
      <SnapshotNote snapshot={scene.snapshot} what="This scene's description is" />
      {scene.error !== null && <p role="alert">{scene.error}</p>}
      {read === null ? (
        scene.error === null && <p className="dim">Loading the scene…</p>
      ) : "notYetComputed" in read ? (
        <p className="dim">{read.notYetComputed}</p>
      ) : (
        <SceneBody scene={read.scene} selected={selected} onSelect={setSelected} tiles={tiles} onTiles={setTiles} />
      )}
    </section>
  );
}

interface BodyProps {
  scene: SceneDetail;
  selected: number | null;
  onSelect: (marker: number) => void;
  tiles: TileStatus;
  onTiles: (status: TileStatus) => void;
}

export function SceneBody({ scene, selected, onSelect, tiles, onTiles }: BodyProps) {
  const manifest = scene.manifest;
  return (
    <>
      <h2 id="scene-heading">
        {manifest.areaLabel}, {formatMinute(manifest.acquiredAt)}
        {scene.simulated && (
          <>
            {" "}
            <SimBadge title="A scene rendered from simulated data" />
          </>
        )}
      </h2>
      <p className="dim">{scene.simulated ? SIMULATED_KIND : ARCHIVE_KIND}</p>
      <img
        className="scene-display"
        src={`${scene.base}display.png`}
        alt={manifest.caption}
        width={1200}
      />
      <SceneFacts scene={scene} tiles={tiles} />
      <h3>Explore it</h3>
      <SceneMap key={scene.hash} scene={scene} selected={selected} onSelect={onSelect} onTiles={onTiles} />
      <MarkerDetail marker={manifest.markers.find((one) => one.number === selected)} />
      <h3>Every marker</h3>
      <MarkerTable markers={manifest.markers} />
      <h3>Keep it</h3>
      <p>
        Each file is named by the scene&apos;s digest, and its sha256 is in the manifest. The GeoTIFF, and the PNG with
        its world file, open in the right place in a GIS tool.
      </p>
      <Downloads scene={scene} />
    </>
  );
}
