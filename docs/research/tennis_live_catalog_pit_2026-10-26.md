# Live Tennis API catalog PIT projection — 2026-10-26

## Change

The external tennis source worker now projects only explicit Live Tennis API catalog values (`format=BO3/BO5` and supported `surface`) into a Fonbet tennis event when both players resolve through confirmed `live_tennis_api` canonical aliases and the Fonbet participants resolve through confirmed Flashscore identity links and aliases.

The availability timestamp is the maximum of the catalog's immutable first-seen time, both source alias creation times, and both identity-link availability times. A later identity confirmation therefore cannot backdate the format or surface. Existing timestamped values from another source are preserved; conflicts, missing mappings, and ambiguous event pairs are skipped. The catalog player ID namespace is kept separate from API-Tennis IDs. Tournament names and scores are never used to infer the format.

Unchanged raw catalog payloads keep their first-seen timestamp. Projection can run again on later polls using identities confirmed since that first observation, while assigning the metadata an availability time no earlier than those confirmations.

## Verification

- `tests/research/test_external_tennis_sources.py`: **48 passed**.
- Python compilation and `git diff --check`: passed.
- Rebuilt and restarted `research` and `external-tennis-sources`; both reported healthy after startup.
- First live worker cycle returned HTTP 200 and archived a catalog of 48 matches. Projection reported `metadata=0`, `unresolved=48`, `ambiguous=0`, `conflicts=0`.
- Projection now also accepts a direct, exact-unique Live Tennis API profile verification for a Fonbet participant, even before the stricter global canonical alias is materialized. It requires the provider profile ID and full name to match the unique lookup, then requires both participants to resolve to the same unique open Fonbet event pair. Its PIT time is no earlier than either profile fetch. A later catalog cycle had 38 matches and still found no complete pair; among 76 catalog player IDs, one had an exact verified profile and zero full Fonbet pairs matched.
- Replayed all three already archived catalog observations through this safe projector: 124 explicit-format matches checked, 124 unresolved, 0 metadata writes, 0 ambiguous event pairs, and 0 source conflicts. Read-only production counts: 0 events with a `best_of` source and 0 events at current period 4 or later. Thus no existing event has a safe value available to fill right now.
- Read-only production query after the latest cycle: 0 tennis events carry `best_of_source=live_tennis_api`; 0 carry `surface_source=live_tennis_api`. No event is currently at period 4 or later, and no current event has format metadata, so no score-based BO5 value was eligible to fill.

## Admission status

The current identity coverage does not yet connect a complete catalog pair to a Fonbet event. No event metadata was synthesized or backfilled. Existing audit remains blocked: 0/7,795 examples PIT-certified; event format is missing or unavailable as-of in all 7,795, surface is unknown in all 7,795, and chronology purges training matches whose labels arrive after validation forecast times. The thresholds were not changed, and training, holdout-quality evaluation, SHADOW, and promote were not run.

The next admission attempt requires prospective catalogs whose players have already-confirmed identities (or confirmed identities whose availability is correctly timestamped), plus enough prospectively observed source features and outcomes to satisfy the unchanged coverage and chronology gates. Then rerun the full admission audit and dataset builder; do not start training before that audit passes.

## Fonbet score inference

The archived Fonbet payload audit found no explicit format field in 3,079 event rows. The Fonbet parser preserves explicit `best_of` when present, but does not infer BO3 from `1:1`, competition, or an incomplete score. The live-score invariant now also recognizes a live third set after one player has won the first two sets as proof of BO5; otherwise it records BO5 from the fourth set or after four completed sets. The PIT timestamp is the current score observation time. This does not backfill earlier snapshots, and a 1:1 set score remains unknown.

The current Live Tennis API catalog has explicit format information. The latest 38-match catalog contained 76 players; one had a verified exact profile, but no complete exact-profile pair matched an open Fonbet event. No link was fabricated. The hourly worker now tries both strict canonical aliases and exact, participant-scoped profile links on every catalog cycle. External metadata remains the preferred path for format known before play; score inference only covers matches whose observed progress forces BO5.

The score-inference unit test covers `1:1` remaining unknown and a 2:2 / fifth-set state producing BO5. `tests/sports/test_tennis_adapter.py`: **10 passed**. The profile identity check and source worker suite: **49 passed**. `worker` and `external-tennis-sources` were rebuilt/restarted and are healthy. No existing event metadata was backfilled because none had an eligible source observation or a forced BO5 score.

## Follow-up: collect upcoming fixtures for prospective PIT (2026-10-26)

The previous worker polled only `status=live`; that catalog is inherently too late for pre-match examples. The collector now polls Live Tennis API `GET /matches?status=upcoming` once per hour. The provider documents `upcoming` as part of its FREE current-state listing and exposes explicit match `format` and `surface` fields. Payloads are archived under the `upcoming` source key and projected through the same confirmed two-player identity path. Availability is the immutable first fetch time, bounded below by both identity-availability times; no timestamp is inferred from scheduled start or terminal results. Fonbet remains authoritative for score/state. The shared request reservation remains capped at 80/day; replacing one hourly live catalog call with one upcoming call does not add a request. The player profile candidate window was widened from ±6h to ±24h so scheduled participants can be looked up before start.

The implementation was deployed on 2026-10-26 and provider-verified: `status=upcoming` returned HTTP 200 with 100 fixtures; projection found 0 confirmed Fonbet pairs and wrote 0 event rows. It can populate future examples only when the upcoming catalog is first observed before the example cutoff and the exact identity pair is already confirmed. Existing historical catalogs were captured live and cannot become pre-match evidence; current historical PIT remains blocked. Evidence for endpoint fields: [Live Tennis API official docs](https://docs.livetennisapi.com/live-scores.html).
