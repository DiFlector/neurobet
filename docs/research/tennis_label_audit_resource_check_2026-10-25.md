# Point-in-time label audit resource check

Cutoff: 2026-10-25 15:23 UTC. No training, fitting, holdout-quality assessment, or model-registry operation ran.

## Work performed

- The audit loaded all historical metric snapshots and serving logs into Python. It also retained each latest point-history ORM row, including its complete JSON tape, while scanning history.
- `scripts/research/tennis_label_audit.py` now scopes metric/profile lookups to participants in eligible events (and participants sharing their available confirmed source IDs), streams large ORM result sets, and filters retained serve logs to audit participant names. Its production path now skips the non-gating corpus-wide point JSON scan and marks those global corpus counters unavailable; linked-match counts used for admission remain exact.
- `py_compile` passed; `tests/research/test_tennis_label_audit.py` plus API-Tennis archive restore tests: 32 passed.

## Bounded production read attempt

The updated audit was started as a separate read-only process with 1.5 GiB memory and 1 CPU limits. Python stayed below 706 MiB, but it reached a full-table stream of `tennis_point_history_versions`; PostgreSQL rose from 1.83 GiB to 2.30 GiB of its 2.5 GiB container limit during the read. The audit was stopped before it produced a manifest. The database returned to 2.21 GiB after stopping; all services stayed healthy and host available memory was 7.0 GiB.

The read attempt reached the whole archived PBP history for a corpus-wide confirmed-outcome count that does not affect admission membership. That global scan has now been removed from the production path; non-gating corpus counters are explicitly unavailable until a copy-database audit. No second production audit has been started because PostgreSQL remains near 90% of its container memory limit.

## Admission evidence still in force

The latest completed feature-source audit remains `tennis_feature_source_completeness_2026-10-25.json`: 4,826 examples, PIT certified 0; source time for event format missing on all 4,826; serve metrics defaulted for both sides on all 4,826. The set target has 91 examples, below the 500-match initial-train requirement. These are data/provenance blockers; they cannot be fixed by changing the training code or relaxing thresholds.
