# PIT истории и новых теннисных срезов — 2026-10-27

## Итог

Найдена ошибка в историческом разрешении identity: аудит выбирал первое append-only решение вместо последнего решения, доступного к snapshot cutoff. Исправление добавлено и покрыто regression-тестом. Повторная проверка полного сохранённого индекса не восстановила приемлемые признаки для старой когорты.

| Проверка | Результат |
|---|---:|
| Исходная историческая когорта | 7,792 примера / 3,207 событий |
| Snapshot ключи повторно загружены из БД | 6,429/6,429; пропусков нет |
| Strict audit PIT | 0/7,792 |
| Strict odds-free builder | 0/7,792 |
| Отдельный новый срез реального завершённого матча | 1/1 PIT accepted |
| Обучение и оценка holdout | не запускались |

Новый принятый пример — отдельный реальный срез Fonbet события `69276687` / `3d593f5a-6545-4ba6-a196-6711add172fd`, сделанный в `2026-10-27T07:17:19.688909Z`, до официального результата в `07:25:29.505528Z`. К моменту среза были доступны все шесть групп: state, format, surface, player metrics и обе serve-метрики. Исправленный audit сертифицировал 1/1, builder принял 1/1. Это подтверждает восстановимый PIT для этого события; оно не меняет результат старой когорты 0/7,792 и не выполняет пороги допуска к обучению.

## Причина и исправление

`tennis_label_audit.py` загружал identity-версии в хронологическом порядке, но брал первую подходящую запись. Для истории, где сначала был `candidate`, а затем `confirmed`, аудит видел только ранний статус и пропускал метрики. Теперь и для участника, и для объединения подтверждённых алиасов выбирается последняя identity-версия с `available_at <= snapshot_at`. Более позднее решение не используется задним числом. Regression test покрывает candidate → confirmed → будущее revoke.

Для старых 7,792 примеров исправление не закрывает основные доказательные пробелы:

- `event_format`: своевременного свидетельства нет в 7,792/7,792;
- `event_surface`: отсутствует явное своевременное значение в 7,792/7,792;
- player metrics: источник наблюдался для 1,299 примеров; 6,493 зависят от запрещённого для PIT contract prior;
- serve A/B: пригодные snapshot доказательства лишь у 20/13 примеров; 7,772/7,779 лишены provenance допустимой стороны/identity;
- event state найден для всех 7,792 примеров, но builder отклонил 556 ссылок из-за отсутствующего raw hash/object;
- 283 примера не имеют времени доступности метки; в части ленты не хватает допустимых live-state признаков.

В этой когорте обновлённый audit materialized features для всех строк, но ни один пример не имеет допустимого полного набора свидетельств. Никакие поздние данные не датировались задним числом.

## Дополнение: диагностика пары каталога и Fonbet — 08:09 UTC

В сохранённом Live Tennis API каталоге найден вероятный upcoming-кандидат, который прежняя диагностика теряла из-за отсутствия заранее подтверждённых canonical aliases: provider match `209627` (Antonia Ruzic — Claire Liu, Guangzhou WTA, BO3/hard) сопоставляется с Fonbet event `69277051` (Рузич А — Лю Клер; Guangzhou; время начала отличается примерно на 6 минут). Новая диагностика сравнивает обе стороны и варианты порядка имён, добавляет такие пары в `name_candidates` и кэширует повторяющиеся сравнения.

Это только указатель для identity/profile follow-up. Fuzzy name score не подтверждает identity, не создаёт alias и не разрешает записать format/surface как PIT-факты. Из-за отсутствия своевременного подтверждения эта уже прошедшая пара не восстанавливает исторические примеры и не подтверждает будущие срезы. Для нового матча потребуется получить подтверждённые source-profile/identity до среза; иначе он остаётся incomplete.

Runtime-проверка нового resolver на этой сохранённой записи проверила SHA-256 исходного raw page и выполнила projection в rollback-транзакции: confirmed pairs `0`, name candidate `1`, observations inserted `0`. Артефакт: [`tennis_live_catalog_pair_candidate_2026-10-27.json`](tennis_live_catalog_pair_candidate_2026-10-27.json).

## Сбор новых срезов

Live Tennis API обходит все страницы live и upcoming singles. Последний завершённый проход в 07:28 UTC сохранил live 30/30 на одной странице и upcoming ближайших 48 часов 457/457 на трёх страницах. Для страниц записаны параметры запроса, время получения, SHA-256, raw key и checkpoint. В этом проходе Fonbet-пары не подтверждены; ранее подтверждённая пара использована только с её сохранённым timely observation.

