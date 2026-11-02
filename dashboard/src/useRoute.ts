import { useCallback, useEffect, useState } from "react";

import { formatRoute, parseRoute, type Route } from "./route";

/** The route in the URL fragment, followed as it changes, and a way to change it. */
export function useRoute(): [Route, (route: Route) => void] {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));

  useEffect(() => {
    const follow = () => {
      setRoute(parseRoute(window.location.hash));
    };
    window.addEventListener("hashchange", follow);
    return () => {
      window.removeEventListener("hashchange", follow);
    };
  }, []);

  const go = useCallback((next: Route) => {
    window.location.hash = formatRoute(next);
    setRoute(next);
  }, []);

  return [route, go];
}
