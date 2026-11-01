# Tennis source coverage audit — 2026-10-25

Read-only production snapshots: external-history census 2026-10-25 07:59 UTC; Fonbet event census in one repeatable-read transaction at 08:02:07 UTC; PBP review generated 07:57 UTC. The live event counts are point-in-time and change as Fonbet is polled. These are censuses of stored rows, not proof that providers cover every real match. External source tournament strings are counted per source and are not globally canonicalized.

## Fonbet live feed

| Measure | Count |
|---|---:|
| Fonbet tennis events | 4,332 |
| Finished / live / prematch / interrupted / cancelled | 3,971 / 162 / 190 / 5 / 4 |
| Events without a linked `leagues` row/name | 4,332 (100%) |
| `best_of` absent from event metadata | 4,332 (100%); source-time field absent in all 4,332 |
| Surface metadata | hard 3,840; absent 492; source-time field absent in all 4,332 |
| Numeric `source_event_id` | 3,974; non-numeric 358; null 0 |
| Unique participant IDs across the events | 3,294; 17 events have at least one missing side ID |
| Confirmed Flashscore identity links among those IDs | 643; candidate 347; any confirmed/candidate link 673 |

The current Fonbet event store therefore cannot be grouped reliably by tournament, and live rows do not consistently carry a numeric event ID, surface, or source-timed match format. The 3,840 `hard` values have no separate source-availability timestamp in the event metadata census, so they are not certified as PIT surface features.

## External result/history sources

Counts below are stored source rows, not unique matches across providers. “Player IDs” means both winner and loser source/canonical ID fields are populated; it is a structural check, not an identity-quality adjudication. “Serve pair” counts rows where both winner and loser serve fields are present; denominators and validity are not implied.

| Source | Rows | Played date span | Distinct tournament strings | Surface unknown | Rows with both player IDs | Rows with both serve fields | Seasons represented |
|---|---:|---|---:|---:|---:|---:|---|
| API-Tennis | 496,868 | 1990-02-05–2026-10-24 | 6,461 | 496,868 (100%) | 496,867 of 496,868 total rows; 1 older row lacks IDs | 70,621 | Every year 2016–2026, plus earlier history |
| ATP odds archive | 54,377 | 2000-01-03–2026-10-01 | 263 | 0 | 0 | 0 | Every year 2016–2026 |
| Flashscore | 104,077 | 1990-07-09–2026-10-22 | 4,293 | 1,855 (1.8%) | 104,077 | 0 in these columns | Every year 2016–2026, plus earlier history |
| Sackmann SQLite | 542,113 | 1968-01-01–2024-05-20 | 4,771 | 3,730 (0.7%) | 542,113 | 40,267 | Every year 2016–2024; no 2025–2026 rows |
| `ten` | 111,404 | 2021-01-05–2026-10-14 | 1,451 | 1,083 (1.0%) | 111,404 | 109,951 | Every year 2021–2026 |
| Tennis Explorer | 11,018 | 2004-06-05–2026-10-25 | 1,188 | 11,018 (100%) | 11,018 | 0 | Every year 2016–2026, plus earlier history |
| TML | 826,960 | 1967-02-20–2026-10-24 | 8,824 | 6,341 (0.8%) | 826,924 (36 rows lack one side ID) | 128,166 | Every year 2016–2026, plus earlier history |

Surface coverage is provider-specific: API-Tennis and Tennis Explorer do not populate the stored surface field; ATP odds archive, Flashscore, Sackmann, `ten`, and TML have broad coverage but still include unknown rows. Across source aliases, identity status is 61,089 confirmed, 317 candidate, and 11 rejected rows (aliases are not unique players across providers). The ATP odds archive has no player IDs and needs an external identity join before it can safely contribute player-level history.

## Player matching

The Fonbet census contains 3,294 distinct participant IDs; 17 events have at least one missing side ID. The Flashscore identity-link table has 643 confirmed and 347 candidate links among these IDs, with any link for 673 unique players. This leaves 2,651 participants without a confirmed Flashscore link in this particular identity table. Other external source aliases exist (API-Tennis 19,568 confirmed, Sackmann 23,428, TML 14,337, Tennis Explorer 3,665, Flashscore 80, live_tennis_api 10, Tennis Abstract 1), but those counts do not establish a one-to-one Fonbet crosswalk.

## Point-by-point sequence continuity

Stored point-history inventory:

- Match links: 3,543 `matched`, 8 `manual`, 20 `ambiguous`.
- Point documents: API-Tennis 217,589 retrospective partial; Match Charting Project 11,785 retrospective partial; Flashscore 740 non-retrospective complete + 100 non-retrospective partial + 447 retrospective complete + 2,372 retrospective partial.
- Automated consistency review [`tennis_pbp_corpus_review_2026-10-25.json`](tennis_pbp_corpus_review_2026-10-25.json), generated 07:57 UTC: latest 20 confirmed links reviewed; side order confirmed for 20/20, but parsed authoritative point events 0/20, terminal set-score comparisons 0/20, and gaps/transitions were therefore not measurable. Archived HTML human review was not performed.

This review does not certify sequence continuity. The latest linked cohort currently has no usable authoritative point sequence in the reviewed versions, despite the larger archive of retrospective and partial documents.

## Consequence for candidate comparison

The latest completed odds-free admission audit remains [`tennis_label_audit_20261022T1540Z.json`](tennis_label_audit_20261022T1540Z.json), cutoff 2026-10-22 15:39 UTC. It admitted no training/evaluation: PIT-certified examples 0/6,083 because `event_format` availability was missing for all examples; causal train matches were 0 in each of four folds for every target; point/game/set validation had 3/2/2/4 matches per fold versus 100 required, and point/game/set holdout had 24 each versus 200 required. Match holdout had 518 matches, but its causal train count was still zero. The unchanged production census above finds `best_of` absent from all 4,332 current Fonbet tennis events, so the PIT format blocker is still present.

No LR/GB/GRU training, candidate evaluation, calibration/log-loss/Brier computation, or holdout-quality review was performed. The holdout remains sealed. Do not relax the existing admission thresholds; rerun the admission audit only after source-timed format/feature evidence and causal labels have accumulated.
