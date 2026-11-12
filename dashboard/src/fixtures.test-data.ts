// Response bodies as the platform publishes them, shared by the view tests.
//
// Built from the public models' field lists (tests/unit/test_public_privacy.py),
// so a renamed field fails a decode here before it blanks a cell on the page.

export function assignmentBody(id: string, decision: "scheduled" | "skipped" = "scheduled") {
  return {
    assignment_id: id,
    pass_id: 7,
    station_id: "st_1",
    satellite_id: "norad:57166",
    start_at: "2026-11-02T10:04:00Z",
    end_at: "2026-11-02T10:16:00Z",
    issued_at: "2026-11-02T09:00:00Z",
    centre_freq_hz: 137_100_000,
    mode: "lrpt",
    timing_uncertainty_s: 4.2,
    decision,
    reason: decision === "scheduled" ? "highest value in its window" : "overlaps a better pass",
    score: 0.61,
    conflicts_with_assignment_id: decision === "skipped" ? "as_best" : null,
    priority: 1,
    predicted_yield: null,
    model_config: "A",
    state: decision === "skipped" ? null : "issued",
    simulated: true,
    schedule_run_id: "sr_1",
    model_sha256: null,
    explanation: {
      terms: {
        value: 432.1,
        yield: 0.6,
        yield_source: "elevation_proxy",
        yield_path: null,
        yield_reason: "peak elevation over 90°",
        frames: 720,
        frames_term: "duration",
        priority: 1,
        priority_weighted: false,
      },
      weighed_against: [{ pass_id: 8, decision: "skipped", value: 211.4, assignment_id: null }],
      rule: decision === "skipped" ? "overlap" : null,
      alternative: { pass_id: 8, decision: "skipped", value: 211.4, assignment_id: null },
      run: { status: "optimal", history_as_of: null },
    },
    revision: 1,
    revoked_reason: null,
  };
}

export const lossBody = {
  diagnosis_id: 41,
  assignment_id: "as_loss",
  station_id: "st_1",
  satellite_id: "norad:57166",
  window_start: "2026-11-02T10:04:00Z",
  window_end: "2026-11-02T10:16:00Z",
  classification: "signal_no_decode",
  cause: "obstruction",
  tested: [
    { cause: "obstruction", fired: true, support: 0.73, findings: { lost_inside: 9, share: 0.41 } },
    { cause: "satellite_silent", fired: false, support: 0, findings: { signals: 2, state: null } },
  ],
  method: "diagnosis-1",
  computed_at: "2026-11-02T11:20:00Z",
  simulated: true,
};

export const warningBody = {
  warning_id: 3,
  station_id: "st_1",
  band: "vhf",
  freq_min_hz: 137_000_000,
  freq_max_hz: 138_000_000,
  method: "watch-1",
  raised_at: "2026-11-02T06:05:00Z",
  shortfall_db: 4.2,
  affected_bins: [{ elevation_deg: 10, shortfall_db: 4.4, receptions: 3 }],
  cleared_at: null,
  cleared_shortfall_db: null,
  simulated: true,
};

export const runBody = {
  status: "computed",
  run_id: "sr_1",
  decided_at: "2026-11-02T10:00:00Z",
  horizon_start: "2026-11-02T10:00:00Z",
  horizon_end: "2026-11-02T16:00:00Z",
  configuration: "A",
  yield_source: "elevation_proxy",
  model_sha256: null,
  history_as_of: null,
  solver: "highs",
  solver_version: "1.11.0",
  solver_status: "optimal",
  objective: 4.25,
  bound: 4.25,
  time_limit_s: 10,
  runtime_s: 0.4,
  stations: 50,
  candidates: 30,
  scheduled: 22,
  skipped: 8,
  simulated_decisions: 30,
  measured_decisions: 0,
  baseline_gain: { status: "not_measured", reason: "measured on a snapshot (D-172)" },
};

export const kpSample = {
  quantity: "kp_index",
  value: 3.67,
  missing_reason: null,
  unit: "Kp (0-9)",
  observed_from: "2026-11-02T09:00:00Z",
  observed_to: "2026-11-02T12:00:00Z",
  published_at: "2026-11-02T09:05:00Z",
  published_basis: "retrieved",
  product: "SWPC",
  lat_deg: null,
  lon_deg: null,
  source: {
    source_id: "noaa_swpc_kp",
    name: "NOAA SWPC Kp",
    licence: "US Government work",
    terms_url: "https://www.swpc.noaa.gov/disclaimer",
  },
};

