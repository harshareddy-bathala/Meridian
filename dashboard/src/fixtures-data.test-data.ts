// What the data page reads, for the tests that draw it.

const packageFiles = [
  { name: "manifest.json", sha256: "1".repeat(64), size_bytes: 2048 },
  { name: "receptions.jsonl", sha256: "2".repeat(64), size_bytes: 1_250_000 },
];

const citation = (hash: string) =>
  `Meridian evidence dataset, content sha256 ${hash}, https://github.com/harshareddy-bathala/Meridian`;

const built = {
  snapshot_sha256: "cd".repeat(32),
  transformation: "evidence-1",
  since: "2026-09-01T00:00:00Z",
  as_of: "2026-09-23T06:00:00Z",
  created_at: "2026-09-23T07:00:00Z",
  licence: "CC-BY-4.0",
};

const measured = { population: "measured", file: "receptions.jsonl", receptions: 12, simulated: false };

/** `/api/v1/datasets`: one measured package, and one that holds simulated
 * receptions beside its measured ones (D-369). */
export const datasetsBody = {
  status: "computed",
  schema: "evidence-index-1",
  packages: [
    {
      ...built,
      name: "abababababab",
      content_sha256: "ab".repeat(32),
      config_sha256: "ef".repeat(32),
      citation: citation("ab".repeat(32)),
      populations: [measured],
      files: packageFiles,
    },
    {
      ...built,
      name: "cdcdcdcdcdcd",
      content_sha256: "cd".repeat(32),
      config_sha256: "01".repeat(32),
      citation: citation("cd".repeat(32)),
      populations: [
        measured,
        { population: "simulated", file: "receptions_simulated.jsonl", receptions: 40, simulated: true },
      ],
      files: [...packageFiles, { name: "receptions_simulated.jsonl", sha256: "3".repeat(64), size_bytes: 512 }],
    },
  ],
};
