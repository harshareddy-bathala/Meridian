import { useEffect, useState } from "react";

import { fetchPlatformHealth, type PlatformHealth } from "./health";
import { InsightsView } from "./Insights";
import { Kiosk } from "./Kiosk";
import { LossesView } from "./Losses";
import { Nav } from "./Nav";
import { Live } from "./Live";
import type { Route } from "./route";
import { ScheduleView } from "./ScheduleTimeline";
import { StationDetail } from "./StationDetail";
import { StationList } from "./StationList";
import { snapshotClass } from "./snapshot";
import { SnapshotNote } from "./SnapshotNote";
import { StationMap } from "./StationMap";
import { useRoute } from "./useRoute";
import { useStations } from "./useStations";
import { WaysIn } from "./WaysIn";

type Load =
  | { kind: "loading" }
  | { kind: "loaded"; health: PlatformHealth }
  | { kind: "failed"; reason: string };

function usePlatformHealth(): Load {
  const [load, setLoad] = useState<Load>({ kind: "loading" });
  useEffect(() => {
    const controller = new AbortController();
    fetchPlatformHealth(controller.signal)
      .then((health) => {
        setLoad({ kind: "loaded", health });
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          setLoad({ kind: "failed", reason: error instanceof Error ? error.message : String(error) });
        }
      });
    return () => {
      controller.abort();
    };
  }, []);
  return load;
}

function PlatformStatus({ load }: { load: Load }) {
  switch (load.kind) {
    case "loading":
      return <p className="dim">Contacting the platform…</p>;
    case "failed":
      return <p role="alert">The platform could not be reached: {load.reason}</p>;
    case "loaded":
      return (
        <p className="dim">
          Platform {load.health.status} · version {load.health.version} · database{" "}
          {load.health.database}
        </p>
      );
  }
}

function Stations({ selectedId, go }: { selectedId: string | null; go: (route: Route) => void }) {
  const { stations, fetchedAt, snapshot, error } = useStations();
  const select = (stationId: string | null) => {
    go({ view: "stations", stationId });
  };
  const selected = stations?.find((station) => station.stationId === selectedId) ?? null;
  return (
    <>
      <section aria-labelledby="stations-heading" className={snapshotClass("", snapshot)}>
        <h2 id="stations-heading">Stations</h2>
        <SnapshotNote snapshot={snapshot} what="The station list and map show">
          {" "}
          Liveness and heartbeat ages are as of then.
        </SnapshotNote>
        {error !== null && (
          <p role="alert">
            The station directory could not be refreshed: {error}
            {fetchedAt !== null && ` Showing the list as of ${fetchedAt.toLocaleTimeString()}.`}
          </p>
        )}
        {stations === null || fetchedAt === null ? (
          error === null && <p className="dim">Loading the station directory…</p>
        ) : (
          <>
            <StationMap stations={stations} onSelect={select} />
            <StationList
              stations={stations}
              now={fetchedAt}
              selectedId={selectedId}
              onSelect={select}
            />
          </>
        )}
      </section>
      <StationDetail
        station={selected}
        onClear={() => {
          select(null);
        }}
      />
    </>
  );
}

function View({ route, go }: { route: Route; go: (route: Route) => void }) {
  switch (route.view) {
    case "live":
      return <Live go={go} />;
    case "stations":
      return <Stations selectedId={route.stationId} go={go} />;
    case "schedule":
      return <ScheduleView stationId={route.stationId} go={go} />;
    case "losses":
      return <LossesView go={go} />;
    case "insights":
      return <InsightsView />;
    case "kiosk":
      return <Kiosk go={go} />;
  }
}

export function App() {
  const load = usePlatformHealth();
  const [route, go] = useRoute();
  return (
    <main className={route.view === "kiosk" ? "kiosk-main" : undefined}>
      <header>
        <h1>Meridian</h1>
        <PlatformStatus load={load} />
        <Nav route={route} />
        {route.view !== "kiosk" && <WaysIn />}
      </header>
      <View route={route} go={go} />
    </main>
  );
}
