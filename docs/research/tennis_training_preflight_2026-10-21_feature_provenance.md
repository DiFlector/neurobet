# Tennis independent-model preflight — 2026-10-21 14:05 UTC

**Decision: blocked.** No working LR, boosting, or GRU candidate was trained. Holdout quality metrics remain unopened.

## Artifacts

- Audit: [`tennis_label_audit_2026-10-21_feature_provenance.json`](tennis_label_audit_2026-10-21_feature_provenance.json)
- Whole-match manifest: [`tennis_training_manifest_95779924731d.json`](tennis_training_manifest_95779924731d.json)
- Example provenance: `training_examples_manifest` inside the audit JSON
- Feature contract: `tennis_odds_free_v1`

The common timeline contains 2,013 matches: 1,610 development and 403 closed holdout. Targets are masked inside this shared split; a row without a target does not count toward that target's minimum.

## Label coverage

| Target | Unique labelled matches | Aligned examples | Class A / B | Observed / retrospective |
|---|---:|---:|---:|---:|
| Point | 31 | 1,009 | 502 / 507 | 0 / 3,822 parsed outcomes |
| Game | 31 | 596 | 308 / 288 | 0 / 705 parsed outcomes |
| Set | 31 | 67 | 35 / 32 | 0 / 76 parsed outcomes |
| Match | 2,305 | 2,305 | 1,187 / 1,118 | not applicable |

The 2,499 eligible event count is broader than the dated, valid labels. There are 484 eligible events without a proven result-availability timestamp; 420 otherwise valid match outcomes are excluded from the chronological manifest. `scheduled_start_at` and `finished_at` were not substituted.

## Shared chronological gate

Point, game, and set each have 22 labelled matches on the common manifest timeline: initial train 9; validation folds 3, 1, 2, 1; holdout 6. They fail the fixed minima (500 / 100 per validation / 200 holdout).

Match has 2,013 labels on the timeline: train 966; validation 161 in each fold; holdout 403. Its sizes and both classes pass, but **all four** folds fail temporal precedence: the latest training-result availability is 2026-10-21, after validation forecasts from 2026-10-12 through 2026-10-14.

## Feature provenance

All 3,977 emitted examples pass the causal-input check under the exact contract policy. This does **not** mean the inputs were broadly observed:

- Both players' metrics were source-backed together for only 63 examples; the other examples used the explicit prior/missingness contract.
- Serve metrics used the explicit `0.62` prior and zero sample weight for both sides in all 3,977 examples. No exact full-name serve-log match was safe to use.
- Surface used `unknown` in all 3,977 examples because source metadata has no separate availability timestamp.
- Match state came from the exact saved Fonbet snapshot for each example.

Inference now accepts a surface only when `surface_available_at <= cutoff`; otherwise it uses `unknown`. The saved forecast fingerprint includes the full odds-free feature vector, so a feature change cannot be hidden by an unchanged score. Audit resolution counters distinguish observed inputs, contract defaults, and unverifiable inputs. No default is counted as observed sports coverage.

## Next work

1. Continue historical PBP collection until point/game/set targets can meet the fixed per-window minima with both classes.
2. Capture real live-observed PBP; current observed PBP count is zero.
3. Preserve result availability when first observed. Today's result refreshes cannot be backdated to match dates; wait for a forward-collected chronological cohort or another source with actual historical observation records.
4. Add point-in-time player history/identity and serving-volume evidence where it exists. Keep `unknown`, missingness flags, and priors when it does not.
5. Rebuild the common manifest after each data/parser change. Only consider training once every target passes label counts, both-class checks, source-time checks, and train-label-before-validation-forecast checks.
