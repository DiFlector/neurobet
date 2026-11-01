# Tennis match-format PIT recheck — 2026-10-26

Read-only production check at 2026-10-26 00:54:34 UTC. The 100 most recently observed Fonbet tennis events contain no `best_of`, `best_of_available_at`, or `event_format` metadata (0/100 for each field).

The two current live Fonbet events also have no format metadata. Neither has a confirmed player-identity link, a confirmed Flashscore match link, or a player metric snapshot, so neither is a valid end-to-end forecast target. No synthetic values were inserted and no forecast/training was started.

The last saved raw-source audit inspected 50 content-addressed Fonbet payloads / 3,079 event rows and found 0 explicit `best_of` rows. Its SHA-256 is `d2c6313309b53041e2e42b360be2a2b0123b2a0cf7bd54ffece397249ec9462c`; artifact: [`tennis_format_raw_live_source_audit_2026-10-22_1540.json`](tennis_format_raw_live_source_audit_2026-10-22_1540.json). The forecast contract continues to require an explicit supported format and `best_of_available_at <= cutoff`; without that evidence the result remains `UNSUPPORTED_FORMAT`.

**Decision:** keep the roadmap item open. The source still provides no PIT-certified match format, and the live events lack the player identity/metric prerequisites for a real forecast.

Machine-readable census: [`tennis_format_pit_recheck_2026-10-26.json`](tennis_format_pit_recheck_2026-10-26.json).
