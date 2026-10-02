# Acceptance — every clause of Stage 24, and what proves it

The roadmap's Stage 24 says the project is software-complete when every clause in its five paths holds. This file is that list. It quotes each clause and gives its status and its evidence.

`tests/unit/test_acceptance_gate.py` keeps it true. It reads the roadmap and this file and checks the following:

- every clause appears here exactly once, as the roadmap words it;
- every test named here exists;
- every CI step named here is in `.github/workflows/ci.yml`;
- every section named here is a heading of the document it names;
- every pending row names a stage that exists.

A clause cannot pass by being left out, and evidence cannot quietly stop existing (D-254).

**Status values:**

- **proven** — a test, or a CI step, asserts it on every change.
- **run** — a run by hand whose transcript is recorded where the row says.
- **pending — Stage N** — not yet true. The stage named owes it.

**Where it stands.** As of 2026-10-02, 36 of the 40 clauses are proven. Stage 26's verdict is among them, though its SC-7 is *not measured* until station 001's receptions are rated (D-260). So is Stage 27's diagnosis, though SC-8 is not claimed until the review of its fault specification is recorded (D-270). Four are pending:

- **The seventy-two hour run.** It is done on the Pi by an operator, from `OPERATIONS.md § The 72-hour acceptance run on the Pi`, and is never started from a test. Its tool, its seal and the report's reading of it are all proven. When the run passes, its row becomes `run`, citing `SCALE-AND-FAULTS.md § The long run`.
- **Three of the six post-reception clauses.** These belong to Stages 28 to 30.

The project is software-complete when no row is pending.

Evidence is cited as `tests/<path>::<test>`, `ci.yml › <job> › <step>`, `<DOC>.md § <section>`, or a path in the repository.

## Operational path

| Clause | Status | Evidence |
|---|---|---|
| clean Compose startup under ten minutes | proven | `ci.yml › image › Bring the stack up`; `ci.yml › image › The bring-up took under ten minutes` |
| migrations complete automatically | proven | `ci.yml › image › Migrations ran and the API is healthy`; `ci.yml › test › Migrations are idempotent`; `tests/integration/test_migration_lifecycle.py::test_empty_database_reaches_head`; `tests/integration/test_failure_recovery.py::test_a_failed_migration_leaves_the_revision_it_started_from` |
| API and database health are visible | proven | `tests/msp_conformance/test_healthz.py::test_a_reachable_database_is_ok`; `tests/msp_conformance/test_healthz.py::test_an_unreachable_database_is_reported_not_raised`; `ci.yml › image › Migrations ran and the API is healthy`; `ci.yml › image › /metrics publishes the platform's own series, and only with the token`; `ci.yml › image › Grafana provisioned the platform dashboard` |
| invites can be created and revoked | proven | `tests/integration/test_cli_invite.py::test_one_invite_prints_one_bare_token`; `tests/integration/test_cli_invite.py::test_a_revoked_invite_admits_no_station`; `tests/integration/test_store_invites.py::test_revoke_expires_a_pending_invite`; `tests/integration/test_psycopg_registry.py::test_a_revoked_invite_is_rejected_through_the_store_layer` |
| stations can register securely | proven | `tests/msp_conformance/test_register_endpoint.py::test_a_successful_registration_returns_exactly_three_fields`; `tests/integration/test_migrations.py::test_no_plaintext_token_columns_exist`; `tests/integration/test_psycopg_registry.py::test_an_expired_invite_is_rejected`; `THREAT-MODEL.md § 4. Trust boundaries` |
| heartbeats update liveness | proven | `tests/integration/test_heartbeat_effects.py::test_a_heartbeat_moves_a_station_from_never_seen_to_online`; `tests/unit/test_liveness.py::test_a_recent_heartbeat_is_online`; `ci.yml › image › The simulated station is counted online, labelled simulated` |
| local element sets generate passes | proven | `tests/integration/test_pass_generation.py::test_the_real_propagator_produces_passes_and_repeats_them_exactly` |
| scheduler creates assignments | proven | `tests/integration/test_schedule_run.py::test_non_overlapping_passes_are_all_scheduled`; `tests/e2e/test_simulated_station_round_trip.py::test_the_gate`; `ci.yml › image › Prometheus scrapes both targets, loads every rule and records scheduling` |
| client persists and executes assignments | proven | `tests/unit/test_held_assignments.py::test_what_a_station_accepted_survives_a_restart`; `tests/msp_conformance/test_station_loop_end_to_end.py::test_a_station_receives_holds_executes_and_survives_an_outage`; `tests/msp_conformance/test_station_loop_end_to_end.py::test_a_restarted_station_resumes_from_its_own_record` |
| observations survive retries and restarts | proven | `tests/msp_conformance/test_station_loop_end_to_end.py::test_a_lost_acknowledgement_does_not_produce_a_second_observation`; `tests/msp_conformance/test_station_loop_end_to_end.py::test_a_station_restarted_before_delivery_still_delivers`; `tests/msp_conformance/test_platform_restart.py::test_a_fleet_lives_through_a_platform_restart`; `ci.yml › image › The platform survives its own faults` |
| public dashboard shows stations and results | proven | Stations: `tests/integration/test_public_stations_endpoints.py::test_a_listed_station_is_coarsened_and_labelled`; `tests/e2e/test_simulated_station_is_published.py::test_the_virtual_station_is_in_the_public_directory_as_simulated`. Results — a station's recent receptions and its capture rate with interval and count, simulated ones badged: `dashboard/src/receptions.test.ts`; `dashboard/src/reliability.test.ts`; `ci.yml › dashboard › vitest`; `tests/integration/test_public_observations_endpoint.py::test_an_observation_is_published_with_exactly_its_public_fields`. Both, served from the image: `ci.yml › image › The image serves the dashboard beside the API` |
| simulated data is always labelled | proven | `tests/integration/test_migrations.py::test_simulated_flag_reaches_every_derived_table`; `tests/integration/test_schedule_run.py::test_simulated_survives_from_the_station_through_to_the_assignment`; `tests/e2e/test_simulated_station_round_trip.py::test_provenance_survives_every_layer`; `tests/integration/test_public_observations_endpoint.py::test_a_simulator_run_is_labelled_and_carries_no_seed` |

