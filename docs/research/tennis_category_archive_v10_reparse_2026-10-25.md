# Flashscore category correction and archive replay — 2026-10-25

The original category census was read-only. Follow-up replay used archived Flashscore profile result feeds and parser `flashscore-tennis-player-v10`; the prior observation versions and timestamps remain intact. Twelve M15 page objects were recovered directly from MinIO by the stored source content hash after finding that their profile jobs had no completed-job pointer.

| Group | Source rows | Reprocessed/corrected | Result |
|---|---:|---:|---|
| M15 Monastir gender mismatches | 55 | 55 | Saved source feeds explicitly say `M15 Monastir 13 (Tunisia)`; v10 parsed all 55 as male ITF singles. Their production projections and latest observations now use v10. |
| ATP Cup unknown gender | 15 | 15 | Archived feeds explicitly say `ATP Cup (Australia)` and contain the corresponding doubles rows. They are excluded from the singles result parser; a new v10 classification observation records male ATP without changing their match score or participants. |
| M15 rows recovered outside completed-job pointers | 12 | 12 | Exact content-addressed MinIO profile responses were found from each saved observation's date, profile ID, and source content hash, then reparsed with v10. |

The post-replay check found all 70 target rows with `gender=male` and parser v10 on both the current projection and latest observation; no target mismatch remains. Re-running the apply path appended zero extra ATP Cup versions, confirming idempotence. The machine report lists each reviewed match ID, archived tournament and participants, profile URL, and object-storage document path: [`tennis_category_archive_v10_reparse_2026-10-25.json`](tennis_category_archive_v10_reparse_2026-10-25.json).

Junior PBP continues through the existing source-only Flashscore lane, without Fonbet links. All 23 real Flashscore junior matches from seasons 2022–2024 have durable source-keyed PBP jobs, using the saved match IDs and player IDs. The source lane is paused until 2026-10-25 11:04 UTC, so the worker has not fetched them yet; there are still zero PBP documents or versions for this cohort. Queue and URL provenance: [`tennis_junior_pbp_source_queue_2026-10-25.json`](tennis_junior_pbp_source_queue_2026-10-25.json). Keep this item open until the jobs return actual archived point snapshots and their quality is checked.
