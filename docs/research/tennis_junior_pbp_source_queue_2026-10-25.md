# Junior Flashscore PBP queue — 2026-10-25

At 2026-10-25 09:36 UTC, the 23 stored Flashscore junior singles matches (Pardubická juniorka, seasons 2022–2024) had no point-history documents, history versions, or existing source refresh jobs. Their match IDs, names, and player IDs come from stored Flashscore result observations. Canonical source URLs use the existing `_match_page_url` helper and pass `_source_match_summary_url` validation.

All 23 were inserted into `tennis_point_source_refresh_jobs` through the existing `_enqueue_source_point_refresh` helper. No Fonbet links were created. The Flashscore PBP source gate was enabled, but the shared source pause was active until `2026-10-25T11:04:19Z`; jobs remain queued for the normal worker after cooldown.

Production verification: 23 queued jobs, 0 point documents, 0 point-history versions, and 0 Fonbet links for these IDs. Queue and per-match URL provenance are recorded in [`tennis_junior_pbp_source_queue_2026-10-25.json`](tennis_junior_pbp_source_queue_2026-10-25.json). Keep junior PBP coverage open until source responses arrive and their parsed sequences are checked.
