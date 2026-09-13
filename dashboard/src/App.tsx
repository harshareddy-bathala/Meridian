import { useEffect, useState } from "react";

import { fetchPlatformHealth, type PlatformHealth } from "./health";
import { StationList } from "./StationList";
import { useStations } from "./useStations";

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
          setLoad({ kind: "failed", reason: String(error) });
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

function Stations() {
  const { stations, fetchedAt, error } = useStations();
  return (
    <section aria-labelledby="stations-heading">
      <h2 id="stations-heading">Stations</h2>
      {error !== null && (
        <p role="alert">
          The station directory could not be refreshed: {error}
          {fetchedAt !== null && ` Showing the list as of ${fetchedAt.toLocaleTimeString()}.`}
        </p>
      )}
      {stations === null || fetchedAt === null ? (
        error === null && <p className="dim">Loading the station directory…</p>
      ) : (
        <StationList stations={stations} now={fetchedAt} />
      )}
    </section>
  );
}

export function App() {
  const load = usePlatformHealth();
  return (
    <main>
      <header>
        <h1>Meridian</h1>
        <PlatformStatus load={load} />
      </header>
      <Stations />
    </main>
  );
}
