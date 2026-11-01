# Tennis PIT dataset materialization and admission recheck — 2026-10-26

Read-only admission audit cutoff: **2026-10-26 12:07:51 UTC**. The audit read production in a separate container capped at 0.5 CPU and 1.8 GiB; PostgreSQL remained below 1.9/2.5 GiB. The database was not modified. No fitting, candidate evaluation, holdout-quality review, registry change, SHADOW registration, or model training ran.

## Engineering change

The earlier audit JSONL recorded feature availability and provenance but omitted feature values. The standalone candidate builder therefore rejected rows before it could validate an actual vector. The audit now materializes the ordered `tennis_odds_free_v1` values from the event snapshot and source observations selected at that snapshot's cutoff. Unknown or late `best_of` is left absent; the downstream builder rejects it. Priors and unknown surface remain identified as defaults and do not count as source-observed PIT coverage.

Focused Research checks: **35 passed** (`test_tennis_label_audit.py` and `test_odds_free_dataset.py`).

## Current admission evidence

- Audit examples: **7,795**, feature values materialized for **7,795**.
- Feature PIT: **0/7,795 certified**. `event_format` has no causal value/timestamp for any row. Surface is unknown for all rows; player metrics are defaulted in 7,296 rows; serve inputs are defaulted in 7,780/7,786 rows by side.
- Labelled matches: point **81**, game **82**, set **82**, match **3,210**. Point/game/set remain below the 500-match initial-train minimum.
- Temporal folds: point/game/set causal train matches are **0/0/0/23**; match **0/0/0/277**. First three folds have no causally eligible training examples. Point/game/set validation counts are below 100 in every fold; holdout has 8 per target. Match validation and holdout counts meet the numeric size minima, but chronology and PIT fail.
- The chronological manifest contains **3,195** matches (2,556 development / 639 sealed holdout). Included result timestamps are certified. **178** eligible results have no chronology timestamp and are excluded. Holdout quality metrics remain sealed.
- The independent dataset builder accepted **0/7,795** and rejected all rows. Main reasons: missing PIT event format (7,795), contract-default event surface (7,795), unobserved player/serve metrics, and 7,795 missing `sets_to_win` values. A further 51 malformed/incomplete score snapshots lack state fields.

Gate remains `blocked` on coverage, walk-forward minimums, temporal precedence, and feature PIT. The immutable numeric thresholds remain train ≥500, each validation ≥100, holdout ≥200, both classes, PIT clean, and causal chronology. No training action is allowed until a later audit passes every gate.

## Artifacts

- Machine audit: [`tennis_label_audit_2026-10-26_materialization_recheck.json`](tennis_label_audit_2026-10-26_materialization_recheck.json), SHA-256 `9ac83184640de6b368d8c0e8f3b3fdd079e828b23595a780d1c3ec5267bc6bc5`.
- Manifest: [`tennis_training_manifest_8447ea4e509b.json`](tennis_training_manifest_8447ea4e509b.json), dataset digest `8447ea4e509bfe3225e06a8b32dae57c77364dfde77173d3054c8e1c4651d858`, SHA-256 `f49f95394c6ba8ceba8a92feaaa158884e0348d763515f5cd1c288c1348ebae3`.
- Materialized provenance: [`tennis_training_examples_77927850ad23.jsonl`](tennis_training_examples_77927850ad23.jsonl), 7,795 examples, SHA-256 `2d59c48ffdceb2334b1a799db39ca5129a0cdf7576294c7f8a88107b0abd9c8d`.
- Candidate rejection audit: [`tennis_odds_free_dataset_2026-10-26_materialization_recheck.jsonl.gz.gz`](tennis_odds_free_dataset_2026-10-26_materialization_recheck.jsonl.gz.gz), SHA-256 `11f517142a93571712e1e1db44d07694000f1056384671c5105d51990d2d8360`; summary [`tennis_odds_free_dataset_2026-10-26_materialization_recheck.jsonl.summary.json`](tennis_odds_free_dataset_2026-10-26_materialization_recheck.jsonl.summary.json), SHA-256 `0160121752757b8a4c3c717540265e8f658305b38c6c9ab549c898d257e29a78`.

Next admission attempt requires new source-timestamped event format and additional non-retrospective observations. Historical values must not be backdated, inferred from competition/gender/score, or used to relax the gate.
