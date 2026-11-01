# Tennis admission gate recheck — 2026-10-26

Read-only production snapshot generated at 02:04:56 UTC and completed at 02:26 UTC. It used a repeatable-read transaction marked `READ ONLY`, `statement_timeout=300s`, `work_mem=4MB`, and an audit container limited to 0.5 CPU / 1,280 MiB. Training, fitting, tuning, holdout quality evaluation, registry changes, and SHADOW registration were not run.

## Artifacts

- Audit JSON: [`tennis_label_audit_2026-10-26_recheck.json`](tennis_label_audit_2026-10-26_recheck.json), SHA-256 `396bdb3d824afb98ba36c54ff11ef70fd966620d9742e6faa863702d9ce7e164`.
- Manifest: [`tennis_training_manifest_6a61183b986e.json`](tennis_training_manifest_6a61183b986e.json), SHA-256 `29d7e2fa1a1e27233d44b6bdac12f4b57f629f9d5e667035e466327d7c11d43d`.
- Provenance JSONL: [`tennis_training_examples_997715d7d6df.jsonl`](tennis_training_examples_997715d7d6df.jsonl), 7,711 rows, SHA-256 `62450776eb01806fe33715fa27b3729cfcdbb6e5827b4444970d375e3b505b5a`.

## Admission counts

The shared manifest has 3,111 whole matches: 2,488 development and 623 holdout. `holdout_quality_metrics_opened=false`. Result availability for the included manifest rows is certified from 2,834 official Fonbet result metadata records and 277 exact previously observed final-score snapshots; 143 other valid results remain undated and excluded.

Feature PIT remains **0/7,711**. Player metrics have source observations in 491 examples; serve A/B in 9/6; event state in all 7,711. `event_format` is missing or unverifiable in all 7,711, and surface is contract-default `unknown` in all 7,711.

| Target | Labelled matches | A/B examples | Causal train matches, folds 1–4 | Validation matches, folds 1–4 | Holdout matches | Temporal precedence, folds 1–4 |
|---|---:|---:|---|---|---:|---|
| Point | 81 | 1,362 / 1,368 | 0 / 0 / 0 / 7 | 4 / 2 / 9 / 12 | 12 | fail / fail / fail / pass |
| Game | 82 | 835 / 839 | 0 / 0 / 0 / 7 | 4 / 3 / 9 / 12 | 12 | fail / fail / fail / pass |
| Set | 82 | 91 / 90 | 0 / 0 / 0 / 7 | 4 / 3 / 9 / 12 | 12 | fail / fail / fail / pass |
| Match | 3,126 | 1,607 / 1,519 | 0 / 0 / 0 / 277 | 249 / 249 / 249 / 249 | 623 | fail / fail / fail / pass |

Required minima remain train ≥500, each validation ≥100, holdout ≥200, both classes, feature PIT, and temporal precedence. Point/game/set fail their labelled-match, train, validation, and holdout minima. Match has sufficient validation and holdout counts but only 277 causal train matches; its first three folds fail precedence. Every target fails feature PIT. The gate is `blocked` with the same four reasons as the preceding audit: insufficient target coverage, unmet walk-forward/holdout minimums, labels unavailable before validation forecasts, and uncertified odds-free feature availability.

Compared with the completed 01:50 UTC audit, the manifest gained one whole match, match labels increased from 3,125 to 3,126, and examples increased from 7,710 to 7,711. Point/game/set counts and PIT certification did not change. This does not satisfy any previously failing minimum.

## Decision

This checklist item cannot be marked complete: the required gate did not pass. Keep training blocked and preserve all thresholds. The next admissible recheck needs more causally available point/game/set labels, at least 500 causal training matches and 100 in each validation window for every target, 200 holdout matches for every target, and source-backed PIT features including event format. Do not open holdout quality to resolve these count checks.
