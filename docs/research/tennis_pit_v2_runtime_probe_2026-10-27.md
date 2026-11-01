# Odds-free v2 runtime readiness probe — 2026-10-27

Checked the production `tennis_forecast_snapshots` table at 11:46 UTC after updating and restarting Neural and Research containers.

- Last 24 hours: 10,736 saved forecast snapshots.
- Readiness payloads using `tennis_odds_free_v2`: 52 snapshots across 46 events.
- Certified snapshots: 2. The remaining checked v2 snapshots are incomplete; observed reasons include unconfirmed player metrics and missing observed serve denominators for both participants.
- Newest saved snapshot at query time: 11:07 UTC. No newer snapshot had appeared after the service restart, so this probe does not verify a post-restart forecast write.
- Unknown match format is represented as `match_format_known=false`; it is not among the rejection reasons. Surface is absent from the v2 readiness requirements.
- Both Neural and Research containers reported healthy after restart, and the updated modules imported successfully.

The v2 readiness record count is not the same as the total snapshot count: most historical snapshots have no v2 readiness payload. Two certified snapshots confirm that some saved examples can pass the revised feature-PIT checks; they do not establish that all new matches pass or satisfy training admission.

The full Docker rebuild did not finish because storage I/O remained heavily loaded. Updated modules were copied into and restarted in the current containers. A commit to durable local images stalled and was stopped; recreate-from-image deployment therefore remains unverified.