## Intelligent path

| Clause | Status | Evidence |
|---|---|---|
| snapshots and feature generation are versioned | proven | Snapshots: `tests/unit/test_snapshot_gate.py::test_one_changed_setting_is_another_dataset`; `tests/unit/test_datasets_manifest.py::test_when_it_was_made_is_not_part_of_the_hash`. Feature generation (D-255): `tests/unit/test_feature_version.py::test_the_feature_code_is_the_code_its_version_names`; `tests/unit/test_prediction_fit.py::test_a_model_names_the_feature_code_it_was_fitted_on`; `tests/unit/test_prediction_live.py::test_a_model_fitted_on_other_feature_code_is_refused` |
| configurations A–D run without code changes | proven | `tests/unit/test_prediction_gate.py::test_a_to_d_run_from_one_key` |
| cold start is tested | proven | `tests/unit/test_prediction_gate.py::test_a_station_that_joins_late_is_scored_by_geometry_and_says_why`; `tests/unit/test_prediction_configurations.py::test_a_new_station_takes_the_geometry_route` |
| only temporal evaluation is used | proven | `tests/unit/test_prediction_gate.py::test_nothing_that_evaluates_can_shuffle`; `tests/unit/test_prediction_gate.py::test_no_outcome_after_train_until_reaches_what_training_learns` |
| completeness and IPW are reported | proven | `tests/unit/test_completeness_gate.py::test_every_evaluation_dataset_carries_its_completeness`; `tests/unit/test_completeness_gate.py::test_a_deterministic_policy_is_reported_as_a_positivity_violation`; `tests/unit/test_report_data.py::test_both_populations_state_their_completeness_and_weighting` |
| calibration and Brier scores are produced | proven | `tests/unit/test_prediction_gate.py::test_the_report_carries_what_calibration_is_judged_by`; `tests/unit/test_prediction_calibration.py::test_brier_is_the_mean_squared_gap`; `tests/unit/test_report_prediction.py::test_each_fitted_model_has_a_reliability_diagram_that_parses` |
| scheduler constraints are always validated | proven | `tests/unit/test_scheduler_gate.py::test_a_solver_that_answers_nothing_or_wrongly_still_leaves_a_valid_schedule`; `tests/unit/test_scheduler_gate.py::test_a_schedule_nobody_checked_stops_the_comparison`; `tests/integration/test_schedule_run.py::test_a_schedule_that_breaks_a_constraint_is_never_written`; `tests/unit/test_scheduler_constraints.py::test_every_greedy_schedule_passes_the_validator` |
| D is compared primarily against B | proven | `tests/unit/test_schedule_replay.py::test_sc_1_is_d_against_the_optimised_b`; `tests/unit/test_report_scheduling.py::test_sc1_is_the_relative_d_minus_b_gain` |
| the oracle is clearly nondeployable | proven | `tests/unit/test_scheduler_boundaries.py::test_nothing_live_reaches_the_oracle`; `tests/unit/test_scheduler_boundaries.py::test_the_reach_would_notice_the_oracle`; `tests/unit/test_schedule_config.py::test_a_setting_that_cannot_be_obeyed_is_refused`; it is reported as an upper bound only: `tests/unit/test_scheduler_gate.py::test_the_oracle_takes_at_least_every_scheduler_s_frames_on_every_day` |

