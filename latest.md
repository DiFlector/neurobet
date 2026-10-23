# Latest status — independent odds-free tennis forecast

**Updated:** 2026-10-22 16:40 UTC
**Admission:** `blocked`. Не запускать обучение LR/GB/GRU, оценку кандидатов, просмотр метрик holdout или регистрацию SHADOW.

## Последний полный допуск

Read-only audit cutoff — `2026-10-22T15:39:52.074048+00:00`, завершён `2026-10-22T16:22:11.712982+00:00` за 2539.6 с. Статус `blocked`. Отчёт [`docs/research/tennis_label_audit_20261022T1540Z.json`](docs/research/tennis_label_audit_20261022T1540Z.json), SHA-256 `658b31c99e013cea001c09ea8ce67bf3c46ee18ba72218e7f50a0db341231660`; execution [`docs/research/tennis_admission_execution_2026-10-22_1539.json`](docs/research/tennis_admission_execution_2026-10-22_1539.json), SHA-256 `0ca0475dc246e03901d9c834fb6f24ac96d6a96dc3fcd46e2c14a75a00dd9262`.

Manifest [`docs/research/tennis_training_manifest_af309e711ba0.json`](docs/research/tennis_training_manifest_af309e711ba0.json): 2,588 матчей (2,070 development / 518 sealed holdout), digest `af309e711ba07b18d021c6c6e48a22fbcef3ed6c6f06f4743d293b139df654a7`, SHA-256 `1691988dfb2c174d18520421dc0fa8db7c01cefdc3e00f738ab07590ccaa0ac8`. Provenance [`docs/research/tennis_training_examples_3fdc5e29899b.jsonl`](docs/research/tennis_training_examples_3fdc5e29899b.jsonl): 6,083 примеров, SHA-256 `a91a4277f424dc9923b1c3fe3106ec895b80463325e4b618ae56be0ded4d6d43`.

- Labels point/game/set/match: 64/64/63/2,603; causal train — 0 во всех folds; validation P/G/S — 3/2/2/4, holdout — 24 каждый. Match validation — 207 в каждом fold, holdout — 518, но причинный train также 0.
- Feature PIT — 0/6,083; `event_format` не подтверждён к as-of ни у одного примера. Player metrics observed в 788 примерах, остальное contract-defaulted; serve A/B defaulted; surface остаётся explicit unknown.
- Result-time provenance: 2,311 official metadata, 277 exact prior snapshots; 143 недатированных результата исключены. Gate причины: недостаточный coverage P/G/S, train labels позднее validation forecasts и source-time format отсутствует.
- Не обучать LR/GB/GRU, не оценивать кандидатов, не открывать метрики качества holdout, не регистрировать SHADOW. Пороги 500/100/200 неизменны.

## Источники и runtime

- Runtime после аудита: PostgreSQL, Collector, Redis, Flashscore worker, Neural, Backend, Frontend и Research healthy; Backend `/health` — HTTP 200. Flashscore worker работает на двух browser processes и остаётся healthy после rollback.
- Полный PBP audit: [`tennis_live_pbp_ingestion_2026-10-22_1429.json`](docs/research/tennis_live_pbp_ingestion_2026-10-22_1429.json), SHA-256 `7ca4ccb5c624f4c61315d74e792d2cf9efdcb8d44c6bb5f833ecec17ca0b023c`: 107 source matches, 1,587 версий, 37,702 point states; 31 non-retrospective matches. Формат не имеет source-time у live Fonbet событий.
- Phase 50 live alignment прошёл на event `69161612` / Flashscore `AJWwyZCG`: 55 PBP событий, сторона A подтверждена по scoreboard DOM и profile IDs, PBP был на ~34 с старше Fonbet. Артефакт [`tennis_live_pbp_alignment_2026-10-22_1444.json`](docs/research/tennis_live_pbp_alignment_2026-10-22_1444.json), SHA-256 `5c348a890b140b5113a74e11777bdcf43b781082b7cc99f8433c96ae402b542f`.
- Queue soak 14:36–15:08 UTC: 31 samples за 31.6 минуты; версии PBP 1,596→1,652, source matches 107→113, live confirmed links 0→2. `waiting_players` 2,676→2,678, поэтому архивный backlog не уменьшился. Отчёт [`tennis_queue_runtime_soak_2026-10-22_1436_report.json`](docs/research/tennis_queue_runtime_soak_2026-10-22_1436_report.json), SHA-256 `ae5d45e11134e967dd66c0f9fd7daa85ffd1e64af210d26d528f8cea96693737`.
- Пробный browser budget Flashscore `3 = 1 live PBP + 2 background` остановлен: в коротком soak worker достигал 1.997/2 GiB, 3 browser roots и 329 процессов; source controller использовал не больше одного из двух слотов. Признаков подтверждённого ускорения нет, доминируют identity/index waits. Настройки возвращены к двум browser processes; см. [`flashscore_capacity_trial_2026-10-22_1533.json`](docs/research/flashscore_capacity_trial_2026-10-22_1533.json), SHA-256 `750ed5cb7c5f7df1dd6056c1d9afe035405a0c5f9ae118a9c2c7ab6e7fc2b272`.
- Событие `69155384` не дало PBP: сбор завершился `PBP_TAB_NOT_PUBLISHED` с 0 событий.
- Дополнительный `as_of` аудит event `69187254` / `xb0xhomn` выявил, что aggregate endpoint выбирает retrospective версии; до результата были non-retrospective версии, но set 3 обрывался при `3:3` против официального `6:4`, несмотря на `complete=true`. Матч исключён из полного PBP alignment и подтверждённых P/G/S labels. Артефакт [`tennis_live_pbp_alignment_2026-10-22_1530.json`](docs/research/tennis_live_pbp_alignment_2026-10-22_1530.json), SHA-256 `1f82621f861f2213dd494cb2709f7ce03c8e0f43b2351b85aa57ae07df08d488`.
- Новый read-only snapshot 15:20:59 UTC: 2,675 PBP jobs ждут игроков/связь; 2,507 ждут индексации Flashscore матча и 169 — player source IDs. Player refresh queue: 674 queued, 973 retry, из них 867 `SEARCH_COMPLETED_NO_MATCH`. Оба сетевых слота в этот момент были свободны, поэтому browser budget сам по себе не устраняет основную причину backlog. Детали: [`flashscore_queue_diagnostic_2026-10-22_1521.json`](docs/research/flashscore_queue_diagnostic_2026-10-22_1521.json).
- PostgreSQL на контрольном срезе использовал `0.9/2.5 GiB`, 18/100 соединений, активных блокировок нет, cache hit около 98%, temporary writes 127 MB, deadlocks 0; БД ~6.5 GB. Кратковременно CPU был ~86% квоты 2 vCPU и наблюдался один `WALSync` waiter, но память/соединения/диск не были на лимите. `pg_stat_statements` не включён, поэтому CPU/WAL источник запросов ещё не локализован; Redis и Collector остаются гораздо ближе к собственным memory caps.

