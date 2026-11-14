import attribution from "../../ATTRIBUTION.md?raw";

import { type ApiReference, fetchApiReference } from "./apiReference";
import { parseCredits } from "./credits";
import {
  checksumLines,
  type DatasetsRead,
  type EvidencePackage,
  fetchDatasets,
  fileUrl,
  formatSize,
  hasSimulated,
} from "./datasets";
import { formatDay } from "./format";
import { Parts } from "./InlineParts";
import { SimBadge } from "./SimBadge";
import { SnapshotNote } from "./SnapshotNote";
import { oldest, snapshotClass } from "./snapshot";
import { usePolled } from "./usePolled";
import { TableScroll } from "./TableScroll";

// The data, for a reader who wants to take it away (D-369): the evidence
// packages with their files and digests, the public API's routes, how to cite
// both, and the credit and licence of everything Meridian is built from. The
// packages and the routes are the platform's; the credits are the
// ATTRIBUTION.md this build shipped with. A simulated population is badged as
// well as said (rule 5).

const CREDITS = parseCredits(attribution);

export const REPOSITORY_URL = "https://github.com/harshareddy-bathala/Meridian";
const CC_BY_URL = "https://creativecommons.org/licenses/by/4.0/";

/** A package is published by hand, and the routes change with a release. */
const DATA_REFRESH_MS = 300_000;

function Populations({ pkg }: { pkg: EvidencePackage }) {
  return (
    <ul>
      {pkg.populations.map((one) => (
        <li key={one.population}>
          {one.receptions} {one.simulated ? "simulated" : "measured"} receptions, in <code>{one.file}</code>
          {one.simulated && (
            <>
              {" "}
              <SimBadge title="Receptions made by virtual stations" />
            </>
          )}
        </li>
      ))}
    </ul>
  );
}

