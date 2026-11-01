# Tennis odds-free experiment pre-registration v2 — 2026-10-27

## Status

This is a frozen protocol proposal and evidence snapshot, not training admission. The admission gate remains **blocked**. Do not fit LR, GB, or GRU; open holdout quality; register SHADOW; or promote until a fresh full audit passes every per-target threshold and PIT/chronology check in `AGENTS.md` §9.1. Thresholds are unchanged.

## Dataset and current evidence

- Source scope: real Fonbet tennis matches and their timestamped snapshots; whole matches are the split unit. Synthetic or retrospectively inferred labels are excluded.
- Current revalidated cohort: 7,792 saved examples across 3,207 matches. The saved optional-serve v2 builder accepts 7,742 examples and rejects 50 for incomplete numeric match state. This is a saved-cohort check, not a current full-database census.
- Dataset/split manifest: [`tennis_pit_v2_manifest_2026-10-27.json`](tennis_pit_v2_manifest_2026-10-27.json), manifest version `tennis_shared_walk_forward_v3`, dataset digest `08c700e482150d96f65ecfc4ccdfe41b1aa5dd915dba3e091f45a75509b9a642`, 3,207 matches (2,565 development, 642 sealed holdout), four chronological expanding folds. Holdout quality metrics remain unopened.
- Latest saved-cohort label coverage: point 81 matches, game 82, set 82, match 3,207. These are counts of labelled matches in the audit, not admitted examples. For point/game/set they are far below 500 train + 4×100 validation + 200 holdout, before PIT and temporal purging.
- Label definitions: point winner, current game winner, current set winner, and match winner; each target is separately masked when not yet observed. A result label is usable for a train fold only when its proven source `available_at` is no later than that fold's earliest validation forecast. Unknown result time is excluded/purged; schedule and `finished_at` are not substitutes.
- Dataset example artifact: [`tennis_pit_v2_optional_serve_7792_2026-10-27.jsonl.gz.gz.gz`](tennis_pit_v2_optional_serve_7792_2026-10-27.jsonl.gz.gz.gz), SHA-256 `3a7320a18a183b78db25706a32102d9d67c41ac48ad08b1ffe62028d992c7fd9`. Its 7,742 accepted examples do not replace the audited shared split manifest.

## Feature and inference contract

- Feature contract: `tennis_odds_free_v2`, defined by `ODDS_FREE_FEATURE_NAMES` and `ODDS_FREE_FEATURE_SOURCE_GROUPS` in `services/neural/app/tennis_forecast.py` (26 features: 25 v1 fields plus `match_format_known`). No odds, market prices, or derived market probabilities.
- Surface is excluded. Unknown match format is represented explicitly (`sets_to_win=0`, `match_format_known=0`). Missing serve evidence uses the declared `unknown_serve_history_v1` prior and zero sample weight; this is not source-observed serve data. Match-state fields must be observed and complete. Every source-backed feature must be available by its forecast timestamp.
- Sequence cap: 64 observed states, matching `MAX_SEQUENCE_STATES` in `services/neural/app/independent_tennis_models.py`.
- Latency allowance: 2.0 seconds, from `sports/tennis/tennis.cfg` (`execution_latency_sec`). Evaluation uses execution-time availability and existing strategy freshness rules.

## Splits, development, calibration, and reporting

- Keep the existing whole-match split and four expanding walk-forward folds in the shared manifest. Apply target-specific causal training membership at each fold boundary. Never use a match across train, validation, or holdout.
- Perform model selection only on development folds. Report per-target coverage and both classes, log loss, Brier score, calibration, strategy metrics, latency, and match-clustered uncertainty. A fold with fewer than 100 labelled matches per target is inadmissible, even if its overall event count is large.
- Calibration proposal for an admitted run: fit one sigmoid/Platt calibrator per model and target using only development out-of-fold predictions; freeze it before the sealed holdout is opened. Do not fit a calibrator or choose thresholds on holdout.
- The exact fold membership, target-specific train IDs, calibration inputs, dataset digest, and code/config hashes must be regenerated and recorded by the final full-database audit before any training. This saved manifest predates the optional-serve policy update and is not a final feature-PIT certificate.

## Candidate settings to freeze before an admitted run

- Shared seed: `42`; match-balanced sample weights; CPU/GPU library and deterministic settings recorded in the run artifact.
- LR: `StandardScaler` + `LogisticRegression(solver="lbfgs", C=1.0, max_iter=1000)`.
- GB: `HistGradientBoostingClassifier(learning_rate=0.05, max_iter=80, max_leaf_nodes=15, l2_regularization=1.0)`.
- GRU: existing architecture uses hidden size 64, 64-state sequence cap, static features, and masked target heads. **Fit settings are not implemented/frozen in the repository**; optimizer, learning rate, batch size, epoch limit, early stopping, and deterministic behavior must be implemented and recorded before a GRU run. Do not infer these settings from the architecture.
- The repository currently declares calibration as `uncalibrated_v1`; the calibration proposal above needs implementation and a version bump before calibrated artifacts can be emitted.

## Admission snapshot and next action

The last completed saved-cohort audit currently referenced by the roadmap reports admission `blocked`: point/game/set label coverage is insufficient; source-feature PIT is not certified; causal label availability fails earlier validation cutoffs; some result receipt times are unknown. The fresh read-only full audit was stopped after approximately 17 minutes of database reads without producing artifacts. A bounded full-database audit is still required; regenerate its dataset manifest and provenance together when it completes. Until then this document is a preregistration draft; Phase 46's manifest item remains open.
