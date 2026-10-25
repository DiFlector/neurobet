# Production identity audit for the fixed tennis cohort — 2026-10-24

## Scope and method

This read-only audit used the same 20 TennisExplorer singles matches from 2026-10-22 as the saved source replay: five each from ATP, WTA, Challenger and ITF. It checked the exact TennisExplorer match IDs in `ext_tennis_matches`, resolved each source player ID through `ext_tennis_canonical_players.source_ids.tennis_explorer`, then looked for one canonical match with that exact canonical player pair and a date within one day before to two days after the source date. A match was counted as linked only when the pair had exactly one canonical candidate. No database rows or identities were changed.

## Results

| Tour | Raw TE records | Unique canonical pair | Canonical score agrees with TE | TE aliases | TML aliases | API-Tennis aliases |
|---|---:|---:|---:|---:|---:|---:|
| ATP | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| WTA | 5/5 | 4/5 | 4/4 | 4/5 | 0/5 | 0/5 |
| Challenger | 5/5 | 4/5 | 4/4 | 4/5 | 0/5 | 0/5 |
| ITF | 5/5 | 5/5 | 5/5 | 5/5 | 0/5 | 0/5 |
| **Total** | **20/20** | **18/20** | **18/18** | **18/20** | **5/20** | **5/20** |

The two unmatched rows remain unlinked:

- WTA `3355695`, Stoiana—Uchijima: the raw opponent ID `uchijima` does not equal the already attached TennisExplorer identity `uchijima-39346` for Moyuka Uchijima. The name suggests a possible alias, but this audit did not confirm that provider-ID change, so no link was written.
- Challenger `3356201`, Rodriguez Taverna—Bueno: the TE source player ID `rodriguez-taverna` is attached to two different canonical players (`Rodriguez Taverna S.` and `Santiago Fa Rodriguez Taverna`). The match remains unlinked until this duplicate identity is resolved.

All 18 unique canonical pairs had the same set-game score as the exact TE source row. The identity map contains 40 TE alias rows for 39 distinct source IDs; the duplicate `rodriguez-taverna` alias is the reason one match remains ambiguous. The `uchijima` ID has no exact map. No ambiguous pair was auto-confirmed.

After the initial audit, the TennisExplorer projection gained a conservative compound-surname fallback. It resolved `Bassols Ribera M.` to the unique canonical full name Marina Bassols Ribera only because the complete surname token set, initial and gender matched and the TE ID was unused. A rollback-only production smoke produced one projection for ITF match `3356155`; the regular worker then persisted the same link. A separate retry by deterministic source key fixed an existing `IntegrityError` that had rolled back repeated daily projection batches. The deployed recent-window projection completed with `projected=23`, `ambiguous=140`, `unlinked=381`, `unchanged=1,104`; the worker is healthy. The WTA and Challenger conflicts above remain unresolved.

## Capability limits

The production audit proves identity-linked canonical results only for this retrospective cohort. It is not a live Fonbet cohort. API-Tennis and TML aliases appear on the five ATP canonical matches only; this does not demonstrate their production coverage for the other tours. The saved HTTP replay separately found API-Tennis point rows in 20/20 candidate matches but could not certify a complete point sequence in any of them (0/20 full PBP). Its candidate-level serve comparison is not an identity-confirmed production stat audit. Do not disable PBP fallback or claim full PBP coverage from these results.

The missing WTA/ITF identity aliases need a durable source-ID alias resolution path with conflict evidence and tests. Until then, unresolved links stay raw and excluded from canonical player metrics. The broader acceptance item remains open for current Fonbet matches, corrections, PBP quality, and live source behavior.

## Evidence

- Fixed cohort: `/tmp/neurobet-source-probe/cohort.json` (20 TE source IDs).
- Saved HTTP replay: [`tennis_cohort_replay_2026-10-24.md`](tennis_cohort_replay_2026-10-24.md).
- Production read-only cohort query rerun 2026-10-24 13:35 UTC against exact source IDs, unique TE player IDs, canonical participant pairs and a bounded date window.
- Regression tests for canonical projection: 16 passed. The external-tennis-sources container was rebuilt and healthy after deployment.

## Follow-up: cross-source match duplicates, 2026-10-24 13:48 UTC

A bounded lookup by the exact saved source match IDs and indexed canonical player-pair/date keys found additional API-Tennis evidence for both previously unresolved identity cases:

- WTA `3355695` Stoiana—Uchijima has exact API-Tennis raw match `12172767` (`2026-10-22`, Osaka, `6-2 2-6 6-3`) and a canonical API row with the same score and source participant names `M. Stoiana` / `M. Uchijima`. The TennisExplorer raw profile records for both `uchijima` and `uchijima-39346` show `Uchijima M.` and gender `w`; however the canonical match references separate API-only abbreviated player rows, so no participant alias was written automatically.
- Challenger `3356201` has a canonical TennisExplorer result row (`d5d37359-5249-5f89-86d4-7bde1776c44b`) with exact source score `6(12)-7 6-4 6-2`. API-Tennis raw match `12173096` and canonical row `7dfc4f7d-9738-591b-9f80-00b94a7a8178` describe the same date, tournament, participants and normalized result (`6-7 6-4 6-2`), but use separate API-only player rows. This is a cross-source duplicate candidate, not a confirmed merge.

