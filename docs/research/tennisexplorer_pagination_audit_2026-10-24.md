# TennisExplorer pagination audit — 2026-10-24

## Purpose

Validate all accessible multi-page profile snapshots without issuing new requests to TennisExplorer. The audit checks source-response integrity and visible pagination only; it does not claim complete career history.

## Method

- Read each saved `tennis_explorer/profile:*` state from PostgreSQL.
- Load the compressed provider response from MinIO.
- Verify the raw bytes against the stored SHA-256 before parsing.
- Parse all `matches-YYYY-N` selectors and `matches-YYYY-N-data` blocks, reject doubles rows, and retain the original source observation timestamp.
- Run the parser upgrade with `daily_budget=0` to prove that local reparsing is independent of provider HTTP quota.

## Results

| Check | Result |
|---|---:|
| Saved profile snapshots | 19 |
| Raw snapshots with verified SHA-256 | 19/19 |
| Snapshots upgraded to `tennisexplorer_profile_v3` | 19/19 |
| Visible year-page blocks | 423 |
| Missing page numbers | 0 |
| Profiles with internally consistent visible pagination | 19/19 |
| Rows examined / rejected | 8,033 / 2,499 |
| Normalized singles results | 5,534 |
| New TennisExplorer HTTP requests for this parser upgrade | 0 |

The parser and projection replay found no newly projectable canonical rows in this pass; existing source observations were already materialized. The source HTTP request counter remained 10 and the profile request quota was unchanged.

## Limits

`pagination_complete` means the page markers and data blocks visible in one saved response are internally consistent. It does not prove that the provider exposed every career match, qualifying draw, or rescheduled match. TennisExplorer coverage remains `partial`; no doubles rows are added to singles history. This result is not a 20-match source-acceptance cohort and does not establish overall Flashscore reduction or throughput improvement.