## Reliability path

| Clause | Status | Evidence |
|---|---|---|
| absence is never automatically counted as a miss | proven | `tests/unit/test_reliability_classification.py::test_silence_with_no_heartbeat_is_a_station_that_was_not_there`; `tests/unit/test_reliability_classification.py::test_an_expiry_while_nobody_was_heard_is_not_a_decline`; `tests/integration/test_reliability_gate.py::test_without_its_listening_heartbeat_the_miss_is_not_a_miss` |
| liveness thresholds are enforced | proven | `tests/integration/test_reliability_live.py::test_a_heartbeat_vouches_until_the_next_or_the_offline_threshold`; `ci.yml › monitoring › Every alert fires and stays silent as its tests say` |
| loss-budget debits retain evidence | proven | `tests/unit/test_reliability_slis.py::test_every_lost_pass_is_a_debit_with_its_reason`; `tests/integration/test_reliability_gate.py::test_every_stored_evidence_is_what_the_tables_say` |
| failures are detected within the target window | proven | `tests/unit/test_reliability_faults.py::test_a_silence_past_ninety_seconds_is_detected_within_sc_5`; `tests/integration/test_fault_gate.py::test_every_injected_fault_is_detected_and_handled`; `tests/integration/test_fault_gate.py::test_the_gate_tested_what_it_claims_to` |
| the scheduler avoids failed stations | proven | `tests/integration/test_schedule_run.py::test_an_offline_station_is_given_nothing_and_decided_when_back`; `tests/integration/test_schedule_run.py::test_an_offline_station_s_work_is_revoked_and_decided_again_on_return` |
| fifty simulated stations work | proven | `tests/e2e/test_fifty_stations.py::test_fifty_stations_operate_through_real_msp`; `tests/e2e/test_fifty_stations.py::test_the_series_do_not_grow_with_the_fleet`; `SCALE-AND-FAULTS.md § Scale` |
| the platform survives a 72-hour unattended simulation | pending — Stage 24 | The run itself, done on the Pi by an operator from `OPERATIONS.md § The long run`, with `deploy/tools/long_run.py`. The tool judges resources and alerts, resumes and seals itself: `tests/unit/test_long_run_watch.py::test_a_short_run_brings_itself_up_settles_clean_and_seals_itself`; `tests/unit/test_long_run_watch.py::test_memory_that_climbs_in_the_second_half_fails_the_run`; `tests/unit/test_long_run_watch.py::test_a_platform_fault_that_outlasts_its_alert_owes_it`; `tests/unit/test_long_run_watch.py::test_resuming_mends_and_closes_what_the_stopped_tool_left_open`. The report counts it only from a sealed record that spans 72 hours and passed: `tests/unit/test_report_faults.py::test_the_long_run_is_included_only_when_a_run_spans_72_hours`; `tests/unit/test_report_faults.py::test_a_run_whose_own_judgement_failed_is_not_included`. Only a two-hour rehearsal has been run (`SCALE-AND-FAULTS.md § The long run`) |

## Reproducibility path

