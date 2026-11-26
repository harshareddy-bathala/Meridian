import { useState } from "react";

import { formatSize } from "./datasets";
import { formatAge, formatMinute } from "./format";
import {
  CLASS_LABEL,
  citationHref,
  cloudLabel,
  downloads,
  fetchAreaScenes,
  fetchImagery,
  fetchScene,
  type ImageryRead,
  type ImageryScene,
  type SceneDetail,
  type SceneMarker,
  type SceneRead,
  NEW_FOR_DAYS,
  newlyPublished,
} from "./imagery";
import type { Route } from "./route";
import { SceneMap, type TileStatus } from "./SceneMap";
import { SimBadge } from "./SimBadge";
import { SnapshotNote } from "./SnapshotNote";
import { snapshotClass } from "./snapshot";
import { TableScroll } from "./TableScroll";
import { useNow } from "./useNow";
import { usePolled } from "./usePolled";

// Pictures of the published areas (D-377). Two kinds are kept apart and never
// mixed: what Meridian's own stations received, of which there is none yet
// (Stage 40), and public archive imagery, credited. Each scene is opened in a
// viewer: its own tiles under the dashboard's graticule, every marker as a
// layer and in a table, each with the records it cites, and the files to keep.
//
// A scene is the platform's: the page reads what /api/v1/imagery says and
// draws it, and a marker's place, words and sources come from the manifest.

/** Scenes are published once a day. */
const IMAGERY_REFRESH_MS = 300_000;

const KIND_LABEL: Record<SceneMarker["kind"], string> = {
  area: "Area",
  change: "Change",
  fire: "Fire",
  place: "Place",
};

/** The scene's source, as a card names it: "Sentinel-2B MSI, sharpest recent view". */
function sourceLine(platform: string, instrument: string, sourceClass: ImageryScene["sourceClass"]): string {
  return `${platform} ${instrument}, ${CLASS_LABEL[sourceClass].toLowerCase()}`;
}

export function SceneCard({ scene, now }: { scene: ImageryScene; now: Date }) {
  return (
    <li className="scene-card">
      <a href={`#/imagery/${encodeURIComponent(scene.hash)}`}>
        <img src={scene.display} alt={scene.caption} loading="lazy" width={300} />
      </a>
      <h4>
        <a href={`#/imagery/${encodeURIComponent(scene.hash)}`}>{scene.areaLabel}</a>
        {scene.simulated && (
          <>
            {" "}
            <SimBadge title="A scene rendered from simulated data" />
          </>
        )}
      </h4>
      <dl>
        <dt>Source</dt>
        <dd>{sourceLine(scene.platform, scene.instrument, scene.sourceClass)}</dd>
        <dt>Taken</dt>
        <dd>{formatMinute(scene.acquiredAt)}</dd>
        <dt>Cloud</dt>
        <dd>{cloudLabel(scene.cloudFraction)}</dd>
        <dt>Published</dt>
        <dd>{formatAge(scene.publishedAt, now)}</dd>
      </dl>
      <p className="dim credit">{scene.credit}</p>
    </li>
  );
}

export function Gallery({ read, now }: { read: ImageryRead; now: Date }) {
  return (
    <>
      <h3>Received by Meridian</h3>
      <p className="dim">
        Meridian&apos;s own stations have received no image yet. Pictures they receive will be shown here, apart from
        the archive&apos;s.
      </p>
      <h3>Public archive imagery, credited</h3>
      <p>
        The clearest recent scene of each published area from three open archives: Sentinel-2 at 10 m, Landsat at 30 m,
        and a daily wide view. Each is credited as its archive asks, and every mark on it cites the record it came from.
      </p>
      {"notYetComputed" in read ? (
        <p className="dim">{read.notYetComputed}</p>
      ) : read.scenes.length === 0 ? (
        <p className="dim">No scene is published yet.</p>
      ) : (
        <ul className="scene-grid">
          {read.scenes.map((one) => (
            <SceneCard key={one.hash} scene={one} now={now} />
          ))}
        </ul>
      )}
    </>
  );
}

