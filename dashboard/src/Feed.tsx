// What just happened across the network, newest first (D-363).

import { describeEvent, type NetworkEvent } from "./events";
import { formatMinute } from "./format";
import type { Route } from "./route";
import { SimBadge } from "./SimBadge";

interface FeedProps {
  events: NetworkEvent[];
  names: Map<string, string>;
  go: (route: Route) => void;
}

export function Feed({ events, names, go }: FeedProps) {
  if (events.length === 0) {
    return <p className="dim">Nothing was recorded in the last week.</p>;
  }
  return (
    <ol className="feed">
      {events.map((one) => (
        <li key={`${one.kind}:${one.ref}`}>
          <time dateTime={one.at} className="dim">
            {formatMinute(one.at)}
          </time>{" "}
          <button
            type="button"
            className="link"
            onClick={() => {
              go({ view: "stations", stationId: one.stationId });
            }}
          >
            {names.get(one.stationId) ?? one.stationId}
          </button>{" "}
          {describeEvent(one)} {one.simulated && <SimBadge />}
        </li>
      ))}
    </ol>
  );
}
