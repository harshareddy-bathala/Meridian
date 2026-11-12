// The published evidence packages, as /api/v1/datasets serves them (D-369).
//
// Schema `evidence-index-1`: for each package, what it was built from by hash,
// its populations with their counts, its licence and citation, and every file
// with the digest and size a download must have. Every package is the public
// cut, to the minute and the degree (D-093). A simulated population is labelled
// by the platform, and badged on the page (rule 5).

import { getJson, type Fetcher } from "./api";
import { asBoolean, asNumber, asObject, asOneOf, asString, DecodeError, type Fields } from "./decode";

export interface PackageFile {
  name: string;
  sha256: string;
  sizeBytes: number;
}

export interface PackagePopulation {
  population: "measured" | "simulated";
  file: string;
  receptions: number;
  simulated: boolean;
}

export interface EvidencePackage {
  name: string;
  contentSha256: string;
  snapshotSha256: string;
  configSha256: string;
  transformation: string;
  since: string;
  asOf: string;
  licence: string;
  citation: string;
  populations: PackagePopulation[];
  files: PackageFile[];
}

export type DatasetsRead = { packages: EvidencePackage[] } | { notYetComputed: string };

function list(fields: Fields, key: string, path: string): unknown[] {
  const value = fields[key];
  if (!Array.isArray(value)) {
    throw new DecodeError(`${path}.${key}`, "an array");
  }
  return value;
}

function decodeFile(value: unknown, path: string): PackageFile {
  const fields = asObject(value, path);
  return {
    name: asString(fields, "name", path),
    sha256: asString(fields, "sha256", path),
    sizeBytes: asNumber(fields, "size_bytes", path),
  };
}

function decodePopulation(value: unknown, path: string): PackagePopulation {
  const fields = asObject(value, path);
  return {
    population: asOneOf(fields, "population", ["measured", "simulated"] as const, path),
    file: asString(fields, "file", path),
    receptions: asNumber(fields, "receptions", path),
    simulated: asBoolean(fields, "simulated", path),
  };
}

function decodePackage(value: unknown, path: string): EvidencePackage {
  const fields = asObject(value, path);
  return {
    name: asString(fields, "name", path),
    contentSha256: asString(fields, "content_sha256", path),
    snapshotSha256: asString(fields, "snapshot_sha256", path),
    configSha256: asString(fields, "config_sha256", path),
    transformation: asString(fields, "transformation", path),
    since: asString(fields, "since", path),
    asOf: asString(fields, "as_of", path),
    licence: asString(fields, "licence", path),
    citation: asString(fields, "citation", path),
    populations: list(fields, "populations", path).map((one, index) =>
      decodePopulation(one, `${path}.populations[${String(index)}]`),
    ),
    files: list(fields, "files", path).map((one, index) => decodeFile(one, `${path}.files[${String(index)}]`)),
  };
}

export function decodeDatasets(body: unknown): DatasetsRead {
  const path = "datasets";
  const fields = asObject(body, path);
  if (fields.status === "not_yet_computed") {
    return { notYetComputed: asString(fields, "reason", path) };
  }
  return {
    packages: list(fields, "packages", path).map((one, index) =>
      decodePackage(one, `${path}.packages[${String(index)}]`),
    ),
  };
}

export async function fetchDatasets(fetcher: Fetcher, signal: AbortSignal): Promise<DatasetsRead> {
  return decodeDatasets(await getJson(fetcher, "/api/v1/datasets", "the evidence datasets", signal));
}

/** Where one file of one package is downloaded from. The front keeps a copy of
 * each, warmed from this listing (deploy/tools/front_warm.py). */
export function fileUrl(pkg: EvidencePackage, file: PackageFile): string {
  return `/api/v1/datasets/${encodeURIComponent(pkg.name)}/${encodeURIComponent(file.name)}`;
}

/** A size in the unit a reader expects: bytes, then KB and MB, decimal. */
export function formatSize(bytes: number): string {
  if (bytes < 1000) return `${String(bytes)} bytes`;
  if (bytes < 1_000_000) return `${(bytes / 1000).toFixed(1)} KB`;
  return `${(bytes / 1_000_000).toFixed(1)} MB`;
}

/** The lines `sha256sum -c` checks a downloaded package against. Each file is
 * saved under its package's name, as the download names it. */
export function checksumLines(pkg: EvidencePackage): string {
  return pkg.files.map((one) => `${one.sha256}  ${pkg.name}-${one.name}`).join("\n");
}

/** Whether any population of a package is simulated. */
export function hasSimulated(pkg: EvidencePackage): boolean {
  return pkg.populations.some((one) => one.simulated);
}
