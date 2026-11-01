# Tennis PIT metrics v3 rollout — 2026-10-25

## Result

Research materializes immutable canonical metric snapshots with `formula_version=tennis_independent_metrics_v3`. Neural reads the snapshots available by forecast calculation time and exposes the form windows for the event surface in the saved forecast diagnostics. The forecast formula remains `tennis_odds_free_v2` / `tennis_v1.1.0`; the new surface-form fields are diagnostic and do not change probabilities.

## Runtime evidence

At 2026-10-25 10:50 UTC, Research, `external-tennis-sources`, and Neural were healthy. The Research database contained 172 canonical metric snapshots with the v3 formula and `recent_by_surface`; the v3 snapshots were produced for 86 confirmed targets in each of two materializer runs. Each surface window stores `matches`, `wins`, `losses`, and `win_rate` for `last_10`, `last_20`, `days_90`, and `days_365`.

The materializer logs recorded generations 54628 and 55536, each with 86 snapshots written. The refresh table subsequently showed generation 56310 / processed generation 55536 because canonical history ingestion continued while the replay ran. The worker remains healthy and will process the newer generation on its next scheduled pass.

The live forecast API `GET /api/events/d57ee73f-92e8-4600-8af6-41695d44500e/forecasts/latest?model_name=tennis_score_baseline` returned a real Medvedev–Basilashvili forecast calculated at `2026-10-25T10:48:35.611208Z`. Both participants' canonical metric snapshots were available at `2026-10-25T10:45:07.287484Z`, before calculation. The A-side ATP ranking snapshot was available at `2026-10-24T06:54:02.441890Z`, also before calculation. The response included overall Elo, Glicko rating/RD, rolling 90-day results, ranking provenance, and per-side `form_by_surface` windows with sample volumes.

For the selected `unknown` surface, the live response showed A `last_10=8/10`, `last_20=15/20`, `days_90=0`, `days_365=5/6`; B `last_10=7/10`, `last_20=9/14`, `days_90=2/2`, `days_365=4/5` (wins/matches). The API therefore preserves the sample denominator and reports the surface category used.

## Data limitation

The active/prematch event census contained 351 tennis events; none had `surface_available_at`, so the live forecast correctly used `surface=unknown` instead of inferring a surface from retrospective or undated metadata. Surface-specific historical summaries are present in the v3 snapshots, but using a known surface for live forecasts still depends on a PIT-timestamped surface source. The forecast fingerprint now includes canonical snapshot digests and the selected surface summaries so a change to these diagnostics causes a new immutable forecast snapshot.

No training, holdout evaluation, model registration, bet journal writes, or bankroll operations were performed for this rollout.
