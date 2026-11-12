// Every source's credit and licence, from ATTRIBUTION.md as it was when the
// dashboard was built (D-369).
//
// Bundled at build time, as the release notes are, so the page credits exactly
// what this build was written under and fetches nothing. Two of the file's
// tables are read: its ingested data sources, each with its licence and terms,
// and the libraries used as dependencies. A cell is read with the release
// notes' inline rules and drawn as elements, never as HTML.

import { parseInline, type Inline } from "./releaseNotes";

export interface DataSource {
  kind: Inline[];
  source: Inline[];
  usedFor: Inline[];
  terms: Inline[];
}

export interface Library {
  name: Inline[];
  licence: Inline[];
  usedFor: Inline[];
}

export interface Credits {
  sources: DataSource[];
  libraries: Library[];
}

/** One table row's cells; `\|` is a bar inside a cell. */
function cells(line: string): string[] {
  return line
    .trim()
    .replace(/^\||\|$/g, "")
    .split(/(?<!\\)\|/)
    .map((one) => one.replace(/\\\|/g, "|").trim());
}

/** The rows of the first table under `## <heading>`, without its header. */
export function tableUnder(markdown: string, heading: string): string[][] {
  const lines = markdown.split("\n");
  const start = lines.findIndex((line) => line.trim() === `## ${heading}`);
  if (start < 0) return [];
  const rows: string[][] = [];
  for (const line of lines.slice(start + 1)) {
    if (line.startsWith("## ")) break;
    if (line.trim().startsWith("|")) rows.push(cells(line));
    else if (rows.length > 0) break;
  }
  // The header, then the |---| line under it.
  return rows.slice(2);
}

export function parseCredits(markdown: string): Credits {
  return {
    sources: tableUnder(markdown, "Ingested data sources")
      .filter((row) => row.length >= 5)
      .map(([kind = "", source = "", usedFor = "", , terms = ""]) => ({
        kind: parseInline(kind),
        source: parseInline(source),
        usedFor: parseInline(usedFor),
        terms: parseInline(terms),
      })),
    libraries: tableUnder(markdown, "Dependencies")
      .filter((row) => row.length >= 3)
      .map(([name = "", licence = "", usedFor = ""]) => ({
        name: parseInline(name),
        licence: parseInline(licence),
        usedFor: parseInline(usedFor),
      })),
  };
}
