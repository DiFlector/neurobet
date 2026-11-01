# Tennis category and snapshot audit — 2026-10-25

Read-only production census generated at `2026-10-25T08:52:59Z`. Machine-readable counts and mismatch IDs are in [`tennis_category_snapshot_audit_2026-10-25.json`](tennis_category_snapshot_audit_2026-10-25.json), SHA-256 `30ac4e197c5ac16e57757832a1e15e708279c31278d1c02cdfd012f6b93718d4`. The audit did not change production rows.

## Coverage

- The Flashscore materialization contains 104,077 unique match rows: 51,147 ITF, 23 junior, 17,689 Challenger, 16,058 WTA, 16,793 ATP and 2,367 unknown. ITF is represented in seasons 2011–2026; junior only in 2022–2024.
- Of 4,344 stored Fonbet tennis events, 3,141 have numeric IDs and look like singles. Across the full event set, 378 categories are unknown and 78 are classified ITF; none are currently classified junior.
- Linked point-history snapshots: ITF 78 source matches / 2,683 versions (120 observed, 2,563 retrospective); WTA 52 matches / 533 versions (406 observed, 127 retrospective); ATP 32 / 266 (203 observed, 63 retrospective); Challenger 21 / 180 (111 observed, 69 retrospective). No junior PBP versions are linked. The 229,374 unmatched API-Tennis/Match Charting Project versions are retrospective and remain in the unknown category; they are not Fonbet-linked training coverage.

## Category consistency checks

On rows whose tournament string contains an explicit category marker, the stored tour category agrees for 47,017 of 47,032 rows (99.968%). The 15 disagreements are all ATP Cup rows stored as `atp_wta` with unknown gender. On the explicit-gender subset, 46,936 of 47,006 rows agree (99.851%); the remaining 70 are 55 M15 Monastir rows stored as female and the same 15 ATP Cup rows with unknown gender.

These are source-string consistency checks, not independent certification against official tournament pages. The raw audit preserves the source match IDs and the mismatching rows for follow-up. A human review of archived source pages remains necessary to certify category accuracy.

## Parser correction and remaining work

Flashscore parser v10 now recognizes junior markers (`J30/J60/J100/J200/J300/J500`, `U14/U16/U18`, boys/girls and junior labels), ITF M/W event codes as gender evidence, and whole-word ATP/WTA/men/women markers. This also prevents a place name such as “Menorca” from being treated as “men”. The label audit uses the same explicit junior markers and lets a clear tournament label disambiguate an `atp_wta` row with missing gender.

Existing observations are immutable; the 55 M15 and 15 ATP Cup materialized rows were not rewritten. They need a parser-v10 source refresh/reparse. Category completeness also remains insufficient: 2,367 Flashscore rows and 378 Fonbet event categories are unknown, and junior point-history coverage is zero. Therefore the roadmap item remains open.
