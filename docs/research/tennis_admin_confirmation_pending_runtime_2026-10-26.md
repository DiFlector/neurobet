# Admin confirmation runtime check — 2026-10-26

## Scope and result

Checked repeated Flashscore executor scheduler cycles, deployed the requested four services, and inspected the production confirmation/job queues before and after rollout. Production had no pending manual confirmation, so an approve/reject race and continuation from a real pending request could not be exercised safely. The roadmap item remains open for that acceptance case.

## Production snapshot

The pre-deployment census found 0 pending requests. The post-deployment census and repeat found:

- `manual_confirmation_requests` with `status='pending'`: 0.
- Jobs in `waiting_confirmation`: player refresh 0, match lookup 0, point refresh 0.
- In two post-deployment read-only snapshots, request / decision totals stayed 490 / 491 and bets / ledger entries stayed 1,025 / 3,479.

No request was fabricated or reset to pending. No decision or betting write was issued by this check.

## Build and deployment

Built and deployed `backend`, `research`, `flashscore-worker`, and `frontend` on 2026-10-26. Running container image IDs match the images just built:

| Service | Image ID | Health / smoke |
|---|---|---|
| backend | `sha256:aba3157bb87472971b38bfb88274d5e9e7f2eacb4c7b0a9e19d5a80550c6c6bd` | healthy; `/health` 200, database connected |
| research | `sha256:88fe2bcad29e38bf2e255e38302db1e94b4ff7c85d82a509bc2075ccf5d13667` | healthy; `/health` 200 |
| flashscore-worker | `sha256:8460ba2bb93bd3655b10dbe755fb80be8c5277822ce3a6a3b6df7b9148371da1` | healthy; dedicated executor started with 2 browser processes / 1 live-PBP lane |
| frontend | `sha256:001287e1ca5f958cdeb215cde6b6802964f5d22c388fe47539e18ff470736b3d` | healthy; configured `/diflector/neurobet` route 200 |

`docker compose config -q` had passed before deployment. All four recreated containers reached healthy status.

## Scheduler and executor

Worker logs after the restart show repeated point-history dispatches and two preparation cycles (`Prepared eligible tennis events: 11` at 02:53:18 and 02:54:25 UTC), as well as repeated current/backlog dispatch iterations. The durable queues remained active after executor restart; the live PBP task was reported active and normal background profile search resumed. The waiting-confirmation job tables remained empty, so this does not prove recovery of a real pending confirmation's dependent job.

Targeted recovery/evidence tests passed in the research image: 7 passed (`test_confirmation_recovery.py`, `test_manual_profile_resolution_evidence.py`). The previous isolated admin E2E report records a two-request race on one versioned fixture returning one 200 and one 409, plus decision-triggered queue continuation: [`tennis_admin_confirmation_e2e_2026-10-25.md`](tennis_admin_confirmation_e2e_2026-10-25.md). This is supporting isolated evidence, not a fresh race against a production pending request.

## Remaining acceptance

When a real `pending` request exists, repeat the current acceptance on that request: concurrent approve/reject attempts, verify exactly one terminal decision, verify the pending job has no executor lease/slot, and confirm the dependent PBP/search resumes after the decision. Until then, keep the Phase 53 checkbox open.