export function bins(empty = 1) {
  return Array.from({ length: 10 }, (_, index) => ({
    low: index / 10,
    high: (index + 1) / 10,
    n: index >= 10 - empty ? 0 : 12,
    mean_predicted: index >= 10 - empty ? null : index / 10 + 0.05,
    observed: index >= 10 - empty ? null : index / 10,
    observed_interval: null,
  }));
}

export const insightsBody = {
  status: "computed",
  schema: "model-summary-1",
  run_sha256: "ab".repeat(32),
  method: "report-9",
  label: "Measured by the SatNOGS network, not by Meridian. Selection-biased.",
  source: {
    name: "SatNOGS Network",
    licence: "CC BY-SA 4.0",
    terms_url: "https://network.satnogs.org/terms",
    retrieved_from: "2026-10-01T00:00:00+00:00",
    retrieved_until: "2026-10-29T00:00:00+00:00",
  },
  simulated: false,
  completeness_ratio: 0.31,
  split: {
    train_until: "2026-10-15T00:00:00.000Z",
    validate_until: "2026-10-22T00:00:00.000Z",
    as_of: "2026-10-29T00:00:00Z",
  },
  configurations: [
    {
      name: "A",
      computable: true,
      n_test: 108,
      n_decoded: 41,
      brier: 0.19,
      brier_interval: [0.16, 0.22],
      base_rate: 0.38,
      base_brier: 0.236,
      skill: 0.19,
      skill_interval: null,
      bins: bins(),
    },
    { name: "C", computable: false, reason: "needs a learned station profile" },
  ],
};

const proportionBody = (numerator: number, denominator: number) => ({
  numerator,
  denominator,
  estimate: denominator === 0 ? null : numerator / denominator,
  interval: denominator === 0 ? null : [0.5, 0.95],
});

function dayBody(day: string, settled: boolean, decoded: number, eligible: number) {
  return {
    day,
    settled,
    passes: eligible,
    capture_rate: proportionBody(decoded, eligible),
    loss_budget: {
      capture_target: 0.9,
      eligible,
      allowed: eligible * 0.1,
      spent: eligible - decoded,
      remaining: eligible * 0.1 - (eligible - decoded),
      remaining_ratio: eligible === 0 ? null : 0,
      exhausted: eligible - decoded > eligible * 0.1,
      by_reason: {},
    },
  };
}

/** /api/v1/aggregates: simulated stations busy, measured ones quiet. */
export const aggregatesBody = {
  status: "computed",
  as_of: "2026-11-07T15:00:00Z",
  method: "classification-1",
  config_sha256: "ab".repeat(32),
  settle_margin_s: 86_400,
  capture_target: 0.9,
  measured: {
    simulated: false,
    today: {
      day: "2026-11-07",
      scheduled: 0,
      ended: 0,
      classified: 0,
      by_class: { successful_reception: 0, satellite_silent: 0 },
      listening_confirmed: 0,
      awaiting: 0,
    },
    days: ["01", "02", "03", "04", "05", "06", "07"].map((d) => dayBody(`2026-11-${d}`, true, 0, 0)),
  },
  simulated: {
    simulated: true,
    today: {
      day: "2026-11-07",
      scheduled: 40,
      ended: 22,
      classified: 10,
      by_class: {
        successful_reception: 7,
        confirmed_miss: 1,
        station_unavailable: 1,
        satellite_silent: 1,
        satellite_state_indeterminate: 0,
      },
      listening_confirmed: 8,
      awaiting: 12,
    },
    days: ["01", "02", "03", "04", "05", "06", "07"].map((d) => dayBody(`2026-11-${d}`, d < "06", 9, 10)),
  },
};

export function eventBody(kind: string, simulated = true) {
  return {
    at: "2026-11-07T14:03:12Z",
    kind,
    ref: `${kind}-1`,
    station_id: "st_1",
    simulated,
    satellite_id: kind === "pass_decoded" || kind === "loss_diagnosed" ? "norad:57166" : null,
    assignment_id: kind === "pass_decoded" || kind === "loss_diagnosed" ? "as_1" : null,
    cause: kind === "loss_diagnosed" ? "obstruction" : null,
  };
}

