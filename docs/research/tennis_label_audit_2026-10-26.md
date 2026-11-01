# Tennis pre-training admission audit — 2026-10-26

Read-only production audit completed at 2026-10-26 01:50 UTC. Database access used a repeatable-read, read-only transaction with a 300-second statement timeout and 4 MiB `work_mem`; audit container was capped at 0.5 CPU and 1,280 MiB. PostgreSQL stayed below 1.6/2.5 GiB and platform services remained healthy. No training, fitting, hyperparameter search, holdout quality evaluation, registry change, or SHADOW registration was run.

## Artifacts

- Audit JSON: [`tennis_label_audit_2026-10-26.json`](tennis_label_audit_2026-10-26.json), SHA-256 `94a2351b6de6122e84fcf4a7d1bf41de2c344335ad4b2a8f5aed204438ccb2aa`.
- Manifest: [`tennis_training_manifest_4d7e7183ea6a.json`](tennis_training_manifest_4d7e7183ea6a.json), SHA-256 `0abb00a085be1e5f21adb7931131e7dde85922392ca3632cb63c780974e76fd2`.
- Provenance examples: [`tennis_training_examples_90c7a14a100f.jsonl`](tennis_training_examples_90c7a14a100f.jsonl), 7,710 rows, SHA-256 `c4d05ef82b4c853fb86c488a5a799a219a5134706066c125f1178ca222c054c2`.

## Feature availability

The audit evaluated 7,710 examples against `tennis_odds_free_v1`; **0/7,710** were PIT-certified and none were fully source-observed. Source availability was:

| Feature group | Source available | Contract prior/default | Missing or not verifiable |
|---|---:|---:|---:|
| Player metrics | 491 examples | 7,219 | 0 |
| Serve, side A | 9 | 7,701 | 0 |
| Serve, side B | 6 | 7,704 | 0 |
| Event state | 7,710 | 0 | 0 |
| Event format | 0 | 0 | 7,710 |
| Surface | 0 | 7,710 (`unknown`) | 0 |

Every example is uncertified because event format has no usable source value and own availability timestamp. Defaults remain explicitly identified and do not count as source coverage. This audit does not infer format, surface, or serve values from retrospective data.

## Labels and manifest gates

The new shared chronological manifest contains 3,110 whole matches: 2,488 development and 622 holdout. The holdout partition is sealed for quality evaluation (`holdout_quality_metrics_opened=false`). Result-time evidence for included examples uses 2,833 official result metadata records and 277 exact previously observed final-score snapshots; 143 results have unavailable time and are not certified.

| Target | Labelled matches | Labelled examples | A/B examples | Walk-forward train matches (4 folds) | Validation matches (4 folds) | Holdout matches |
|---|---:|---:|---:|---|---|---:|
| Point | 81 | 2,730 | 1,362 / 1,368 | 0 / 0 / 0 / 7 | 4 / 2 / 9 / 12 | 12 |
| Game | 82 | 1,674 | 835 / 839 | 0 / 0 / 0 / 7 | 4 / 3 / 9 / 12 | 12 |
| Set | 82 | 181 | 91 / 90 | 0 / 0 / 0 / 7 | 4 / 3 / 9 / 12 | 12 |
| Match | 3,125 | 3,125 | 1,606 / 1,519 | 0 / 0 / 0 / 277 | 249 / 249 / 249 / 249 | 622 |

Required minima are train ≥500 per target, each validation window ≥100, holdout ≥200, both classes, feature PIT, and label temporal precedence. The audit therefore does **not** pass: point/game/set fail the coverage minima; match has enough total labels/holdout but its first three training folds are purged because labels were not available before the validation forecast cutoffs. Only fold 4 passes temporal precedence for each target. All targets fail overall feature PIT. The audit gate is `blocked` with reasons `base_target_coverage_or_both_classes_missing`, `walk_forward_or_holdout_minimums_not_met`, `training_labels_not_available_before_validation_forecasts`, and `odds_free_feature_availability_not_certified`.

## Actual trainer builder

The existing `services/neural/app/dataset.py` builds the legacy priced-snapshot dataset (`Event`, odds and state snapshots) for market models. The independent odds-free inference/model primitives accept caller-supplied feature rows, but this audit found no production training loader that materializes and certifies `tennis_odds_free_v1` rows through the actual trainer path. Thus the actual odds-free dataset-builder certification remains open; the audit JSONL is provenance evidence, not a certified trainer matrix.

## Queue snapshot

Read-only status at 2026-10-26 02:00 UTC:

- Profile refresh participant `ab47f470-6cc2-4e6b-8bdb-75201cebff91`: `queued`, 37 attempts, no last error; last update/next-run timestamp is 01:53:13 UTC. Successful completion is not confirmed.
- Fonbet event `69171568` PBP refresh: its scheduled retry elapsed, but at 02:00 UTC the job remained `queued` at 72 attempts, retaining `point_history_tab_missing`; it had not been claimed. Worker logs show dispatch activity for other jobs. Successful completion is not confirmed.
- The previously reviewed Flashscore profile `AROuX8tf` had 280/281 advertised singles imported; the saved point-history document was retrospective and not linked to Fonbet. CAPTCHA resolution did not establish successful completion of these target jobs.

No production queue or identity rows were modified. These live jobs still require a successful terminal result before their roadmap item can be closed.

## Conclusion

The manifest rebuild and gate evaluation are complete, but the numeric, PIT, and temporal admission criteria are not satisfied. Training remains prohibited by the existing gate. Required next work is to obtain source-timestamped format/serve/surface data, expand usable point/game/set labels with causal availability, implement or connect the odds-free trainer dataset builder and certify its actual rows, and then rerun this same audit. Do not lower thresholds or open holdout quality to do so.