function Files({ pkg }: { pkg: EvidencePackage }) {
  return (
    <TableScroll label="The package's files">
      <table>
        <caption>Every file of the package, with the digest a download must have</caption>
        <thead>
          <tr>
            <th scope="col">File and its sha256</th>
            <th scope="col">Size</th>
          </tr>
        </thead>
        <tbody>
          {pkg.files.map((one) => (
            <tr key={one.name}>
              <td>
                <a href={fileUrl(pkg, one)} download={`${pkg.name}-${one.name}`}>
                  {one.name}
                </a>
                <br />
                <span className="mono digest">{one.sha256}</span>
              </td>
              <td>{formatSize(one.sizeBytes)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

export function PackageCard({ pkg }: { pkg: EvidencePackage }) {
  return (
    <div className="figure package">
      <h4>
        Package <span className="mono">{pkg.name}</span>
        {hasSimulated(pkg) && (
          <>
            {" "}
            <SimBadge title="This package holds receptions made by virtual stations" />
          </>
        )}
      </h4>
      <p>
        Receptions from {formatDay(pkg.since)} to {formatDay(pkg.asOf)}. Every time is to the minute and every angle to
        the degree, and no SNR or Doppler series is included, so no station can be located from it more finely than it
        chose to publish.
      </p>
      <Populations pkg={pkg} />
      <Files pkg={pkg} />
      <p>
        Free to share and adapt under <a href={CC_BY_URL}>CC BY 4.0</a>. Cite it as:
      </p>
      <p>
        <code className="citation">{pkg.citation}</code>
      </p>
      <details>
        <summary>Check a download</summary>
        <p>
          Keep each file under the name its link gives it, then check them all with <code>sha256sum -c</code> and these
          lines:
        </p>
        <pre className="checksums">{checksumLines(pkg)}</pre>
        <p>
          Built by transformation {pkg.transformation} from snapshot{" "}
          <span className="mono">{pkg.snapshotSha256.slice(0, 12)}</span> under configuration{" "}
          <span className="mono">{pkg.configSha256.slice(0, 12)}</span>, as its manifest and datasheet state. Whoever
          holds that snapshot can rebuild the package and compare its hash with <code>meridian dataset verify</code>.
        </p>
      </details>
    </div>
  );
}

export function Packages({ read }: { read: DatasetsRead }) {
  if ("notYetComputed" in read) {
    return <p className="dim">{read.notYetComputed}</p>;
  }
  if (read.packages.length === 0) {
    return <p className="dim">The published index lists no package.</p>;
  }
  return (
    <>
      {read.packages.map((one) => (
        <PackageCard key={one.name} pkg={one} />
      ))}
    </>
  );
}

function Routes({ reference }: { reference: ApiReference }) {
  return (
    <TableScroll label="The API's routes">
      <table>
        <caption>Every public route of version {reference.version}, each a GET</caption>
        <thead>
          <tr>
            <th scope="col">Route</th>
            <th scope="col">What it answers</th>
          </tr>
        </thead>
        <tbody>
          {reference.routes.map((one) => (
            <tr key={one.path}>
              <td>
                <code>{one.path}</code>
              </td>
              <td>{one.description === "" ? one.summary : one.description}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TableScroll>
  );
}

function Credits() {
  return (
    <>
      <TableScroll label="Data sources and their terms">
        <table>
          <caption>Data Meridian takes from others, and the terms it comes under</caption>
          <thead>
            <tr>
              <th scope="col">Source</th>
              <th scope="col">Used for</th>
              <th scope="col">Licence and terms</th>
            </tr>
          </thead>
          <tbody>
            {CREDITS.sources.map((one, index) => (
              <tr key={index}>
                <td>
                  <Parts parts={one.source} />
                </td>
                <td>
                  <Parts parts={one.usedFor} />
                </td>
                <td>
                  <Parts parts={one.terms} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
      <TableScroll label="Software Meridian is built on">
        <table>
          <caption>Software Meridian is built on</caption>
          <thead>
            <tr>
              <th scope="col">Library</th>
              <th scope="col">Licence</th>
              <th scope="col">Used for</th>
            </tr>
          </thead>
          <tbody>
            {CREDITS.libraries.map((one, index) => (
              <tr key={index}>
                <td>
                  <Parts parts={one.name} />
                </td>
                <td>
                  <Parts parts={one.licence} />
                </td>
                <td>
                  <Parts parts={one.usedFor} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </TableScroll>
    </>
  );
}

export function DataView() {
  const datasets = usePolled("datasets", fetchDatasets, DATA_REFRESH_MS);
  const reference = usePolled("api-reference", fetchApiReference, DATA_REFRESH_MS);
  const snapshot = oldest(datasets.snapshot, reference.snapshot);
  return (
    <section aria-labelledby="data-heading" className={snapshotClass("view", snapshot)}>
      <h2 id="data-heading">Data</h2>
      <SnapshotNote snapshot={snapshot} what="This view shows" />
      {[datasets.error, reference.error]
        .filter((one) => one !== null)
        .map((one) => (
          <p role="alert" key={one}>
            {one}
          </p>
        ))}
      <h3>The evidence datasets</h3>
      <p>
        A sealed package of Meridian&apos;s own receptions, each with the verdict and the diagnosis Meridian concluded,
        so anyone can check its figures. Measured and simulated receptions are always in separate files.
      </p>
      {datasets.value !== null && <Packages read={datasets.value} />}
      <h3>The API</h3>
      <p>
        Everything on these pages comes from a public, read-only API, with no key to ask for and five requests a second
        for each caller. Every answer says whether it is simulated, and says in words when something has not been
        computed yet. Its full description is at <a href="/openapi.json">/openapi.json</a>.
      </p>
      {reference.value !== null && <Routes reference={reference.value} />}
      <h3>How to cite</h3>
      <p>
        A package is cited by its content hash, which names exactly what it holds: the line under each package above.
        The software is Apache-2.0, at <a href={REPOSITORY_URL}>{REPOSITORY_URL}</a>; cite the release or the commit you
        used.
      </p>
      <h3>Credits</h3>
      <Credits />
    </section>
  );
}
