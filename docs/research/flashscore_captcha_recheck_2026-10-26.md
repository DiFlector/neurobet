# Flashscore recheck after CAPTCHA fix — 2026-10-26

## Scope and transport

Bounded production check of the previously blocked Huang Tsung-Hao profile (`AROuX8tf`) and source-only PBP match `juGuZdcD`. No cooldown was changed and no synthetic job was inserted. The previously recorded CAPTCHA failure is no longer reproduced: profile HTML, profile history feeds and PBP feed all returned HTTP 200 over the normal HTTP transport; no CAPTCHA challenge or FlareSolverr recovery was invoked.

The profile was then processed by the штатный `FlashscoreTennisImporter.import_profiles` path. It fetched and archived the profile page and 15 subsequent history pages, but returned `partial`: 281 advertised singles versus 280 unique observed singles, after two empty terminal probes. It parsed 271 winner-verifiable matches, excluded 314 doubles rows and skipped 9 rows whose scores did not prove a winner. The discrepancy is exactly one advertised singles match; the importer correctly did not certify full pagination. There is no evidence yet that this remaining count gap is a parser defect rather than a source-side count mismatch.

## PBP idempotency and saved history

The source-only PBP request returned `available` over HTTP feed transport. It saved 0 new documents because the unchanged content/parser version already exists; the repeat therefore did not create a duplicate. Production contains one `juGuZdcD` document and one immutable version, parser `flashscore-point-history-v7`, 190 confirmed points, zero ambiguous points, no gaps, `complete=true`. It is retrospective and was unlinked from Fonbet when retrieved, so it does not certify PIT availability or a live Fonbet association.

## Queue snapshot

At the check, profile jobs included two terminal-page count mismatches (including the tested profile) and 13 blocked for other identity/archive reasons. Point-history jobs had 20 completed and 3 retry/partial. This is a point snapshot, not queue-throughput evidence. No additional browser slots were allocated.

## Acceptance decision

The CAPTCHA/transport part is now verified as resolved for these targets, and the PBP repeat is idempotent. Keep the broader profile/PBP acceptance open: profile pagination still misses one advertised singles row; no same-FlareSolverr-session retry applies because normal HTTP succeeded and the solver was not used; the stored PBP is retrospective. Continue the queue-progression check separately with a suitable eligible workload.

Raw importer report: [`flashscore_captcha_recheck_2026-10-26.json`](flashscore_captcha_recheck_2026-10-26.json). Full profile import report: [`flashscore_profile_import_2026-10-26.json`](flashscore_profile_import_2026-10-26.json).
