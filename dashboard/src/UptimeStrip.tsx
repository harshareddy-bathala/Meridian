import { averageCoverage, uptimeBars, type StationUptime } from "./uptime";

const WIDTH = 240;
const HEIGHT = 24;

/** The last 48 hours of heartbeats, one bar an hour, and the average beside it. */
export function UptimeStrip({ uptime }: { uptime: StationUptime }) {
  const average = Math.round(averageCoverage(uptime.hours) * 100);
  return (
    <figure className="uptime">
      <svg
        viewBox={`0 0 ${String(WIDTH)} ${String(HEIGHT)}`}
        role="img"
        aria-label={`Heartbeats received each hour over the last ${String(uptime.hours.length)} hours`}
      >
        {uptimeBars(uptime.hours, WIDTH, HEIGHT).map((bar, index) => (
          <rect key={index} className="uptime-bar" x={bar.x} y={bar.y} width={bar.width} height={bar.height} />
        ))}
      </svg>
      <figcaption>
        {uptime.simulated && <span className="badge badge-simulated">simulated</span>} Heard {average}% of the
        last {uptime.hours.length} hours, by heartbeat.
      </figcaption>
    </figure>
  );
}
