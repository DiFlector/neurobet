# Admin confirmation runtime and end-to-end verification — 2026-10-25

## Production runtime (read-only)

Checked at 2026-10-25 15:49 UTC after the backend rollout:

- Database revision is `054_tennis_point_doc_status`, which includes `033_source_point_jobs`.
- All five production `ADMIN_*` configuration keys are present; their values were not read or emitted.
- Backend, Research, and frontend containers are healthy. Backend `/health` and frontend `/diflector/neurobet` return HTTP 200.
- Backend TestClient smoke: unauthenticated confirmation-list request `401`; test login/logout `200`; player-profile and event-match lists `200` with 458 and 10 rows.
- Confirmation request/decision counts were `468/469` before and after the read-only smoke. No production confirmation decision was made by this check.
- With `ADMIN_USERNAME`, `ADMIN_PASSWORD_SHA256`, and `ADMIN_SESSION_SECRET` removed in a one-shot TestClient process, login and confirmation-list requests both failed closed with HTTP `503`.

## Isolated PostgreSQL/API/UI run

All writes below used the disposable database `neurobet_admin_e2e_20261025`, created empty and migrated through head. The production database was not used for approval/rejection tests.

- Player-profile reject and approve paths and event-match reject and approve paths returned HTTP 200. A rejected pair was not re-proposed by `upsert_manual_confirmation`.
- Two simultaneous decisions on one versioned request returned one `200` and one `409`; exactly one decision was recorded for that request.
- Approving a match wrote a manual match link and queued its point-history job/event. Rejecting a match wrote a rejected link and queued a new match lookup.
- Two pending profile candidates for one subject were shown in the admin confirmation panel; approving one superseded the sibling, which remained visible under the “all” filter as superseded.
- Browser test against the assembled frontend and patched backend: login, open Confirmations, reject a player profile, approve a match link, and approve one of two profile candidates. All actions completed; browser page errors: 0.
- A source identity metric snapshot was readable immediately before a new identity decision's `available_at` and unavailable immediately after it, using the Neural PIT loader.
- A separate Research process resumed an orphaned `waiting_confirmation` job with no pending request. The queued state persisted after reopening the database session; stale lease and attempt token were cleared.
- No bet rows were created. Decision requests left the ledger count unchanged in the isolated API run. Backend startup's initial-deposit rows are separate from confirmation actions.

## Real linked match and PBP document

The previously reviewed Fonbet event `91f04ffb-700e-478b-a72e-d71ce213ce53` is finished 2:1 (`7-6, 1-6, 7-6`) and remains linked to Flashscore match `A5F0BCMP`. The database contains 22 PBP documents and 22 parsed history versions. The latest v5 versions cover all three sets, each reports `complete=true`, no gaps, and zero ambiguous points; terminal set scores match the Fonbet result. These latest versions are marked retrospective/availability `known`, so this confirms a complete saved PBP document but does not certify point-in-time PBP availability.

## Defects fixed

The isolated E2E run exposed two backend defects. The confirmation endpoint now flushes the terminal request status before checking for other pending candidates, so rejecting a candidate queues the player's next search. After acquiring the subject advisory lock, it now refreshes the request from PostgreSQL before validating its version; concurrent stale decisions therefore return `409`. The rebuilt backend was deployed and is healthy. No database migration was required.

## Explicit format source remains unavailable

A fresh read-only check at 15:36 UTC found no `best_of`, `best_of_available_at`, or `event_format` in the 100 most recently updated tennis event metadata rows. The latest saved raw-source audit (2026-10-22) also found no explicit format fields in 50 Fonbet payloads / 3,079 event rows. `best_of` remains unknown and no format inference was used; the format-gated forecast E2E remains open.
