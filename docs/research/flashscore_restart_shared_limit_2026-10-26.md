# Flashscore queue restart and shared network limit — 2026-10-26

## Restart recovery

At 01:00:26 UTC, the durable Flashscore queue had one running point-history job for Fonbet event `69171568` (already finished; attempt 70, `point_history_tab_missing`) and one running historical profile backfill (attempt 32). The shared source gate was configured for two slots, with no cooldown.

I restarted only `flashscore-worker` using the normal Compose restart. It stopped cleanly at 01:00:36 and started at 01:00:45. The worker became healthy, reported its expected lanes (two browser processes, one live PBP worker, separate current/backlog lanes), and resumed dispatch. The point job returned to `retry` with its error retained and lease/token cleared; the profile job returned to `queued` with its attempt count/checkpoint retained and lease/token cleared. Subsequent worker logs showed new HTTP PBP requests returning 200 and dispatch continuing. This verifies durable queue recovery after a normal restart; it does not show that the terminal `point_history_tab_missing` job completed.

## Shared limit and live contention

The source controller remained at two slots with no cooldown. Code assigns priorities `live=0`, `participant=10`, `archive=20`, `background_profile=30`; an archive waiter ages to effective priority 5 after ten seconds. Successful requests continued after restart (controller counter 6,733 to 6,774 during the observed dispatch interval).

A real latency comparison was not possible: the two fresh Fonbet live events had `PLAYER_SOURCE_IDS_MISSING` and both PBP jobs remained `waiting_players`. No live source request was eligible while archive/background work ran. The log activity and priority configuration prove dispatch/recovery behavior, but do not prove that archive requests cause no live delay under simultaneous eligible load. Keep this part open until a real live PBP job overlaps archive traffic and per-request wait/latency is captured.

Machine-readable evidence: [`flashscore_restart_shared_limit_2026-10-26.json`](flashscore_restart_shared_limit_2026-10-26.json).