/** An area's latest scene of each class, on the area's own page (D-378). */
export function AreaScenes({ read, now }: { read: ImageryRead; now: Date }) {
  if ("notYetComputed" in read) {
    return <p className="dim">{read.notYetComputed}</p>;
  }
  if (read.scenes.length === 0) {
    return <p className="dim">No scene of this area is published yet.</p>;
  }
  return (
    <>
      <p className="dim">Public archive imagery, credited: the latest scene from each archive.</p>
      <ul className="scene-grid">
        {read.scenes.map((one) => (
          <SceneCard key={one.hash} scene={one} now={now} />
        ))}
      </ul>
    </>
  );
}

/** Scenes published in the last week, for the changes page. Not an event:
 * events are the database's records, and a scene is a published file (D-363). */
export function NewImagery({ read, now }: { read: ImageryRead; now: Date }) {
  if ("notYetComputed" in read) {
    return <p className="dim">{read.notYetComputed}</p>;
  }
  const fresh = newlyPublished(read.scenes, now);
  if (fresh.length === 0) {
    return <p className="dim">No scene was published in the last {NEW_FOR_DAYS} days.</p>;
  }
  return (
    <ul>
      {fresh.map((one) => (
        <li key={one.hash}>
          <a href={`#/imagery/${encodeURIComponent(one.hash)}`}>{one.areaLabel}</a>
          {one.simulated && (
            <>
              {" "}
              <SimBadge title="A scene rendered from simulated data" />
            </>
          )}
          , {sourceLine(one.platform, one.instrument, one.sourceClass)}, taken {formatMinute(one.acquiredAt)}: published{" "}
          {formatAge(one.publishedAt, now)}
        </li>
      ))}
    </ul>
  );
}

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
        <dd>{sourceLine(manifest.platform, manifest.instrument, manifest.sourceClass)}</dd>
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

function Viewer({ hash, go }: { hash: string; go: (route: Route) => void }) {
  const scene = usePolled<SceneRead>(
    `scene-${hash}`,
    (fetcher, signal) => fetchScene(fetcher, hash, signal),
    IMAGERY_REFRESH_MS,
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
      <p className="dim">Public archive imagery, credited.</p>
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

function GalleryView() {
  const imagery = usePolled("imagery", fetchImagery, IMAGERY_REFRESH_MS);
  const now = useNow(60_000);
  return (
    <section aria-labelledby="imagery-heading" className={snapshotClass("view", imagery.snapshot)}>
      <h2 id="imagery-heading">Imagery</h2>
      <SnapshotNote snapshot={imagery.snapshot} what="This list shows">
        {" "}
        How long ago each scene was published is said beside it.
      </SnapshotNote>
      {imagery.error !== null && <p role="alert">{imagery.error}</p>}
      {imagery.value === null ? (
        imagery.error === null && <p className="dim">Loading the published imagery…</p>
      ) : (
        <Gallery read={imagery.value} now={now} />
      )}
    </section>
  );
}

/** An area's latest scenes, read for the area page. */
export function AreaImagery({ areaId }: { areaId: number }) {
  const read = usePolled(
    `area-imagery-${String(areaId)}`,
    (fetcher, signal) => fetchAreaScenes(fetcher, areaId, signal),
    IMAGERY_REFRESH_MS,
  );
  const now = useNow(60_000);
  if (read.value === null) {
    return read.error === null ? <p className="dim">Loading the area&apos;s imagery…</p> : <p role="alert">{read.error}</p>;
  }
  return <AreaScenes read={read.value} now={now} />;
}

/** Scenes published this week, read for the changes page. */
export function ImageryChanges() {
  const read = usePolled("imagery", fetchImagery, IMAGERY_REFRESH_MS);
  const now = useNow(60_000);
  if (read.value === null) {
    return read.error === null ? <p className="dim">Loading the published imagery…</p> : <p role="alert">{read.error}</p>;
  }
  return <NewImagery read={read.value} now={now} />;
}

export function ImageryView({ hash, go }: { hash: string | null; go: (route: Route) => void }) {
  return hash === null ? <GalleryView /> : <Viewer key={hash} hash={hash} go={go} />;
}
