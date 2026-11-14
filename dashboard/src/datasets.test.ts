import { describe, expect, it } from "vitest";

import { checksumLines, decodeDatasets, fileUrl, formatSize, hasSimulated } from "./datasets";
import { datasetsBody } from "./fixtures-data.test-data";

function packages() {
  const read = decodeDatasets(datasetsBody);
  if (!("packages" in read)) throw new Error("expected packages");
  return read.packages;
}

describe("the evidence datasets", () => {
  it("decode every package, its populations and its files", () => {
    const [first, second] = packages();
    expect(first?.name).toBe("abababababab");
    expect(first?.files.map((one) => one.name)).toEqual(["manifest.json", "receptions.jsonl"]);
    expect(second?.populations.map((one) => [one.population, one.simulated])).toEqual([
      ["measured", false],
      ["simulated", true],
    ]);
  });

  it("say why there is none in the platform's words", () => {
    expect(
      decodeDatasets({
        status: "not_yet_computed",
        reason: "None yet.",
        available_from_stage: 37,
      }),
    ).toEqual({
      notYetComputed: "None yet.",
    });
  });

  it("refuse a package missing a field rather than draw it blank", () => {
    const [first] = datasetsBody.packages;
    expect(() =>
      decodeDatasets({
        ...datasetsBody,
        packages: [{ ...first, citation: undefined }],
      }),
    ).toThrow("datasets.packages[0].citation");
  });
});

describe("a package on the page", () => {
  it("knows whether it holds simulated receptions", () => {
    expect(packages().map(hasSimulated)).toEqual([false, true]);
  });

  it("link each file where the platform serves it", () => {
    const [first] = packages();
    if (first === undefined) throw new Error("expected a package");
    expect(first.files.map((one) => fileUrl(first, one))).toEqual([
      "/api/v1/datasets/abababababab/manifest.json",
      "/api/v1/datasets/abababababab/receptions.jsonl",
    ]);
  });

  it("give sha256sum the names the downloads are saved under", () => {
    const [first] = packages();
    if (first === undefined) throw new Error("expected a package");
    expect(checksumLines(first)).toBe(
      `${"1".repeat(64)}  abababababab-manifest.json\n${"2".repeat(64)}  abababababab-receptions.jsonl`,
    );
  });

  it("state a size in the unit a reader expects", () => {
    expect([formatSize(512), formatSize(2048), formatSize(1_250_000)]).toEqual(["512 bytes", "2.0 KB", "1.3 MB"]);
  });
});
