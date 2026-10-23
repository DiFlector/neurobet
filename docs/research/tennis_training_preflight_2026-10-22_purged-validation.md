# Odds-free tennis walk-forward purge check

**Derived:** 2026-10-22 02:18 UTC<br>
**Decision:** `blocked`<br>
**Scope:** Re-evaluate causal walk-forward membership on the frozen full audit and provenance; no database rescan and no model training.

## Inputs and outputs

- Source audit: [`tennis_label_audit_2026-10-22_batched.json`](tennis_label_audit_2026-10-22_batched.json), SHA-256 `a3574ed94149379d1a7c2f198385f58e4d1f1c92afe01d3d1a88a7759deb6d16`.
- Example provenance: [`tennis_training_examples_6af68a476573.jsonl`](tennis_training_examples_6af68a476573.jsonl), 4,284 rows, SHA-256 `da3b149086499561ddec1092e32d250eec6ada32cfb7824c5b7d423da541ee4a`.
- Prior manifest v1 was retained as [`tennis_training_manifest_77fcc1b3a797_v1.json`](tennis_training_manifest_77fcc1b3a797_v1.json), SHA-256 `05e79e2bbf497b696dbeb8dde4954ab8c8c4202513b66bb76478d652b1c6e865`.
- Current manifest v2: [`tennis_training_manifest_77fcc1b3a797.json`](tennis_training_manifest_77fcc1b3a797.json), dataset digest `77fcc1b3a7975df3083d32eb28ab9dee0ed8d6c4d621951b3e8e333e3d7454d3`, SHA-256 `cc8c4dce57f5ea84c5903b0f16b361c75cb4e637b84b1c683da97bc1f35f9bd3`.
- Derived gate report: [`tennis_label_audit_2026-10-22_purged_validation.json`](tennis_label_audit_2026-10-22_purged_validation.json), SHA-256 `84e9529cebf7934f690a34848ecb02c6448a3e8a2bacd907b408cfcc8cc9ea9a`.

## Causal training membership

Manifest v2 keeps the original shared whole-match fold boundaries and adds `target_train_event_ids` for each target and fold. A target's training match is eligible only when its latest target-label `available_at` is no later than the earliest labelled validation forecast. Labels with unknown times, late availability, or no validation boundary are excluded from that target's training membership. The minimum remains 500 training matches, 100 matches in each validation window, and 200 holdout matches; no threshold or holdout boundary changed.

| Target | Effective train by fold | Late train labels purged | Validation by fold | Holdout count | Temporal order |
|---|---|---|---|---:|---|
| Point | 0 / 0 / 0 / 0 | 20 / 23 / 26 / 0 | 3 / 3 / 1 / 0 | 8 | Fails all folds |
| Game | 0 / 0 / 0 / 0 | 20 / 23 / 26 / 0 | 3 / 3 / 1 / 0 | 8 | Fails all folds |
| Set | 0 / 0 / 0 / 0 | 20 / 23 / 26 / 0 | 3 / 3 / 1 / 0 | 8 | Fails all folds |
| Match | 1,115 / 1,309 / 1,474 / 1,698 | 37 / 35 / 62 / 30 | 192 / 192 / 192 / 192 | 480 | Passes all four folds |

Match retains both classes in all effective training folds: 563/552, 658/651, 746/728, and 867/831. Strict temporal precedence now passes for match after purging 164 late-available train labels. For point/game/set, existing PBP labels were only available after the historical validation forecasts, so there are no causally eligible train matches; fold four also has no validation labels and therefore no causal training boundary.

## Decision

The overall gate remains `blocked` for missing base target coverage, fixed window minima, and temporal availability for point/game/set. Match now passes its chronology and fixed count gates. Holdout quality metrics were not opened; only the required target counts and class-presence checks were evaluated. No database rescan, training, candidate evaluation, or SHADOW registration occurred in this derived check. The source full audit remains the latest full database audit at 01:57:42 UTC.
