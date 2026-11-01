# PIT v2 historical re-audit — 2026-10-27

## Scope and result

Revalidated the saved 7,792-example cohort against current PostgreSQL rows using the exact snapshot index. This was not a full database census. All 6,429 indexed snapshot keys were found for 3,207 events; no indexed snapshot was missing.

The strict `tennis_odds_free_v2` builder accepted **5/7,792 examples**, each a distinct match example. The feature audit also certified 5 examples. The remaining 7,787 are rejected. No training, holdout-quality evaluation, SHADOW, or promotion was run.

## V2 treatment

- **Format:** all 7,792 examples had no timely explicit best-of observation. They are represented as unknown (`sets_to_win=0`, `match_format_known=0`) and are not rejected for format.
- **Surface:** removed from v2 features and PIT checks.
- **Match state:** a saved Fonbet state snapshot is available for all 7,792. Fifty examples lack one or more required numeric score fields (sets, games, points, server, or tiebreak flag).
- **Player metrics:** 1,300 examples have source-observed metrics; 6,492 use disallowed contract priors. Evidence is missing for 5,698.
- **Serve metrics:** source evidence is available for 20 examples on side A and 13 on side B. Evidence is missing for 7,772 and 7,779 respectively.
- **Labels:** 283 confirmed match labels lack a proven receipt time. They remain PIT/evaluation candidates but are purged from each applicable training fold. The builder records receipt-time presence separately; fold eligibility is determined by the audit for each validation boundary. No scheduled time or replay/import time was substituted.

## Chronology and admission

The shared manifest contains 3,207 matches, ordered by first forecast snapshot time. Match validation windows contain 256, 257, 257, and 256 matches; the 642-match holdout meets its size and both-class counts, but holdout quality was not opened. Chronology purges leave 9, 9, 9, and 1,049 match training examples across the four expanding folds. The first three folds miss the 500-match training minimum. Point, game, and set targets also remain below their per-window coverage requirements. Overall training admission remains **blocked**.

Across match folds, 283 unknown-time labels are purged from training (1,132 fold-level exclusions); labels with known time are admitted only if available by that fold's first validation forecast.

## Artifacts

- Audit JSON: [`tennis_pit_v2_historical_7792_2026-10-27.json`](tennis_pit_v2_historical_7792_2026-10-27.json), SHA-256 `21366e6cbe4200f6630a067e36b1de88b3398552b6fb2248614a3d5c196b5904`.
- Builder JSONL: [`tennis_pit_v2_builder_7792_2026-10-27.jsonl.gz.gz`](tennis_pit_v2_builder_7792_2026-10-27.jsonl.gz.gz), SHA-256 `2c890e466577d27e59fd9c233b01acb12aef00862f5f15fa7636e699fd23185c`; summary is beside it.
- Training-example provenance JSONL: [`tennis_training_examples_10df6b1408b3.jsonl`](tennis_training_examples_10df6b1408b3.jsonl), SHA-256 `b1b8a917f9330b7bf46ba5ee7e0a0b07f77bbbb63cb6b44f196104a33ae4971e`.
- Snapshot index SHA-256: `9200df3b5c52ebf47017897365627133c8483757d2a32e1e9593d623c69dc722`.
- Walk-forward manifest: [`tennis_pit_v2_manifest_2026-10-27.json`](tennis_pit_v2_manifest_2026-10-27.json), SHA-256 `f7980834d3292267a5e4d6f2585f72873854082af40ae4d327f95505bf2a841d`.

The saved cohort result is complete for this index only. It does not establish current automatic-snapshot coverage; that is checked separately after deploying v2.

## Follow-up: missing match-state fields and unknown label times

The optional-serve v2 builder recheck accepted 7,742/7,792 examples for feature PIT and rejected the remaining 50 because the saved match-state row lacks required numeric fields: sets, games, points, server, and tiebreak state. These are 50 distinct matches, not duplicate rows. Their state evidence has no raw object key or content hash, so the state cannot be reconstructed from the preserved source. The rows remain excluded from the builder output; no score was inferred from the final label or later point history.

Of the 7,792 examples, 283 have no proven label receipt time. The walk-forward audit purges those labels from training at every applicable fold boundary (1,132 fold-level exclusions across the match target); it does not substitute schedule time or import time. The audit's stricter result-chronology manifest separately excludes 15 matches for which neither official result availability nor an exact previously observed final-score timestamp is available. These chronology rules do not open training admission: the gate remains blocked on feature PIT and target/window coverage.
