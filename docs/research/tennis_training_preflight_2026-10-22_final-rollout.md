# Independent odds-free tennis admission — final-rollout checkpoint

**Observed:** 2026-10-22 01:46 UTC<br>
**Decision:** `blocked`<br>
**Boundary:** Stop before LR/GB/GRU training, candidate/development evaluation, opening holdout quality metrics, and SHADOW registration.

## Last completed full data audit

The latest completed full audit remains [`tennis_label_audit_2026-10-22_postsoak.json`](tennis_label_audit_2026-10-22_postsoak.json), generated 00:34:22 UTC, SHA-256 `257149e5dd3a7480e53f9104e13f236bf7cd569546e2d1a1353dcbbb67b130d4`.

- Shared manifest: [`tennis_training_manifest_postsoak.json`](tennis_training_manifest_postsoak.json), 2,393 whole matches (1,914 development / 479 sealed holdout), dataset digest `a9666c9694c507ea53d5885afe9e00e384b8b813680cba1605c130fa8405552d`, file SHA-256 `a6349c07655a3cafbf5b6a9cbf0ed990b8921074592a51f8d4774ba636b1e4b`.
- Provenance examples: [`tennis_training_examples_e134829734dc.jsonl`](tennis_training_examples_e134829734dc.jsonl), 4,220 rows, SHA-256 `8da96ca7a8adaf87ea3ede946dbf2b0429ba6c6a1b40e207c5d10bb8c1a01a9e`, logical digest `e134829734dc2a2ef2be591488d921ecdf98c664b875421baf0d2e73ec8006e9`.
- Labelled matches in that audit: point/game/set 34 each; match 2,408. Observed point/game/set examples: 49/9/0. P/G/S train counts across four shared folds: 19/23/26/27; validation: 4/3/1/0; holdout: 7. Temporal precedence failed all four folds for each target, including match.
- The 4,220/4,220 PIT checks pass only under the explicit contract semantics for documented priors and unknown values. Fully source-observed examples: 0. This is not evidence that all feature values are observed in source history.
- Every target still misses at least the 500 labelled-match train minimum and the four 100-match validation minima. The P/G/S targets are also far below the 200-match holdout minimum. No holdout quality metrics were read.

## Live PBP follow-up

Event `69173294` (Fonbet UUID `cb7ca562-272e-498d-a657-ddea74cae648`, Flashscore `nFpUNjLN`) was live during the soak. After the reserved PBP lane rollout, its API history grew from 53 to 146 point events, and side orientation was confirmed using saved Flashscore scoreboard DOM/profile IDs. The latest captured source document was available at 01:20:48 UTC and ended at set 3 game score 0-3, point 15-40; a Fonbet snapshot at 01:21:01 reported the same set/game score but point 30-40. Thus it was source-observed history, but not synchronized to the latest point. Raw response and score snapshot are [`tennis_live_pbp_api_smoke_2026-10-22_0121_response.json`](tennis_live_pbp_api_smoke_2026-10-22_0121_response.json) and [`tennis_live_pbp_api_smoke_2026-10-22_0121_fonbet_state.json`](tennis_live_pbp_api_smoke_2026-10-22_0121_fonbet_state.json); their summarized timestamps, side evidence, hashes, and acceptance are in [`tennis_live_pbp_api_smoke_2026-10-22_0121.json`](tennis_live_pbp_api_smoke_2026-10-22_0121.json).

The Fonbet event later finalized at 01:43:31 with `1:2 (6-4 2-6 0-6)`. The latest captured PBP did not include the rest of set 3. The event therefore cannot be accepted as a complete observed PBP match. Its completion occurred after the previous audit, so a refreshed target count could gain at most this one match from this event; it cannot satisfy the 500-match minimum.

## Final worker runtime evidence

The separate final-rollout soak is complete. Summary: [`flashscore_reserved_lane_soak_2026-10-22.json`](flashscore_reserved_lane_soak_2026-10-22.json), SHA-256 `c0e2ca3edae55765a178c3aee31b8c1ad85a0c60b9e15332dce6083aec025bce`.

- Queue API: 61/61 HTTP 200; worker heartbeat healthy 61/61; latency p50/p95/max 0.104/0.232/1.282 seconds.
- `overdue_pbp` was 1 in 6/61 samples (maximum 1) and 0 in the final sample.
- Resource sampler: 55/55 samples without errors, maximum two Chromium roots. Worker peak was 1.494 GiB / 2 GiB (74.7%); a single sample reached 150.59% CPU. These are transient peaks without a crash, but show limited headroom.
- Rolling-hour counters changed from 16 to 40 saved documents and 16 to 42 confirmed links; this subset observation does not prove full import completeness or throughput at 500+ matches.
- One Livesport search returned HTTP 429; worker continued. No worker traceback or logged timeout occurred during this soak. A PBP job separately retried on `SET_2_NAVIGATION_TimeoutError` during the collection window.
- No forced I/O stall was injected; recovery under that condition remains unverified.
- Raw JSONL hashes: runtime soak `f284cc78cbcf97249b361c3a23534e922f3b776c94b0e5d1c7efc96b15d9b954`; resource soak `c68f9a8df5251380000d4bddc75460cd0aa81c6402aff0c29360da636e1318e1`.

The MCP system-health report at 01:30 UTC was `healthy`, with production `BET_MODE=SIMULATION`; all 16 Compose services were running after the soak. The MCP-wide data-quality endpoint timed out. The neural container was running only `python -m app.main`; no training process was started in this task.

## Fresh-audit attempt and why counts are not refreshed

A full read-only `build_report()` pass was attempted at 01:42:12 UTC after the live event finished. The audit stalled in `_result_score_snapshot_evidence`, querying exact Fonbet score snapshots for all historical events. After about 2 minutes, PostgreSQL Docker memory was 1.966/2 GiB (98.3%) and CPU 40.31%. The audit query was canceled and the Python process interrupted; no audit report, manifest, or provenance output was written, and the database was not modified. PostgreSQL stayed healthy and memory subsequently decreased, but host availability was low, so the broad query was not retried.

This means the previous complete report remains the last full quantitative audit, but it predates one newly finished match. The admission decision is still `blocked` because the observed P/G/S match counts are orders of magnitude below the fixed minima, every prior validation fold failed temporal precedence, and fully source-observed PIT examples were zero. The exact post-event label/window counts and new unified provenance manifest require an optimized full audit pass before they can be called current.

## Work that remains before training

- Collect and manually verify original PBP documents across unique matches, including tie-breaks, gaps, corrections, orientation, and current-score latency.
- Continue the profile → match → confirmed link → PBP document → four-target label funnel until all fixed counts and temporal checks pass.
- Improve the full audit’s historical snapshot query so a current manifest/provenance pass completes within database resource limits; then publish the refreshed audit and exact hashes.
- Complete remaining roadmap gates for player/source feature PIT, full-queue throughput/import completeness, and an injected transient I/O stall.

No training, candidate/development evaluation, holdout quality inspection, or SHADOW registration was performed.