These findings update the earlier “unmatched” description: the evidence gap is cross-source canonical identity and deduplication, not absence of source records. Preserve both raw observations and all canonical IDs until a durable alias/duplicate representation is implemented and tested; do not count the duplicate as a second player result. The broad fixed-cohort acceptance remains open.

## Reconciliation follow-up — 2026-10-24 15:10 UTC

The exact shared-ID rule was completed and exercised against the production rows. For WTA Stoiana—Uchijima, API-Tennis `12172767`, the TML weekly row (`88321c99-4f48-50f1-bc01-18495ea166ca`, 2026-10-19) and the TE/API row (`faab4375-cad0-5ac5-9a41-f3ef840322c3`, 2026-10-22) have the same canonical winner and loser IDs, tournament, gender, score, and exact API-Tennis source alias. The TE/API date is retained as the survivor because it also has the established TE source alias; the three-day discrepancy is recorded in `stats.source_date_conflicts`. The former API-only child (`0f30eeef-f3ff-5ce7-a37f-c8405d754364`) remains directly attached to that survivor. The three canonical rows now have exactly one non-duplicate row, and the original raw source observations were not modified. This targeted merge does not resolve the separate ambiguous Rodriguez Taverna identity or establish broad-cohort completeness.

## Indexed replay of adjudicated API-Tennis player IDs — 2026-10-24 17:31 UTC

The prior player-scoped replay was too expensive because raw results had no provider-player indexes. Migration `046_external_player_id_idx` added concurrent indexes on `(source, winner_player_id)` and `(source, loser_player_id)`. Production `EXPLAIN` now selects both through `BitmapOr` (estimated 188 matches) instead of scanning every API-Tennis result. Replaying the saved observations for confirmed IDs `361`, `9650`, `8762`, and `38056` selected 772 rows and committed 666 date groups independently: 21 canonical projections, 10 exact merges, 347 unchanged, 101 ambiguous and 293 unlinked. The latter cases were deliberately left without forced identities; raw observations and their timestamps were not edited. Alias/projection regression tests: 33 passed. The existing fixed 20-match cohort result (19/20 confirmed participant pairs, with the Rodriguez Taverna conflict) and complete-PBP result (0/20) remain the admission evidence; this broader alias replay is not a replacement for that cohort audit or for live Fonbet E2E.

## Refreshed fixed-cohort canonical check — 2026-10-24 15:14 UTC

Rechecked the same 20 saved TennisExplorer source match IDs against production `ext_tennis_matches`, confirmed `tennis_explorer` player aliases, canonical rows, and normalized scores after the source-ID reconciliation. Each TE match ID now has exactly one active canonical row and all 20 scores agree after stripping provider-specific tie-break annotations. Confirmed participant IDs match the canonical sides for 19/20; Challenger match `3356201` remains unconfirmed because TE ID `rodriguez-taverna` has two candidate owners. Source-alias presence on those canonical rows is:

| Tour | TE match rows | One active canonical row | Confirmed TE participant pair | Normalized score agrees | API-Tennis alias | TML alias |
|---|---:|---:|---:|---:|---:|---:|
| ATP | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| WTA | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 | 5/5 |
| Challenger | 5/5 | 5/5 | 4/5 | 5/5 | 3/5 | 1/5 |
| ITF | 5/5 | 5/5 | 5/5 | 5/5 | 0/5 | 0/5 |

Alias presence means the source match is attached to the canonical row; it does not prove that source has complete history, serve statistics, or PBP for that match. The saved API-Tennis PBP replay still certifies 0/20 complete point sequences. This refresh improves the earlier TE alias tally but is a retrospective production snapshot, not a live Fonbet E2E or a PBP/serve capability acceptance.

## Live Fonbet history routing — 2026-10-24 15:22 UTC

The current event Lamens S.—Jacquemot E. initially returned complete TML history for Lamens (440 results) but no history for Jacquemot, even though the canonical archive has Elsa Jacquemot with confirmed TML and TennisExplorer IDs. Fonbet's Russian spelling `Жакмо Э` does not pass the strict general name matcher against French `Jacquemot`. A narrow fallback now considers only French canonical profiles with both confirmed provider IDs, an exact French orthographic/phonetic surname key, a matching initial, and a unique candidate. A rollback-only seed created 42 historical facts; the deployed `/api/tennis-history/prepare` then returned `ready=true` for both players. Jacquemot's profile now has 42 TML results, complete seven-day coverage (`checked_at=05:54 UTC`, source watermark 2026-10-23), and materialized metrics at 15:22 UTC. The prior Flashscore no-match job was then closed from fresh TennisExplorer partial history (`completed`, error cleared, attempts still 6); no new Flashscore request occurred. This verifies history routing only. PBP identity remains a separate need and no live-PBP completeness is claimed.
