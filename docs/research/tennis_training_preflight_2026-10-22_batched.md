# Independent odds-free tennis admission — batched audit

**Observed:** 2026-10-22 01:57:42 UTC<br>
**Decision:** `blocked`<br>
**Boundary:** No LR/GB/GRU training, development candidate evaluation, holdout quality review, or SHADOW registration.

## Reproducible audit artifacts

- Full read-only report: [`tennis_label_audit_2026-10-22_batched.json`](tennis_label_audit_2026-10-22_batched.json), SHA-256 `a3574ed94149379d1a7c2f198385f58e4d1f1c92afe01d3d1a88a7759deb6d16`.
- Unified manifest: [`tennis_training_manifest_77fcc1b3a797.json`](tennis_training_manifest_77fcc1b3a797.json), 2,400 whole matches (1,920 development / 480 sealed holdout), dataset digest `77fcc1b3a7975df3083d32eb28ab9dee0ed8d6c4d621951b3e8e333e3d7454d3`, file SHA-256 `05e79e2bbf497b696dbeb8dde4954ab8c8c4202513b66bb76478d652b1c6e865`.
- Example provenance: [`tennis_training_examples_6af68a476573.jsonl`](tennis_training_examples_6af68a476573.jsonl), 4,284 rows, file SHA-256 `da3b149086499561ddec1092e32d250eec6ada32cfb7824c5b7d423da541ee4a`, logical digest `6af68a4765731aeaff8d830aa110584eb9e07228bc2c2b7393a6fbafa6652b8f`.

## Admission gates

The fixed requirements remain 500 labelled matches in the initial training window, 100 in each of four validation windows, 200 in holdout, both classes, source-time-valid features, and labels available before each validation forecast. Holdout quality metrics were not opened.

| Target | Labelled on manifest timeline | Train by fold | Validation by fold | Holdout | Result |
|---|---:|---|---|---:|---|
| Point | 35 | 20 / 23 / 26 / 27 | 3 / 3 / 1 / 0 | 8 | Blocked |
| Game | 35 | 20 / 23 / 26 / 27 | 3 / 3 / 1 / 0 | 8 | Blocked |
| Set | 35 | 20 / 23 / 26 / 27 | 3 / 3 / 1 / 0 | 8 | Blocked |
| Match | 2,400 | 1,152 / 1,344 / 1,536 / 1,728 | 192 / 192 / 192 / 192 | 480 | Counts pass; temporal precedence fails |

All four validation folds fail temporal precedence for every target with labelled training data: latest training label availability is later than the earliest validation forecast. The point/game/set targets are also far below every fixed count gate.

## Label availability and feature PIT clarification

The manifest contains 2,400 matches. Of eligible events, 143 have unknown result availability and are excluded; 2,293 results use an exact previously observed final-score snapshot and 107 use official result metadata. No scheduled start or mutable `finished_at` was used as a substitute. `chronology_timestamp_missing_matches=182` is reported separately from result availability certification.

The report says `point_in_time_certified=true` for 4,284/4,284 examples under `tennis_odds_free_v1` contract semantics: documented priors and explicit unknowns are permitted when represented by the contract, while source-backed values must be available by the forecast snapshot. This is not full source-observed coverage: all 4,284 examples are contract-defaulted and zero are fully source-observed. Player metrics were available in 142 examples; serve metrics and surface were contract defaults throughout. Therefore the PIT flag is consistent with the implemented contract but does not establish empirical coverage or validate the defaults as adequate training features.

For point/game/set, only 138 / 24 / 2 examples respectively are observed; most parsed PBP labels are retrospective (4,691 / 867 / 95 examples respectively). They cannot be treated as online forecast examples.

## Decision and remaining gates

Admission remains `blocked` for `base_target_coverage_or_both_classes_missing`, `walk_forward_or_holdout_minimums_not_met`, and `training_labels_not_available_before_validation_forecasts`. Continue source collection, identity and PBP confirmation, and causal feature coverage work without lowering thresholds. Manual verification of original PBP documents, full queue/import and throughput gates, and injected I/O-stall recovery remain open in the roadmap. No training or registry changes were made.

The live PBP check at 02:06:59 UTC returned 19 live tennis events: 15 were `waiting_players`, four were `retry`, none had both player profiles available, and none exposed a confirmed usable point sequence. Four API `point_history` slots contained retry objects without a sequence or event count; they were not accepted as PBP. Snapshot: [`tennis_live_pbp_queue_snapshot_2026-10-22_0207.json`](tennis_live_pbp_queue_snapshot_2026-10-22_0207.json).