Neural сохраняет readiness вместе с каждым создаваемым прогнозным snapshot и проверяет шесть групп относительно времени самого snapshot. Исследовательский rolling report считает старые snapshots без readiness неполными. Срез 07:17:19 был восстановлен из сохранённого production Fonbet snapshot для обычного forecast пути; forecast запись сделана до окончания матча. После поступления official result он прошёл strict audit и builder. Синтетических матчей, ставок и ledger-записей не создавали.

### Естественные forecast-срезы после rollout

Проверка живой БД за 15 минут до 07:54:37 UTC нашла **144 автоматически записанных snapshot по 10 событиям**; **144 incomplete, 0 PIT-ready**, и **0 срезов до scheduled start**. Format и surface отсутствовали в 141 срезе каждый; serve evidence отсутствовала в 122/123 срезах A/B. Identity не подтверждена на стороне A в 32 и стороне B в 52 срезах. Это подтверждает работу естественного forecast E2E и одновременно показывает, что новые матчи автоматически не проходят PIT, пока данные не пришли.

Проверен raw Fonbet payload события `69347553`: он содержит ID, имена сторон, `startTime` и `place`, но не содержит best-of, court или surface. Ingestion path уже записывает immutable observations, когда Fonbet явно передаёт format/surface и источник content-addressed; в этом payload таких полей нет, поэтому из него эти признаки восстановить нельзя. Для игрока B Flashscore identity search завершился `SEARCH_COMPLETED_NO_MATCH`, а PBP job ожидает `PLAYER_SOURCE_IDS_MISSING`. Последний полный Live Tennis API catalog не имел подтверждённых Fonbet пар. Разбирать счёт или турнир как замену явным PIT-наблюдениям нельзя. Подробный runtime probe: [`tennis_pit_natural_snapshot_probe_2026-10-27_0754.json`](tennis_pit_natural_snapshot_probe_2026-10-27_0754.json).

Свежий rolling probe в 08:13 UTC охватил 48 часов: **18,319 snapshots, 1 с полным prelabel-readiness, 18,318 incomplete, 0 готовых до scheduled start**. Из 18,319 записей у 17,644 проверка ещё не записывалась; это старые snapshots, не новые провалы. Среди проверенных readiness payloads всё ещё преобладают пропуски своевременного format/surface, serve denominators и отдельных identities. Единственный ready-срез был после scheduled start, поэтому он не доказывает подготовку upcoming-матчей до начала. `certified_examples=0` в rolling report: метка и strict builder считаются отдельно. Артефакт: [`tennis_pit_natural_snapshot_probe_2026-10-27_0815.json`](tennis_pit_natural_snapshot_probe_2026-10-27_0815.json).

## Артефакты

- Полный пересчёт исходной когорты: [`tennis_pit_historical_7792_identityfix_2026-10-27.json`](tennis_pit_historical_7792_identityfix_2026-10-27.json), snapshot index [`tennis_pit_historical_7792_snapshot_index_2026-10-27.jsonl`](tennis_pit_historical_7792_snapshot_index_2026-10-27.jsonl), examples [`tennis_training_examples_fd4961d0ef5a.jsonl`](tennis_training_examples_fd4961d0ef5a.jsonl), builder summary [`tennis_pit_historical_7792_builder_summary_2026-10-27.json`](tennis_pit_historical_7792_builder_summary_2026-10-27.json).
- Реальный принятый срез: [`tennis_pit_live_match_69276687_audit_2026-10-27.json`](tennis_pit_live_match_69276687_audit_2026-10-27.json), исходный training example [`tennis_training_examples_e80253f5a7d7.jsonl`](tennis_training_examples_e80253f5a7d7.jsonl), результат builder [`tennis_pit_live_match_69276687_builder_result_2026-10-27.jsonl`](tennis_pit_live_match_69276687_builder_result_2026-10-27.jsonl).
- Каталог, quota и pre-label coverage: [`tennis_pit_history_new_slices_2026-10-27.json`](tennis_pit_history_new_slices_2026-10-27.json).
- Естественные snapshots и причины отказа по последним 15 минутам: [`tennis_pit_natural_snapshot_probe_2026-10-27_0754.json`](tennis_pit_natural_snapshot_probe_2026-10-27_0754.json).

## Проверки и ограничения

- Research audit regression in the rebuilt, running image: **38 passed**; pagination regression: **56 passed**; Neural forecast/readiness regression ранее: **26 passed**.
- `compileall` и `git diff --check` прошли. Research образ пересобран, контейнер перезапущен и healthy; в контейнере regression suite после rollout: **38 passed**.
- Candidate-name regression suite: **58 passed** (включая Ruzic/Liu с перевёрнутыми русскими метками и отрицательный unrelated-pair сценарий). Research обновлён и healthy; широкий replay 100 каталогов прерван из-за избыточной CPU-нагрузки, он не нужен для изменения identity/PIT.
- Четыре expanding windows и holdout рассчитаны только для структурного admission audit; качество holdout не открывалось. Admission закрыт: PIT по старой когорте 0, выборки меньше 500/100/200, будущие validation labels отсутствуют в train timeline.
- Обучение, SHADOW и promote не запускались.

