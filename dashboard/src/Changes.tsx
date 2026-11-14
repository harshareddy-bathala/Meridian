import { ChangeLine } from "./Areas";
import { fetchAreas } from "./areas";
import { fetchEventPage, type NetworkEvent } from "./events";
import { Feed } from "./Feed";
import { ReleaseNotes } from "./ReleaseNotes";
import type { Route } from "./route";
import { SnapshotNote } from "./SnapshotNote";
import { mergeOnce, useOlderPages, type LoadPage } from "./useOlderPages";
import { usePolled } from "./usePolled";
import { useStations } from "./useStations";

// What has changed: every change measured in an area, with its interval and the
// report it came from (D-231, D-326); the network's events, older ones on
// request (D-363); and every release (D-366). A change in an area is a
// report's, read from the summary file, not an event the database records.

type Go = (route: Route) => void;

function AreaChanges() {
  const read = usePolled("areas", fetchAreas, 300_000);
  const value = read.value;
  if (value === null) {
    return read.error === null ? <p className="dim">Loading the areas of interest…</p> : <p role="alert">{read.error}</p>;
  }
  if ("notYetComputed" in value) {
    return <p className="dim">{value.notYetComputed}</p>;
  }
  const labels = new Map(value.summary.areas.map((one) => [one.areaId, one.label]));
  if (value.summary.changes.length === 0) {
    return <p className="dim">The published report measured no change.</p>;
  }
  return (
    <>
      <ul>
        {value.summary.changes.map((one) => (
          <ChangeLine key={`${String(one.areaId)}:${one.quantity}`} change={one} label={labels.get(one.areaId) ?? null} />
        ))}
      </ul>
      <p className="dim">
        From the regional report <span className="mono">{value.summary.reportSha256.slice(0, 12)}</span>.
      </p>
    </>
  );
}

const olderEvents: LoadPage<NetworkEvent> = async (fetcher, cursor, signal) => {
  const page = await fetchEventPage(fetcher, cursor, signal);
  return { items: page.events, nextCursor: page.nextCursor };
};

const eventKey = (one: NetworkEvent) => `${one.kind}:${one.ref}`;

function useEventPages() {
  const newest = usePolled("events-page", (fetcher, signal) => fetchEventPage(fetcher, null, signal));
  const pages = useOlderPages(olderEvents, newest.value?.nextCursor ?? null);
  return { newest, ...pages, events: mergeOnce(newest.value?.events ?? [], pages.older, eventKey) };
}

function NetworkEvents({ names, go }: { names: Map<string, string>; go: Go }) {
  const { newest, events, hasMore, more, error } = useEventPages();
  return (
    <>
      <SnapshotNote snapshot={newest.snapshot} what="The events show" />
      {newest.error !== null && <p role="alert">{newest.error}</p>}
      {newest.value !== null && <Feed events={events} names={names} go={go} />}
      {error !== null && <p role="alert">{error}</p>}
      {hasMore && (
        <button type="button" onClick={more}>
          Show older events
        </button>
      )}
    </>
  );
}

export function ChangesView({ go }: { go: Go }) {
  const directory = useStations();
  const names = new Map((directory.stations ?? []).map((one) => [one.stationId, one.name]));
  return (
    <section aria-labelledby="changes-heading" className="view">
      <h2 id="changes-heading">Changes</h2>
      <h3>In the areas of interest</h3>
      <AreaChanges />
      <h3>On the network, over the last week</h3>
      <NetworkEvents names={names} go={go} />
      <h3>Releases</h3>
      <ReleaseNotes />
    </section>
  );
}
