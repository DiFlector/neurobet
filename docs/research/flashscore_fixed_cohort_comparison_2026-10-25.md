# Flashscore fixed-cohort acceptance audit — 2026-10-25

This read-only audit re-examined the existing 2026-10-23 two-slot baseline and three-slot trial; it did not change worker settings or enqueue production jobs. The worker remains at the supported two-browser budget.

## Controlled-run start probe — 2026-10-25 13:17 UTC

- Read-only queue census: 20 currently eligible events, six prepared players, and 16 queued player-history jobs. The Flashscore worker is healthy at 2 browsers and about 891 MiB / 2 GiB.
- Durable source control has `cooldown_until=2026-10-25T17:06:39Z`, `slot_limit=1`, and `success_count=0`; it records the last throttle at 11:06:39 UTC. The queue reports no eligible live PBP jobs. A DB census found 22 fresh live events; their existing point jobs were all `waiting_players / PLAYER_SOURCE_IDS_MISSING` (zero attempts).
- Therefore no production baseline request was sent. A profile/PBP latency run during the cooldown would measure a paused source and a cohort with no PBP-eligible rows, not worker performance. The first safe baseline window must begin after this durable cooldown expires and after a real, fixed PBP-eligible cohort exists.

## Matched-cohort findings

- The windows each tracked 24 player jobs and 12 match/PBP jobs. Only 18 player IDs and 9 PBP event IDs occur in both fixed projections.
- Of the 18 shared player IDs, 11 were eligible at the start of both windows. None completed during either window (`0/11` in each). The baseline's two completed profiles were no longer eligible in the later trial; the 20 total successful trial profiles had **zero** IDs in common with the 11 successful baseline profiles. Therefore the reported aggregate 11→20 profiles is not a fixed-cohort throughput comparison.
- Of the nine shared PBP event IDs, none completed in either window. Their per-job `wait_seconds` are absent from the saved samples, so matched-cohort PBP latency cannot be calculated. The published 13 s→63 s p95 is queue-wide and uses different eligible populations.
- Saved queue snapshots retain cumulative `attempts` and only the latest error. Several trial jobs later become `not_in_projection`; per-attempt timeout outcomes are not retained in these artifacts. Thus timeout rate for the matched eligible cohort cannot be reconstructed from `top_errors` or current `last_error`.

## Full accessible imports

The four trial archive jobs completed, covering 22 Flashscore profiles. Production `result_json` records `history_scope=full_accessible`, `pagination_complete=true`, two confirmed empty terminal probes, and matching advertised versus observed unique singles counts for all 22/22 profiles (13,776 advertised and observed rows in aggregate). This proves source pagination completion for those 22 profiles. It does not prove that all source rows were newly projected: many were duplicates or rejected by result-quality filters, and there is no corresponding same-profile baseline cohort.

## Decision

Keep the roadmap item open. Existing evidence supports worker health and confirms terminal pagination for 22 profiles, but does not meet the requested acceptance: same eligible profile cohort before/after with per-attempt timeout outcomes, same live PBP event cohort with per-event latency, and full-import completeness across that cohort. Do not increase the browser budget; the earlier three-slot run approached the worker memory limit and violated the live-PBP latency guard.

Reproducible source artifacts:

- `flashscore_capacity_baseline_2slots_2026-10-23.jsonl`, SHA-256 `18b1f8e001744e924c879e32f65483b6f60c86d9dd51b056085158df919dcbdf`.
- `flashscore_capacity_trial_3slots_2026-10-23.jsonl`, SHA-256 `97652c44f7f269ddc7d9a6966fe91c8b1b83a98e952e0e82f2ea3cfd998485f9`.
- `flashscore_capacity_trial_3slots_db_all_outcomes_2026-10-23.json`, SHA-256 `2455146c982d1cfb4078044d0d230e3b40749729f3a2e02bab1d1b81335c6e2d`.