## Открыто

1. Собирать свежие, естественно возникающие forecast snapshots и подтверждать в rolling report, что они проходят все шесть проверок до метки.
2. Увеличить matching Fonbet ↔ provider и покрытие upcoming до начала матча; не считать непривязанные события готовыми.
3. Продолжить историческое восстановление только по сохранённым timely evidence. При текущем исходном источнике исторический результат остаётся 0/7,792.
4. Повторять strict audit/builder и проверять неизменные admission пороги. PIT-пример 1/1 сам по себе не разрешает обучение.

## Дополнение: архив identity и пакетный profile import — 08:48 UTC

Flashscore search matcher расширен вариантами `Лю Клер` → `Lyu Kler` / `Liu Claire`. Автоматическое подтверждение теперь требует сохранённых provider-response bytes: для участника `Лю Клер` подтверждён Flashscore ID `8KHYNHRA` методом `automatic_exact_full_name` в `2026-10-27T08:42:35.240476Z`. Identity link содержит два запроса (`lyu kler`, `liu claire`), их `fetched_at`, parser version, SHA-256 и MinIO keys. Оба объекта перечитаны, SHA-256 распакованных bytes совпали с сохранённым evidence. Подтверждение действует только с `available_at`; исторические cutoffs до этого времени не меняются. Для `Рузич А` Flashscore поиск завершился `SEARCH_COMPLETED_NO_MATCH`, поэтому её identity не создана.

Первый профильный import Liu сохранил raw страницы, но при пакетной записи 411 матчей столкнулся с PostgreSQL deadlock на row-level триггере `mark_tennis_independent_metrics_dirty`. Миграция 059 перевела dirty triggers canonical matches/aliases, player profiles, identity links и result facts на `FOR EACH STATEMENT`. Возобновлённый import после миграции завершён: 411 матчей, `metrics_version=2`, `sync_status=available`, refresh job `completed`; deadlock повторно не возник.

После обновления профиля повторный rolling probe (окно 48 часов, generated `2026-10-27T08:48:33.730437Z`) содержит 18,923 snapshots: 1 prelabel-ready, 18,922 incomplete, 17,314 без readiness-поля, 0 ready до scheduled start. Готовый срез относится к ранее проверенному матчу и не является новым доказательством pre-start покрытия. Новая identity и профиль были получены после события `69277051`, поэтому его ранние snapshots не проходят PIT задним числом. Полный per-example report: [`tennis_pit_natural_snapshot_probe_2026-10-27_0849.json`](tennis_pit_natural_snapshot_probe_2026-10-27_0849.json).

Исходная когорта 7,792 не меняется от этих новых данных и остаётся закрыта без backdating. Повторный audit по точному snapshot index завершён в 08:52 UTC: 7,792 примера, 6,429 snapshots загружены без пропусков, PIT 0/7,792. Strict builder также принял 0/7,792; основные отказы — format 7,792, surface 7,792, contract prior player metrics 6,493, serving evidence A/B отсутствует для 7,772/7,779, 556 event-state raw evidence incomplete, 283 недатированных label availability. Audit SHA-256 `8fb04cfb06e5ad7f2269495ba37d5236be12305f30225ad7b20fe118d3e7b399`; builder summary `docs/research/tennis_pit_historical_7792_post_identity_builder_2026-10-27.jsonl.gz.gz.gz.summary.json`. Обучение и оценка качества holdout не запускались.

## Дополнение: безопасный поиск кандидата Pohankova — 09:12 UTC

Flashscore resolver обновлён до `flashscore_name_match_v3`: для русской `г` добавлен ограниченный вариант поискового написания `h` (для `Поганкова М` запрос `pohankova m`). Это расширяет только поиск, не автоматическое подтверждение identity. Полный набор `test_tennis_flashscore.py` и `test_flashscore_worker.py` прошёл: **107 passed**.

Research пересобран и работает healthy под штатным именем. Повторная подготовка Fonbet event `69313818` показала готовую историю Валентовой (281 матч), но профиль Поганковой остаётся `not_found`. Повторная попытка завершилась `SEARCH_COMPLETED_NO_MATCH`, без кандидата и без identity link. Следовательно, для этой пары пока нет подтверждения provider ID, а свежего PIT-примера не появилось. Автоматическое подтверждение по транслитерационному сходству намеренно запрещено.

Проверки развёртывания: Compose config валиден, `git diff --check` чистый, Research healthy. Исторический strict builder остаётся **0/7,792**; свежие срезы до scheduled start с шестью готовыми группами не доказаны. Цель восстановления PIT не завершена.
