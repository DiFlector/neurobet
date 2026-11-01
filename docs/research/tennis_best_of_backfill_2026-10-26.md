# Tennis best-of backfill from official Fonbet results

## Outcome

An idempotent backfill was run against existing tennis events using only the event's official Fonbet result observation. The first pass committed 3,676 rows. A follow-up dry run found 9 additional eligible rows and committed them; follow-up details are in [`tennis_best_of_backfill_2026-10-26_followup.json`](tennis_best_of_backfill_2026-10-26_followup.json).

The current database check returned:

| Check | Result |
|---|---:|
| Current Fonbet tennis events | 4,617 |
| Current events carrying any format | 3,685 |
| Official-result backfill rows, all BO3 | 3,684 |
| Live score invariant rows, BO5 | 1 |
| Events still without format | 932 |
| Official-result availability timestamps before scheduled start | 0 |

The follow-up scanned 4,617 events: 3,676 already carried format, 9 new events had eligible normal finished results, and 932 were skipped (377 without an official result, 551 without a valid result `available_at`, and 4 that were not normal finished results). All 9 new results were completed two-set matches, so their format is BO3. Together with the first pass, this yields 3,684 official-result BO3 rows. One additional BO5 row is sourced from observed live score invariant evidence. This does not establish format for the 932 skipped events.

## Safety and PIT boundary

Eligibility required official `status=FINISHED`, a winner matching one of the two event participants, `is_retired=false`, `is_walkover=false`, a valid result `available_at` no earlier than scheduled start and no later than the current time, and a parseable normal final score. Existing format metadata was not overwritten. The script stores `best_of_available_at` from the official result's original `available_at`, with source and provenance; it does not fabricate an earlier timestamp or change historical snapshots.

The official result is observed at or after match completion. Therefore the backfilled format cannot be used as known at earlier in-play prediction cutoffs. This operation fills event-level metadata safely but does not by itself make any odds-free example PIT eligible or unblock model training.

## Reproduction

Dry run is the default:

```bash
python scripts/research/backfill_tennis_best_of_from_official_results.py
```

Commit explicitly with:

```bash
python scripts/research/backfill_tennis_best_of_from_official_results.py --commit
```

Machine-readable run artifacts: [`tennis_best_of_backfill_2026-10-26.json`](tennis_best_of_backfill_2026-10-26.json) and [`tennis_best_of_backfill_2026-10-26_followup.json`](tennis_best_of_backfill_2026-10-26_followup.json).

## Future formats

The live persistence path records BO5 only when Fonbet's observed score proves it: a live third set after one player won the first two, a fourth set, or at least four completed sets. Its availability timestamp is the score observation time. A 1:1 set score remains ambiguous. The external upcoming catalog path can supply explicit BO3/BO5 before play only after the source event is unambiguously linked to both Fonbet participants. Neither path retroactively changes earlier snapshots.
