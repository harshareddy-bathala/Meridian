// The reports page's bodies, as the platform serves them (D-367, D-368).

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

/** `/api/v1/reports/criteria`: one measured on simulated runs, one measured on
 * our stations but not claimed, one not measured. */
export const criteriaBody = {
  status: "computed",
  schema: "criteria-summary-1",
  run_sha256: "8a".repeat(32),
  method: "report-7",
  since: "2026-10-01T00:00:00+00:00",
  as_of: "2026-10-31T00:00:00+00:00",
  criteria: [
    {
      id: "SC-4",
      simulated: false,
      proposed: false,
      status: "measured",
      reason: null,
      figures: [
        { name: "capture_rate", value: 0.93, low: 0.88, high: 0.96, target: 0.9, direction: "at_least", n: 140 },
      ],
      met: true,
      interval_clears: false,
      claimable: false,
      caveat: "measured over 7 days, not the thirty SC-4 is over",
    },
    {
      id: "SC-5",
      simulated: true,
      proposed: false,
      status: "measured",
      reason: null,
      figures: [
        { name: "slowest_detection_s", value: 68.15, low: null, high: null, target: 90, direction: "at_most", n: 123 },
      ],
      met: true,
      interval_clears: null,
      claimable: true,
      caveat: null,
    },
    {
      id: "SC-2",
      simulated: false,
      proposed: false,
      status: "not_measured",
      reason: "D was not fitted",
      figures: [],
      met: null,
      interval_clears: null,
      claimable: false,
      caveat: null,
    },
  ],
};
