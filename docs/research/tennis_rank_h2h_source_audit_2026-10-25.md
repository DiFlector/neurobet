# Official ranking, H2H and opponent-strength audit — 2026-10-25

Production verification completed 2026-10-25 12:32 UTC. Reads and the source-profile refresh used confirmed identities only; no synthetic match/ranking rows were added. The refresh made 14 provider calls and appended 14 ranking observations.

## Source and PIT evidence

- The Live Tennis API player profile identifies the current singles position as ATP/WTA ranking and exposes a ranked/unranked status. Its free profile response does not expose the ranking publication week, so `ranking_date` remains `NULL`; it is not inferred from retrieval time. See [provider player/ranking documentation](https://docs.livetennisapi.com/players-and-tournaments.html) and [API reference](https://docs.livetennisapi.com/reference.html).
- Each new observation is append-only and stores exact confirmed provider ID, tour, `available_at`, source URL, parser version `live_tennis_player_profile_v2`, raw object reference and SHA-256. The profile's `ranking_checked_at` must not be later than retrieval time. Unranked responses are retained with `rank=NULL` and `ranking_status=unranked`.
- Current DB census for `live_tennis_api_official`: 27 observations, 26 ranked and one explicitly unranked, 14 distinct canonical players. Fourteen latest observations have URL/parser provenance; the 13 earlier observations retain their raw reference/hash and availability time but predate URL/parser provenance. Latest availability: `2026-10-25T12:30:39Z`.
- The neural PIT loader now accepts only ranked observations no older than 30 days and with `available_at <= cutoff`. Runtime read-through loaded all 13 confirmed ranked participants (13/13). A tie-order bug where rejected alternate IDs could mask a same-time confirmed identity was corrected; a later rejection of the same source ID still overrides an earlier confirmation.

## Materialized independent metrics

- Production has 80 canonical snapshots at the latest materialization version, 69 with non-empty H2H; the JSON metric contract reports `tennis_independent_metrics_v4`. Snapshots carry dependency digests and source watermarks. H2H is keyed by canonical opponent ID and exposed to inference only when both participant snapshots agree reciprocally.
- A live-code read-through on an actual confirmed pair returned ATP ranks 3 and 39, reciprocal H2H 7–0 over seven meetings, Elo 2698.571/2289.344, Glicko 2446.083 (RD 81.957)/2059.312 (RD 64.341), adjusted serve rates 0.633/0.626 over 178/341 points, and adjusted return rates 0.386/0.354 over 206/359 points. Both participants had independent metrics and fresh ranking observations; the pair was marked ranking-comparable and H2H-available.
- The refreshed canonical metric snapshot is available at `2026-10-25T12:13:22Z`. Materializer state was generation 78,505 / processed generation 75,093 at 12:31:56 UTC; some newer source changes remained queued during this census. Existing snapshots remain PIT-readable at their recorded cutoff while the background refresh catches up.
- Counts remained `bets=1,025`, `ledger_entries=3,479`; ranking ingestion and metric reads created no wagers or ledger entries.
- At 12:39 UTC the rebuilt `external-tennis-sources` image was deployed with its six-hour official-ranking refresh loop active; the service returned to healthy with a 3 GiB limit. Startup imported new TennisMyLife rows, raising refresh state to generation 79,916 / processed 75,093 by 12:39:43 UTC, so the background materializer has a live backlog to catch up. The 27 ranking observations did not increase during restart because the 14 confirmed profiles had already been refreshed within the seven-day guard; this confirms restart did not duplicate them.

## Checks

- Research + sports focused suite: 50 passed (`test_external_tennis_sources.py`, `test_tennis_independent_metrics.py`).
- Neural identity + forecast suite: 27 passed (`test_tennis_player_identity_links.py`, `test_odds_free_tennis_forecast.py`).
- `py_compile` on changed Python modules and `git diff --check`: passed.
- Runtime pair-feature check read real rows through `load_player_metric()` and `pair_features()` in the healthy neural container; no DB writes were performed by this check.

## Scope and limitation

The Phase 42 feature item is complete: PIT rank provenance, opponent-based Glicko-2, reciprocal H2H, and opponent-adjusted serve/return are present in materialized data and have been read by the live feature path. This is a data/contract verification, not a claim that `tennis_v1.2.0` has passed predictive-quality or model-release gates. Ranking publication week is unavailable from the provider's free profile endpoint, overall player coverage is still limited, and the materializer had a queued refresh backlog at audit time.
