# Остаточные проверки до первого эксперимента — 2026-10-26

Срез: 2026-10-26 02:00 UTC. Обучение, fit, holdout quality, registry и SHADOW не запускались. Production-identity и очередь не изменялись; выполнены только read-only запросы и документационные изменения.

## Recheck after engineering implementation — 2026-10-26

Added `services/neural/app/odds_free_dataset.py` and `scripts/research/build_odds_free_dataset.py`. The standalone builder is not imported by the trainer and only materializes a versioned allowlist candidate or a rejection record. It validates six source-group timestamps against each snapshot, label availability against dataset cutoff, feature names/values, contract-default use, and records provenance and all rejection reasons. Missing values are never filled from inference priors. Five focused tests passed.

Run against [`tennis_training_examples_997715d7d6df.jsonl`](tennis_training_examples_997715d7d6df.jsonl) (7,711 source-audit examples) yielded **0 accepted / 7,711 rejected**, `pit_certified=false`, `training_admission=not_evaluated`. Per-example outcomes and their reasons are saved in [`tennis_odds_free_dataset_2026-10-26.jsonl.gz.gz.gz`](tennis_odds_free_dataset_2026-10-26.jsonl.gz.gz.gz), SHA-256 `fe193772bedd231250cf74658058780d234ad23d1311caf53efe95d5b0ffcf79`; summary: [`tennis_odds_free_dataset_2026-10-26.jsonl.gz.gz.gz.summary.json`](tennis_odds_free_dataset_2026-10-26.jsonl.gz.gz.gz.summary.json). Builder implementation and rejection audit are complete; PIT source coverage, dataset certification and training admission remain blocked independently.

## 1. Восстановленные задания и rejected-кандидат

Восстановление durable jobs после restart подтверждено отдельным отчётом [`flashscore_restart_shared_limit_2026-10-26.md`](flashscore_restart_shared_limit_2026-10-26.md). Повторный read-only запрос к БД показал, что успешного завершения пока нет:

| Job | Текущее состояние | Попытки | Причина |
|---|---|---:|---|
| Profile refresh `ab47f470-6cc2-4e6b-8bdb-75201cebff91` | `queued` | 37 | без last_error; last update/next_run 01:53:13 UTC |
| PBP Fonbet `69171568` | `queued` | 72 | retry time прошёл; job не claimed, сохранён `point_history_tab_missing` |

Read-only запрос 02:00 UTC подтвердил, что CAPTCHA-снятие не завершило целевые задания: профильный job всё ещё `queued` после 37 попыток, PBP job — `queued` после 72 попыток; назначенный retry истёк, но цель не была claimed, а `point_history_tab_missing` сохранён. Worker продолжал dispatch других jobs. Ранее проверенный профиль `AROuX8tf` импортирован частично (280 из 281 заявленных singles), существующий PBP документ retrospective и не привязан к Fonbet. Успешное завершение восстановленных jobs не подтверждено. Полный свежий аудит и hashes: [`tennis_label_audit_2026-10-26.md`](tennis_label_audit_2026-10-26.md).

В коде `_prepare_one_player` передаёт rejected Flashscore IDs как `excluded_source_ids`; при завершённом поиске без результата и наличии rejected IDs job получает `NO_ALTERNATIVE_PROFILE_DATA`. Read-only DB query нашёл 29 persisted jobs с этой причиной; у каждой записи задано ненулевое `excluded_rejected_candidates`, а стадия — `confirmed_not_found` или `candidate_name_mismatch`. У примера `Монсон И` одна rejected link, search diagnostics показывают 3 valid candidates, после исключения rejected identity job получила `NO_ALTERNATIVE_PROFILE_DATA`. Это подтверждает именно no-alternative branch; корректность пользовательского reject API отдельно подтверждена изолированным E2E в [`tennis_admin_confirmation_e2e_2026-10-25.md`](tennis_admin_confirmation_e2e_2026-10-25.md).

**Статус:** эта ветка закрыта. Ожидание успешного завершения восстановленных jobs вынесено в отдельный пункт roadmap и остаётся открытым.

## 2. PIT источники и фактический dataset builder

Последний завершённый feature-source audit [`tennis_feature_source_completeness_2026-10-25.json`](tennis_feature_source_completeness_2026-10-25.json) анализирует 4,826 сохранённых examples:

- полная PIT-сертификация: **0/4,826**; у всех отсутствует доступный на cutoff source timestamp формата события;
- player metrics: источник наблюдался хотя бы на одной стороне в 795 срезах, остальные содержат contract defaults;
- serving: у обеих сторон 0/4,826 наблюдённых source значений и знаменателей; использованы только явно маркированные priors;
- surface: 0/4,826 наблюдённых значений; категория во всех примерах `unknown`;
- установленный источник `best_of`/`best_of_available_at` остаётся обязательным; вывод формата по турниру, полу или счёту запрещён.