/** /api/v1/satellites/tracks: one satellite crossing the antimeridian, one without an orbit. */
export const tracksBody = {
  status: "computed",
  at: "2026-11-07T14:00:00Z",
  step_s: 60,
  horizon_s: 5400,
  satellites: [
    {
      satellite_id: "norad:57166",
      name: "Meteor-M2-3",
      is_active: true,
      element_set_epoch: "2026-11-07T08:00:00Z",
      element_set_age_s: 21_600,
      element_set_source: "celestrak",
      track: [
        { t: "2026-11-07T14:00:00Z", lat_deg: 10, lon_deg: 178 },
        { t: "2026-11-07T14:01:00Z", lat_deg: 12, lon_deg: -178 },
        { t: "2026-11-07T14:02:00Z", lat_deg: 14, lon_deg: -174 },
      ],
    },
  ],
  omitted: [{ satellite_id: "norad:59051", name: "Meteor-M2-4", reason: "no_element_set" }],
};

export function resultBody(id: number, classification: string, effect: string, simulated = true) {
  return {
    classification_id: id,
    assignment_id: `as_${String(id)}`,
    station_id: "st_1",
    satellite_id: "norad:57166",
    window_start: "2026-11-06T10:04:00Z",
    window_end: "2026-11-06T10:16:00Z",
    classification,
    effect,
    listening_confirmed: classification !== "station_unavailable",
    outcome: classification === "successful_reception" ? "decoded" : null,
    diagnosis_id: effect === "lost" ? 9 : null,
    cause: effect === "lost" ? "obstruction" : null,
    simulated,
  };
}

/** /api/v1/areas, published: one area, two quantities, one gap, one change. */
export const areasBody = {
  status: "computed",
  simulated: false,
  summary: {
    schema: "areas-summary-1",
    report_sha256: "a".repeat(64),
    method: "regions-1",
    since: "2026-10-05T00:00:00+00:00",
    as_of: "2026-11-02T00:00:00+00:00",
    sources: [{ source_id: "ornl_modis_ndvi", licence: "NASA open data", terms_url: "https://example.org/terms" }],
    areas: [{ area_id: 1, label: "Bengaluru urban", area_km2: 741.0, bbox: [77.46, 12.83, 77.75, 13.14] }],
    series: [
      ["2026-10-21", 0.4, null],
      ["2026-10-05", 0.41, null],
      ["2026-11-01", null, "no granule covered the area"],
    ].map(([from, value, missing]) => ({
      area_id: 1,
      quantity: "ndvi",
      unit: "1",
      period_from: `${String(from)}T00:00:00+00:00`,
      period_until: `${String(from)}T00:00:00+00:00`,
      value,
      missing_reason: missing,
      count: value === null ? 0 : 3,
      sources: ["ornl_modis_ndvi"],
    })),
    changes: [
      {
        area_id: 1,
        quantity: "ndvi",
        verdict: "within",
        reason: "the 95% interval reaches -0.15 or does not cross it",
        baseline: { from: "2026-10-05T00:00:00+00:00", until: "2026-10-21T00:00:00+00:00" },
        current: { from: "2026-10-21T00:00:00+00:00", until: "2026-11-02T00:00:00+00:00" },
        baseline_mean: 0.41,
        current_mean: 0.4,
        change: -0.0244,
        low: -0.05,
        high: 0.03,
      },
    ],
  },
};

/** `/api/v1/reports/weekly`, with a simulated section as the template writes it. */
export const weeklyBody = {
  status: "computed",
  template: "network-weekly-1",
  period_start: "2026-11-02T00:00:00Z",
  period_end: "2026-11-09T00:00:00Z",
  previous_week: "2026-10-26",
  as_of: "2026-11-12T06:20:51Z",
  includes_simulated: true,
  subject: "Meridian network, week of 2026-11-02",
  body: [
    "Week: 2026-11-02 to 2026-11-09, Monday to Monday UTC. A pass belongs to the week its window closed in.",
    "",
    "MEASURED STATIONS",
    "No measured station is registered.",
    "",
    "SIMULATED STATIONS — not real receptions.",
    "Stations: 12 registered, 12 heard from during the week.",
    "Passes settled: 36; decoded 2, station unavailable 34.",
    "",
    "Written from Meridian's stored results by template network-weekly-1; no part of it was generated.",
  ].join("\n"),
  inputs_sha256: "7d".repeat(32),
  content_sha256: "dd".repeat(32),
};
