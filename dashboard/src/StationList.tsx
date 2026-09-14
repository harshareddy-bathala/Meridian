import { formatAge, livenessLabel, precisionLabel } from "./format";
import type { Station } from "./stations";

function SimulatedBadge() {
  // Text, not only a colour or an icon: the label has to survive a screenshot,
  // a screen reader and a greyscale print (CLAUDE.md rule 5).
  return (
    <span className="badge badge-simulated" title="A virtual station run by the simulator">
      Simulated
    </span>
  );
}

export type Select = (stationId: string) => void;

interface RowProps {
  station: Station;
  now: Date;
  selected: boolean;
  onSelect: Select;
}

function StationRow({ station, now, selected, onSelect }: RowProps) {
  return (
    <tr className={selected ? "selected" : undefined}>
      <th scope="row">
        <button
          type="button"
          className="link"
          aria-pressed={selected}
          onClick={() => {
            onSelect(station.stationId);
          }}
        >
          {station.name}
        </button>{" "}
        {station.simulated && <SimulatedBadge />}
        <div className="dim mono">{station.stationId}</div>
      </th>
      <td>
        <span className={`liveness liveness-${station.liveness}`}>
          {livenessLabel(station.liveness)}
        </span>
      </td>
      <td title={station.lastHeartbeatAt ?? undefined}>
        {formatAge(station.lastHeartbeatAt, now)}
      </td>
      <td>{station.operator}</td>
      <td className="mono">
        {station.latDeg}, {station.lonDeg}
        <div className="dim">{precisionLabel(station.locationPrecisionDecimals)}</div>
      </td>
    </tr>
  );
}

interface ListProps {
  stations: Station[];
  now: Date;
  selectedId: string | null;
  onSelect: Select;
}

export function StationList({ stations, now, selectedId, onSelect }: ListProps) {
  if (stations.length === 0) {
    return <p>No station has registered yet.</p>;
  }
  const simulated = stations.filter((station) => station.simulated).length;
  return (
    <div className="table-scroll">
      <table>
        <caption>
          {stations.length} {stations.length === 1 ? "station" : "stations"}, {simulated}{" "}
          simulated
        </caption>
        <thead>
          <tr>
            <th scope="col">Station</th>
            <th scope="col">Liveness</th>
            <th scope="col">Last heartbeat</th>
            <th scope="col">Operator</th>
            <th scope="col">Published location</th>
          </tr>
        </thead>
        <tbody>
          {stations.map((station) => (
            <StationRow
              key={station.stationId}
              station={station}
              now={now}
              selected={station.stationId === selectedId}
              onSelect={onSelect}
            />
          ))}
        </tbody>
      </table>
    </div>
  );
}