Свежий read-only census 2026-10-26 00:54 UTC не нашёл `best_of`, `best_of_available_at` или `event_format` в 100 последних Fonbet tennis events: [`tennis_format_pit_recheck_2026-10-26.md`](tennis_format_pit_recheck_2026-10-26.md).

`scripts/research/tennis_label_audit.py` строит audit examples/manifest, но отдельная сертификация фактического trainer dataset builder не завершена. Стандартный builder `services/neural/app/dataset.py` формирует строки из odds/state snapshots; это само по себе не подтверждает, что production training rows соответствуют odds-free PIT contract. Попытка полного production audit была остановлена при росте PostgreSQL до 2.30/2.5 GiB; безопасный аудит копии БД пока отсутствует. Детали ресурса: [`tennis_label_audit_resource_check_2026-10-25.md`](tennis_label_audit_resource_check_2026-10-25.md).

Свежий безопасный read-only аудит 2026-10-26 завершён на рабочей БД в repeatable-read транзакции с ограничениями ресурса; он не менял production. В 7,710 примерах PIT-сертифицировано 0: player metrics доступны у 491, serve A/B — у 9/6, event state — у всех, event_format отсутствует у всех, surface остаётся contract-default `unknown` у всех. На тот момент `services/neural/app/dataset.py` был legacy priced-snapshot builder; trainer loader не подключали. Последующий отдельный builder реализован и проверен, но намеренно не меняет legacy trainer. В свежем audit recheck 7,711 examples PIT-certified также 0. Подробности исходного аудита и hashes: [`tennis_label_audit_2026-10-26.md`](tennis_label_audit_2026-10-26.md) и JSON/manifest/examples в нём.

**Статус:** builder implementation/check закрыты; PIT источников недостаточно, candidate set отклонён полностью и не сертифицирован. Trainer не подключён, обучение и admission не выполнялись; текущие ретроспективные факты нельзя backdate, пороги не меняются.

## 3. Решение по входному контракту

Для первого эксперимента зафиксирован существующий `tennis_odds_free_v1` без изменения порядка/состава allowlist:

- surface принимается только с собственным `surface_available_at <= cutoff`; иначе кодируется только `unknown`;
- serve принимается только с валидным числителем/знаменателем и PIT-доступностью; иначе — prior `0.62` и `serve_sample_weight=0`;
- никакие current metadata, поздние или ретроспективные значения не подставляются;
- priors/missingness не считаются observed coverage и не удовлетворяют admission-порогам;
- `event_format` остаётся обязательной PIT-группой. Без source `best_of` с доступностью на cutoff данные не сертифицированы, admission остаётся закрытым.

Решение отражено в [`01-ARCHITECTURE.md`](../../01-ARCHITECTURE.md). Оно утверждает только обработку неизвестных полей; допуска к обучению не даёт.

**Статус:** решение по контракту закрыто; data PIT, builder certification и training admission остаются открыты.

## Recheck after feature-vector materialization — 2026-10-26 12:07 UTC

Исправлен инженерный пробел: audit JSONL теперь содержит реальные `feature_values`, материализованные из соответствующего event snapshot и PIT-ограниченных source observations. Для неизвестного или поздно подтверждённого `best_of` `sets_to_win` не подставляется. Focused Research tests — **35 passed**.

Новый read-only audit создал 7,795 provenance examples и materialized vectors для всех 7,795 строк. Это не изменило eligibility данных: `event_format` отсутствует/не имеет causal timestamp у всех 7,795, surface неизвестен у всех, player metrics defaulted у 7,296, serve sides defaulted у 7,780/7,786. Downstream candidate builder принял **0/7,795**; основная причина отказа — PIT format, затем отсутствующие метрики/подача и contract-default surface. Подробная сводка, fold counts, hashes и ресурсные ограничения: [`tennis_label_audit_2026-10-26_materialization_recheck.md`](tennis_label_audit_2026-10-26_materialization_recheck.md).

Admission остаётся `blocked`: point/game/set — 81/82/82 размеченных матча против 500; causal train folds — `0/0/0/23` для этих целей и `0/0/0/277` для match; P/G/S validation и holdout тоже ниже minima. 178 исходов без доказанного timestamp исключены. Holdout quality не открывался, обучение/fit, модельный registry и SHADOW не затрагивались. Следующий шаг — накопить новые source-timestamped event formats и non-retrospective labelled observations и повторить тот же audit без изменения порогов.
