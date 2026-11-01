# API-Tennis serve denominator materialization — 2026-10-26

## Change

The API-Tennis importer already retained explicit match-level `Service Points Won` numerator and denominator in immutable raw observations, but did not project them into `ext_tennis_serve_logs`, the table consumed by PIT feature snapshots. Added validated denominator extraction and persistence for completed matches. A row is written only when the API-Tennis player ID maps through a confirmed canonical alias to exactly one player profile. The row retains the source observation's `available_at`, event time, explicit surface when present, source provenance, and `retrospective=true`; unknown surface remains unknown. Existing rows are not rewritten.

Added an offline bounded replay utility at [`scripts/research/materialize_api_tennis_serve_logs.py`](../../scripts/research/materialize_api_tennis_serve_logs.py). It reads saved observations only, defaults to read-only, and requires `--commit` to persist. The running `external-tennis-sources` image includes the change.

## Verification

- Bounded read-only inventory scanned the newest 1,000 saved API-Tennis observations: 210 completed observations had explicit valid serve-point denominators. Only confirmed, unambiguous identity links were eligible for materialization.
- The bounded offline commit inserted 2 rows from 2 source matches. A repeatable read-only inventory reports 2 rows, both retrospective, with source `available_at=2026-10-26 00:14:35.689323+00`.
- A rollback-only database insert probe passed before the write. No provider/network requests were made by the offline replay.
- `tests/research/test_external_tennis_sources.py`: 47 passed in the research container.
- The `external-tennis-sources` container is healthy on image `sha256:ad32699b47bba1eb5b41d707934269a848a2ee3c073a26748d328f9cd02672d7`; image ID matches the running container. `git diff --check` passed.

## Admission impact and remaining blockers

This is a source-ingestion repair, not admission evidence for earlier forecast cutoffs. The 2 observations were fetched on 2026-10-26 for matches played earlier; their availability time is not backdated, so they cannot certify historical snapshots. The latest completed admission audit still reports feature PIT 0/7,711, no verifiable source-time `event_format` for 7,711 examples, and `surface=unknown` for all 7,711. It reports only 43 unique matches with complete non-retrospective PBP; point/game/set labels remain 81/82/82 matches and fail the fixed admission minima and early-fold temporal checks. The latest PBP funnel has only 9/9/7 non-retrospective target events for point/game/set.

Therefore this fixes denominator loss for newly captured eligible observations, but the roadmap admission item remains open. Do not train, lower thresholds, infer format/surface, or present these two historical-match rows as PIT-certified features. Re-run the bounded source inventory and admission audit only after enough fresh, causally available source observations and PBP have accumulated and event format is obtained with source availability evidence.
