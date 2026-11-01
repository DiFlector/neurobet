# API-Tennis ID conflict reconciliation — 2026-10-28

## Result

Reviewed 62 completed Fonbet events with conflicting or unconfirmed API-Tennis player IDs. The projection resolver was extracted into a shared function and used to compare each provider ID against established canonical player identities. The dry-run plan assigned a target only when that resolver agreed with the confirmed match identity and the mapping was consistent across every reviewed event.

The evidence-backed plan covered 83 unique API player IDs: 76 existing aliases were reassigned, one missing alias was confirmed, and six were already correct. Each change has an append-only decision record with its prior owner, evidence, raw hash and decision availability time. As-of reads continue to return the old identity before the decision and the corrected identity from the decision onward.

Five provider IDs remain unresolved because the resolver has no independent canonical peer for those API-only player cards: `2910`, `37903`, `1960`, `10502`, and `73`. Their links remain excluded. No identity was inferred from event order, score, date or a name-only match.

## Recovered history

After refreshing the API-Tennis link dry-run, 51 of 131 reviewed candidates were ready. Fifty append-only point-history links were added; one was already present. The linked rows preserve API-Tennis's partial/retrospective status and became available only after the identity decisions.

The bounded full-tape pass completed all 51 events and scanned 34,418 saved snapshots. It found usable point and game coverage in all 51 events and set coverage in 48. Against the same-policy baseline for this linked subset, full tapes added 32 point, 7 game and 6 set matches. The audit index grew from 6,457 to 6,502 snapshot keys.

The refreshed saved-cohort audit loaded every one of 6,502 indexed snapshots and found no missing keys. It reports point labels on 130 matches (3,143 training examples), game labels on 131 (1,720), set labels on 2,937 (6,226) and match labels on 3,210 (3,210). Compared with the previous saved-cohort report, point/game coverage is now 130/131 rather than 91/92. This is a saved-cohort revalidation, not a full database census.

After deployment, a new dry-run against all 131 candidates reproduced the same reason counts and the same 51 ready links. Reapplying the link plan classified all 51 as already existing; reapplying the identity plan classified all 83 provider IDs as already correct. No duplicate identity decisions or history links were added.

## Remaining blockers

Admission is still `blocked`; thresholds remain 500 initial training matches, 100 in each of four validation windows, and 200 holdout matches. For point/game, chronological training counts are `0/0/0/19` and `0/0/0/21`; the validation windows contain only `8/3/14/19` and `8/3/14/20` matches. Neither target meets the minimums. There are 283 result labels without proven availability time, 100 indexed snapshots lack numeric state fields, and the full database census remains open. Holdout quality metrics were not opened. No model was trained or released.

Other link-review outcomes remain explicit: 66 candidates have no API-Tennis match, one API match is ambiguous, one provider match is missing or ambiguous, six have unknown official final-set state, and one lacks confirmed game coordinates.

## Reproducibility and checks

The saved report cutoff is `2026-10-28T05:25:39Z`; the expanded index SHA-256 is `047e54e18bc26196e797cbd5bf078e604fb23c16aeb059d2c5e56eaaaf72939e`; the training manifest digest is `73506f7c0fe7949d8990dacbbac9c8e07985022300b24ac13e74ab8579e9d570`.

The canonical alias, projection, history-linking, coverage and label-audit tests passed: 103 passed. Research, Flashscore worker and external-tennis-sources were rebuilt from the same image `sha256:f7f4ba0fc402ea377e41389fe53eafb24aa34ffabf52435e2f00c0d7a520c139`; all three are healthy, and the shared resolver imports successfully in each. Full candidate tapes, checkpoints, reports, manifest, evidence plan and repeat-apply results are stored in [`tennis_api_alias_reconciliation_2026-10-28/`](tennis_api_alias_reconciliation_2026-10-28/), with a [compressed evidence archive](tennis_api_alias_reconciliation_2026-10-28_artifacts.tar.gz) and [SHA-256 manifest](tennis_api_alias_reconciliation_2026-10-28_manifest.json). The reports distinguish successful identity repair from the still-closed training gate.
