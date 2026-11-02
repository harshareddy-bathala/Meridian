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
