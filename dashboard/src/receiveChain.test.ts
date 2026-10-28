import { describe, expect, it } from "vitest";

import { chainName, decodeReceiveChains, describeChain, openWarning } from "./receiveChain";

const watched = {
  band: "vhf",
  freq_min_hz: 137_000_000,
  freq_max_hz: 138_000_000,
  baseline: {
    evidence: "samples",
    method: "baseline-1",
    trained_from: "2026-10-06T00:00:00Z",
    trained_until: "2026-10-20T00:00:00Z",
    bin_width_deg: 10,
    bins: [
      { elevation_deg: 10, median_db: 4.2, p10_db: 2.1, receptions: 9 },
      { elevation_deg: 20, median_db: 7.4, p10_db: 5.0, receptions: 8 },
    ],
  },
  warnings: [],
};

const warned = {
  ...watched,
  warnings: [
    {
      method: "watch-1",
      raised_at: "2026-10-22T06:05:00Z",
      shortfall_db: 4.24,
      affected_bins: [
        { elevation_deg: 10, shortfall_db: 4.4, receptions: 3 },
        { elevation_deg: 20, shortfall_db: 4.1, receptions: 3 },
      ],
      cleared_at: null,
      cleared_shortfall_db: null,
    },
  ],
};

const body = { station_id: "st_1", simulated: true, chains: [watched] };

describe("decodeReceiveChains", () => {
  it("reads each chain, its baseline and its warnings", () => {
    const decoded = decodeReceiveChains({ ...body, chains: [warned] });

    expect(decoded.simulated).toBe(true);
    expect(decoded.chains[0]?.baseline?.bins).toBe(2);
    expect(decoded.chains[0]?.warnings[0]?.affectedDeg).toEqual([10, 20]);
  });

  it("names the field a renamed key broke", () => {
    expect(() => decodeReceiveChains({ ...body, chains: [{ ...watched, band: 7 }] })).toThrow("band");
  });
});

describe("a chain, described", () => {
  it("is named by its band and range", () => {
    const [chain] = decodeReceiveChains(body).chains;

    expect(chain && chainName(chain)).toBe("VHF 137–138 MHz");
  });

  it("says when it is hearing as usual", () => {
    const [chain] = decodeReceiveChains(body).chains;

    expect(chain && openWarning(chain)).toBeNull();
    expect(chain && describeChain(chain)).toBe(
      "Hearing as usual, against its signal from 6 Oct to 20 Oct at 2 elevations.",
    );
  });

  it("says by how much, and where, when it is falling short", () => {
    const [chain] = decodeReceiveChains({ ...body, chains: [warned] }).chains;

    expect(chain && describeChain(chain)).toBe(
      "Receive-chain warning since 22 Oct: recent passes were 4.2 dB weaker than this chain usually " +
        "hears, at 10°, 20°.",
    );
  });

  it("says when it is not watched yet", () => {
    const [chain] = decodeReceiveChains({ ...body, chains: [{ ...watched, baseline: null }] }).chains;

    expect(chain && describeChain(chain)).toMatch(/^Not watched yet/);
  });

  it("says when its last warning cleared", () => {
    const cleared = {
      ...warned,
      warnings: [{ ...warned.warnings[0], cleared_at: "2026-10-24T09:00:00Z", cleared_shortfall_db: 0.6 }],
    };
    const [chain] = decodeReceiveChains({ ...body, chains: [cleared] }).chains;

    expect(chain && describeChain(chain)).toMatch(/The last warning cleared 24 Oct\.$/);
  });
});
