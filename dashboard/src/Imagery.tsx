import { formatAge, formatMinute } from "./format";
import {
  cloudLabel,
  fetchAreaScenes,
  fetchImagery,
  type ImageryRead,
  type ImageryScene,
  NEW_FOR_DAYS,
  newlyPublished,
  SIMULATED_KIND,
  sourceLine,
} from "./imagery";
import type { Route } from "./route";
import { Viewer } from "./SceneViewer";
import { SimBadge } from "./SimBadge";
import { SnapshotNote } from "./SnapshotNote";
import { snapshotClass } from "./snapshot";
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

function SceneGrid({ scenes, now }: { scenes: ImageryScene[]; now: Date }) {
  return (
    <ul className="scene-grid">
      {scenes.map((one) => (
        <SceneCard key={one.hash} scene={one} now={now} />
      ))}
    </ul>
  );
}

/** Simulated scenes, if any, in a section of their own: never among an archive's. */
function SimulatedScenes({ scenes, now, heading }: { scenes: ImageryScene[]; now: Date; heading: "h3" | "h5" }) {
  if (scenes.length === 0) return null;
  const Heading = heading;
  return (
    <>
      <Heading>Simulated, for the demonstration</Heading>
      <p className="dim">{SIMULATED_KIND}</p>
      <SceneGrid scenes={scenes} now={now} />
    </>
  );
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
        <dd>{sourceLine(scene.platform, scene.instrument, scene.sourceClass, scene.simulated)}</dd>
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
  const scenes = "scenes" in read ? read.scenes : [];
  const archive = scenes.filter((one) => !one.simulated);
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
      ) : archive.length === 0 ? (
        <p className="dim">No archive scene is published yet.</p>
      ) : (
        <SceneGrid scenes={archive} now={now} />
      )}
      <SimulatedScenes scenes={scenes.filter((one) => one.simulated)} now={now} heading="h3" />
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
  const archive = read.scenes.filter((one) => !one.simulated);
  return (
    <>
      {archive.length > 0 && (
        <>
          <p className="dim">Public archive imagery, credited: the latest scene from each archive.</p>
          <SceneGrid scenes={archive} now={now} />
        </>
      )}
      <SimulatedScenes scenes={read.scenes.filter((one) => one.simulated)} now={now} heading="h5" />
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
          , {sourceLine(one.platform, one.instrument, one.sourceClass, one.simulated)}, taken {formatMinute(one.acquiredAt)}: published{" "}
          {formatAge(one.publishedAt, now)}
        </li>
      ))}
    </ul>
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
