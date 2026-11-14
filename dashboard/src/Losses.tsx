import { formatWindow } from "./format";
import { causeWords, describeFinding, fetchLosses, type Loss, whyUndetermined } from "./losses";
import type { Route } from "./route";
import { SimBadge } from "./SimBadge";
import { SnapshotNote } from "./SnapshotNote";
import { snapshotClass } from "./snapshot";
import { usePolled } from "./usePolled";
import { TableScroll } from "./TableScroll";

// Every lost pass the platform diagnosed: the cause, or *undetermined* said
// plainly with the reason, and every cause tested beside it, fired or not,
// with what its test found (D-273). A loss is diagnosed once its pass has
// settled, so the newest passes are not here yet.

type Go = (route: Route) => void;

function Tested({ loss }: { loss: Loss }) {
  return (
    <ul className="tested">
      {loss.tested.map((one) => (
        <li key={one.cause} className={one.fired ? "fired" : undefined}>
          {one.fired ? "Fired" : "Not fired"}: {causeWords(one.cause)}
          {one.fired && ` (support ${one.support.toFixed(2)})`}
          {Object.keys(one.findings).length > 0 && (
            <span className="dim">
              {" "}
              — {Object.entries(one.findings).map(([key, value]) => describeFinding(key, value)).join(", ")}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

export function LossRow({ loss, go }: { loss: Loss; go: Go }) {
  const undetermined = whyUndetermined(loss);
  return (
    <tr>
      <td>{formatWindow(loss.windowStart, loss.windowEnd)}</td>
      <td>
        <button
          type="button"
          className="link mono"
          onClick={() => {
            go({ view: "stations", stationId: loss.stationId });
          }}
        >
          {loss.stationId}
        </button>{" "}
        {loss.simulated && <SimBadge />}
      </td>
      <td className="mono">{loss.satelliteId}</td>
      <td>
        <strong>{loss.cause === "undetermined" ? "Undetermined" : causeWords(loss.cause)}</strong>
        {undetermined !== null && <p className="dim">{undetermined}</p>}
        <Tested loss={loss} />
      </td>
    </tr>
  );
}

/** The list, a part of the results page since Stage 37 (D-365). */
export function LossesSection({ go }: { go: Go }) {
  const losses = usePolled("losses", (fetcher, signal) => fetchLosses(fetcher, null, signal));
  return (
    <section aria-labelledby="losses-heading" className={snapshotClass("", losses.snapshot)}>
      <h3 id="losses-heading">Losses</h3>
      <SnapshotNote snapshot={losses.snapshot} what="This list shows" />
      {losses.error !== null && <p role="alert">{losses.error}</p>}
      {losses.value === null ? (
        losses.error === null && <p className="dim">Loading the diagnosed losses…</p>
      ) : losses.value.length === 0 ? (
        <p className="dim">
          No loss has been diagnosed. A pass is diagnosed once it has settled, so a network that has just started
          has none yet.
        </p>
      ) : (
        <TableScroll label="Diagnosed losses">
          <table>
            <caption>The most recently diagnosed first, with every cause tested.</caption>
            <thead>
              <tr>
                <th scope="col">Window (UTC)</th>
                <th scope="col">Station</th>
                <th scope="col">Satellite</th>
                <th scope="col">Cause</th>
              </tr>
            </thead>
            <tbody>
              {losses.value.map((one) => (
                <LossRow key={one.diagnosisId} loss={one} go={go} />
              ))}
            </tbody>
          </table>
        </TableScroll>
      )}
    </section>
  );
}