| Clause | Status | Evidence |
|---|---|---|
| all dependencies and images are pinned | proven | `tests/unit/test_pinning.py::test_every_image_is_named_by_digest`; `tests/unit/test_pinning.py::test_every_base_the_image_builds_from_is_named_by_digest`; `tests/unit/test_pinning.py::test_every_action_is_named_by_commit`; `tests/unit/test_pinning.py::test_one_uv_reads_the_lockfile_everywhere`; `tests/unit/test_pinning.py::test_dependabot_watches_every_pinned_ecosystem`; `ci.yml › lint › Lockfile matches pyproject`; `uv.lock`; `dashboard/package-lock.json` |
| every dataset has a manifest and hash | proven | `tests/unit/test_datasets_manifest.py::test_when_it_was_made_is_not_part_of_the_hash`; `tests/unit/test_snapshot_gate.py::test_the_hash_does_not_depend_on_the_process`; `tests/integration/test_snapshot_gate.py::test_the_dataset_is_the_same_after_its_source_rows_are_gone`; `tests/unit/test_ingest_raw_store.py::test_a_published_artefact_reads_back_byte_for_byte` |
| every experiment has a config and seed | proven | `tests/unit/test_report_cli.py::test_the_run_keeps_its_configuration_byte_for_byte_and_its_seed`; `tests/unit/test_report_gate.py::test_the_run_records_its_code_and_what_it_drew_from`; `tests/unit/test_datasets_seeds.py::test_a_components_seed_is_the_same_everywhere`; `tests/unit/test_scheduler_gate.py::test_the_comparison_moves_with_its_seed_and_with_its_data`; a long run's fault run seals its seed and settings in its record: `tests/unit/test_report_faults.py::test_the_record_is_sealed_with_the_run`; `tests/integration/test_cli_reliability.py::test_a_long_run_s_record_is_sealed_with_its_faults` |
| every result includes sample size and uncertainty | proven | `tests/unit/test_report_gate.py::test_every_estimate_carries_its_interval_and_its_count`; `tests/unit/test_report_data.py::test_the_indeterminate_share_carries_its_interval_and_its_n`; `tests/unit/test_report_prediction.py::test_sc2_is_read_from_d_with_its_interval`; `tests/unit/test_scheduler_comparison.py::test_the_interval_is_the_same_for_one_seed_and_moves_with_another`; `EVALUATION.md § 10. Reporting rules` |
| every figure is regenerated by a command | proven | `tests/unit/test_report_gate.py::test_every_figure_is_regenerated_byte_for_byte_from_the_results`; `tests/unit/test_report_gate.py::test_verify_regenerates_it`; `OPERATIONS.md § Evaluation reports` |
| measured, archive-derived, and simulated results remain separate | proven | `tests/unit/test_datasets_completeness.py::test_the_populations_are_never_pooled`; `tests/unit/test_report_data.py::test_measured_and_simulated_are_counted_apart`; `tests/unit/test_reliability_report.py::test_measured_and_simulated_are_counted_apart`; `tests/unit/test_prediction_fit.py::test_a_dataset_of_simulated_passes_is_refused_as_simulated`; `tests/integration/test_migrations.py::test_a_run_over_both_populations_is_two_rows_never_one_total`; `tests/integration/test_ingest_gate.py::test_every_loaded_record_can_say_where_it_came_from` |

## Post-reception path

| Clause | Status | Evidence |
|---|---|---|
| every measured reception carries a versioned, calibrated verdict | proven | Every closed, scheduled measured reception holds a verdict naming its model's method, scored by a Platt-calibrated model: `tests/integration/test_verdict_gate.py::test_every_measured_reception_carries_a_versioned_verdict`. SC-7, how well calibrated, is built and verified through `meridian report`: `tests/unit/test_report_verdict.py::test_sc7_is_measured_and_drawn`; `tests/unit/test_report_verdict.py::test_the_report_and_figures_regenerate_from_the_results`. **SC-7 is *not measured* until receptions are rated** (D-260): `tests/unit/test_report_verdict.py::test_without_dates_sc7_is_not_measured`; `OPERATIONS.md § Reception verdicts` |
| every loss has a diagnosed cause or says undetermined | proven | Every loss of a faulted fleet is diagnosed, with its cause or *undetermined*: `tests/integration/test_diagnosis_gate.py::test_every_loss_is_diagnosed`; `tests/integration/test_diagnosis_gate.py::test_a_dead_receiver_s_losses_are_named_for_it`; `tests/integration/test_diagnosis_gate.py::test_a_stepped_clock_s_losses_are_named_for_it`. Each cause from planted evidence, and expired and revoked work never: `tests/integration/test_loss_diagnosis.py::test_each_planted_loss_is_named_for_what_it_left`. SC-8 is built from sealed fleets and verified through `meridian report`: `tests/unit/test_report_diagnosis.py::test_built_with_sealed_runs_it_verifies_and_regenerates`. **SC-8 is not claimed until the fault specification's review is recorded** (D-270): `tests/unit/test_report_diagnosis.py::test_the_review_flag_follows_the_specification`; `OPERATIONS.md § Loss diagnosis` |
| receive-chain warnings precede failure on injected degradation | pending — Stage 28 | The degradation it is scored on is built: `tests/unit/test_simulator_sky_faults.py::test_every_fault_is_reproducible_from_its_seed` |
| owner reports are delivered and regenerate to their recorded hash | pending — Stage 29 | |
| the evidence dataset regenerates to an identical hash | pending — Stage 30 | |
| simulator ground truth never reaches MSP or the platform database | proven | `tests/integration/test_ground_truth_gate.py::test_the_gate`; `tests/unit/test_simulator_sky_ground_truth.py::test_no_fault_and_none_of_its_parameters_reach_an_msp_body` |
