import { useCallback, useEffect, useState } from "react";

// The selected station lives in the URL fragment, `#/stations/<id>`, so a view
// of one station is a link that can be shared. A fragment never reaches the
// server, which is how D-091 gives the dashboard views without a catch-all route.

const PREFIX = "#/stations/";

function readHash(): string | null {
  const { hash } = window.location;
  if (!hash.startsWith(PREFIX)) {
    return null;
  }
  const id = decodeURIComponent(hash.slice(PREFIX.length));
  return id === "" ? null : id;
}

export function useHashSelection(): [string | null, (stationId: string | null) => void] {
  const [selectedId, setSelectedId] = useState<string | null>(readHash);

  useEffect(() => {
    const follow = () => {
      setSelectedId(readHash());
    };
    window.addEventListener("hashchange", follow);
    return () => {
      window.removeEventListener("hashchange", follow);
    };
  }, []);

  const select = useCallback((stationId: string | null) => {
    window.location.hash = stationId === null ? "" : `${PREFIX}${encodeURIComponent(stationId)}`;
    setSelectedId(stationId);
  }, []);

  return [selectedId, select];
}
