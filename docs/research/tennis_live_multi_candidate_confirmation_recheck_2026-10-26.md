# Live multi-candidate confirmation recheck — 2026-10-26

Read-only production snapshot at 02:39 UTC (05:39 Moscow). No confirmation, identity, queue, or link rows were changed.

## Current live cohort

There were 11 numeric-ID live Fonbet tennis events. Only event `69316735` had confirmed Flashscore player IDs for both participants; its lookup had already returned `LIVE_MATCH_NOT_FOUND` three times and was scheduled to retry at 03:14:36 UTC. Its PBP job was `waiting_match`. The other live events were `waiting_players` because at least one confirmed source player ID was missing.

At the snapshot:

- pending Flashscore `event_match` requests: **0**; distinct pending event/candidate pairs: **0**;
- `TennisMatchLookupJob` rows in `waiting_confirmation`: **0**;
- `TennisPointRefreshJob` rows in `waiting_confirmation`: **0**;
- live pending cards therefore could not be counted in the admin UI, and there was no waiting job whose lease/token state could be observed;
- one shared Flashscore source slot was active, but it was not associated with a waiting-confirmation job.

The ten existing approved production match-confirmation requests cover ten distinct events and have no additional same-subject requests. They do not establish that a live search returned multiple candidates or that only one pending card per candidate pair was rendered.

## Actual PBP continuation after administrator approval

One real production case confirms the downstream path after a match approval: Fonbet event `69160487` / Flashscore candidate `U1v4wMZ7` was approved at 2026-10-21 18:47:03 UTC. Its PBP job subsequently reached `finalized` (one attempt), with two history versions containing 111 sequence events. This verifies continuation after an administrator decision for that event. It was finished by the current snapshot and does not satisfy the requested live multi-candidate branch.

## Follow-up dispatch

At 02:41 UTC I made the already-confirmed live match `69316735` due once and invoked the ordinary `/api/tennis-history/match-search/dispatch?limit=8` endpoint. The endpoint claimed one job, with the shared source cooldown still clear. At 02:43 UTC the real search returned `LIVE_MATCH_NOT_FOUND` again (attempt 4); the job is back in `retry` until 04:03:10 UTC. No candidate or pending request was created. The job has no lease/token, no lookup/PBP job is `waiting_confirmation`, and the shared source lease count is back to zero.

## Decision

The requested live scenario cannot be accepted from the current data. No live event currently has a multi-candidate confirmation request; the only live event with two confirmed player IDs has no Flashscore match and is under normal backoff. I made one bounded early retry for the confirmed live pair, retained the shared source cooldown/slot gate, and did not invent a second candidate or bypass any active source cooldown. Existing code clears the lookup lease/token when it enters `waiting_confirmation` and upserts requests by event/candidate, but that code path is not a substitute for the missing production scenario.

Keep the multi-candidate live checkbox open until a real live lookup returns at least two source candidates, the admin panel shows one pending request per candidate pair, jobs park without holding a lease/network slot, and an administrator decision resumes PBP.
