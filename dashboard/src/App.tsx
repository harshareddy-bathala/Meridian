import { useEffect, useState } from "react";

import { fetchPlatformHealth, type PlatformHealth } from "./health";

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
      return <p>Contacting the platform…</p>;
    case "failed":
      return <p role="alert">The platform could not be reached: {load.reason}</p>;
    case "loaded":
      return (
        <dl>
          <dt>Platform</dt>
          <dd>{load.health.status}</dd>
          <dt>Version</dt>
          <dd>{load.health.version}</dd>
          <dt>Database</dt>
          <dd>{load.health.database}</dd>
        </dl>
      );
  }
}

export function App() {
  const load = usePlatformHealth();
  return (
    <main>
      <h1>Meridian</h1>
      <PlatformStatus load={load} />
    </main>
  );
}