- Raw-source формат аудит 15:30–15:38 UTC: проверено 50 последних Fonbet S3 payloads (3,079 event rows), явных format keys нет; metadata в DB — 0/3,847 с `best_of` и `best_of_available_at`. Формат остаётся unknown, поэтому fail-closed forecast ожидаем. Артефакт [`tennis_format_raw_live_source_audit_2026-10-22_1540.json`](docs/research/tennis_format_raw_live_source_audit_2026-10-22_1540.json), SHA-256 `d2c6313309b53041e2e42b360be2a2b0123b2a0cf7bd54ffece397249ec9462c`.

## Свежий сбор PBP и очередь — 2026-10-22 16:32 UTC

Read-only census [`docs/research/tennis_live_pbp_ingestion_2026-10-22_1632.json`](docs/research/tennis_live_pbp_ingestion_2026-10-22_1632.json), SHA-256 `78126b4368e8fac50a0da1346acb0b3cbfc64f98a9a8cf09c860567d89a8ebb5`: 1,768 PBP versions / 117 source matches / 41,532 point states; 401 non-retrospective versions на 34 matches. После admission cutoff добавились 71 version и один match. Всего 1,605 matched-link rows по 120 Fonbet events; source-keyed refresh jobs нет. PBP queue: 2,688 ждут игроков/индексации, 173 retry, 92 queued, 1 running. Profile queue: 596 queued, 1,015 retry, 353 completed, 480 failed, 12 running, 12 waiting for confirmation. В live Fonbet metadata у 3,862 tennis events остаётся 0 явных `best_of`, `best_of_available_at` и `event_format`. В external Flashscore rows 48,275 ITF и 23 junior entries, но точность категорий по raw source не проверена; предыдущий label audit классифицировал 1,932 события как unknown, 771 по Fonbet competition name и 79 по Flashscore archive. Эти данные не обновляли frozen manifest и не закрывают порог PBP в сотни матчей.

## Проверенная спецификация Phase 40

Зафиксированный audit/manifest включает `tennis_odds_free_v1`, четыре цели, exclusions и chronological folds. `test_tennis_flashscore.py`, `test_tennis_label_audit.py` и `test_tennis_point_history.py`: **97 passed**; целевой odds-free forecast suite: **54 passed**. Соответствующий checklist в [`02-ROADMAP.md`](02-ROADMAP.md) закрыт с hashes артефактов. Это не закрывает coverage ITF/juniors, source-time формата или admission gate.

## Дальше по Roadmap

Продолжать накопление source-timed формата, причинной истории игроков/подач, своевременных меток и ручных source reviews; устранить ожидание индексации матчей/player IDs, добиться full PBP с подтверждённой полнотой и проверить queue recovery/admin approve/reject smoke. Повторить полный admission только после накопления новых проверяемых доказательств. Checklist: [`02-ROADMAP.md`](02-ROADMAP.md), Phase 39–53.
