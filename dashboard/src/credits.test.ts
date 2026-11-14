import { describe, expect, it } from "vitest";

import attribution from "../../ATTRIBUTION.md?raw";
import { parseCredits, tableUnder } from "./credits";
import type { Inline } from "./releaseNotes";

function text(parts: Inline[]): string {
  return parts.map((one) => one.text).join("");
}

describe("the credits", () => {
  const credits = parseCredits(attribution);

  it("credit every ingested source, with its licence and terms", () => {
    const sources = credits.sources.map((one) => text(one.source));
    expect(sources).toContain("CelesTrak GP element sets");
    expect(sources).toContain("SatNOGS Network observations");
    const satnogs = credits.sources.find((one) => text(one.source) === "SatNOGS Network observations");
    expect(text(satnogs?.terms ?? [])).toContain("CC BY-SA 4.0");
  });

  it("link a terms page given as a bare address", () => {
    const celestrak = credits.sources.find((one) => text(one.source) === "CelesTrak GP element sets");
    const links = (celestrak?.terms ?? []).flatMap((one) => (one.kind === "link" ? [one.href] : []));
    expect(links).toContain("https://celestrak.org/usage-policy.php");
  });

  it("credit the libraries with their licences", () => {
    const sgp4 = credits.libraries.find((one) => text(one.name) === "sgp4");
    expect(sgp4 === undefined ? "" : text(sgp4.licence)).toBe("MIT");
  });

  it("read only the table under its heading, and a bar escaped inside a cell", () => {
    const markdown = [
      "## Other",
      "| a | b |",
      "|---|---|",
      "| no | no |",
      "## Dependencies",
      "",
      "| Library | Licence | Used for |",
      "|---|---|---|",
      "| one | MIT \\| BSD | x |",
      "",
      "| later | table | ignored |",
    ].join("\n");
    expect(tableUnder(markdown, "Dependencies")).toEqual([["one", "MIT | BSD", "x"]]);
  });
});
