# Восстановление PIT по сохранённой когорте — 2026-10-26

## Результат

Исторические raw-свидетельства состояния восстановлены только там, где совпали исходное время получения, содержимое, событие и повторный разбор Fonbet state. Новые значения и timestamps не подставлялись.

- В индексированной когорте 7,792 примера и 6,707 уникальных snapshot keys; все 6,707 найдены в PostgreSQL.
- Raw-поиск проверил 7,930 объектов. Из них 6,209 точно подтвердили Fonbet live state. Dry-run отклонил 498: 329 неверного типа снимка, 62 несовпадения состояния, 53 отсутствующих/неоднозначных события, 42 несовпадения счёта и 12 без pre-cutoff кандидата.
- В append-only `tennis_event_state_raw_evidence` записаны 6,208 новых связей; ещё одна ранее существовала. В таблице 6,209 уникальных snapshot evidence rows. Повторный commit тестового snapshot вставил 0 строк.
- `LastModified` MinIO ограничивал только поиск кандидатов. Доказанное `available_at` извлечено из исходного payload `collected_at`; raw key/content hash и состояние проверены повторно.
- Общий audit обработал все 7,792 примера. Strict builder (`tennis_odds_free_v1`) принял **0/7,792**, отклонил 7,792, `pit_certified=false`.

## Почему builder не принял примеры

- своевременный `event_format` отсутствует у 7,792 примеров;
- raw evidence для `event_surface` отсутствует у 7,792; contract defaults не разрешены builder;
- source evidence player metrics отсутствует у 6,019; ещё 7,288 примеров используют contract defaults, которые builder не принимает;
- serve evidence и подтверждённая identity отсутствуют для стороны A у 7,778 и стороны B у 7,783;
- у 556 примеров нет точной raw-связи `event_state`, хотя state-поля snapshot доступны самому audit;
- у 15 меток отсутствует доказанное время доступности; builder оставил их отклонёнными.

Итоговый audit насчитал 0 сертифицированных примеров, 0/4 validation windows с минимумом, недостаточный train и holdout. Training admission заблокирован. Dataset builder помечает `training_admission=not_evaluated`; обучение, качество holdout, SHADOW и promote не запускались. Пороговые значения и feature allowlist не менялись.

## Будущие срезы

Research и External Tennis Sources healthy. Rolling audit последних 48 часов запускается регулярно каждые шесть часов. Обновление от 23:30 UTC выбрало 37,300 snapshot keys; 1,581 не вошли в числовую singles source scope, для 35,719 срезов сохранены per-example причины. PIT прошёл 0/35,719. Группы: event state с проверяемым raw — 5,485; timely format — 33; surface — 0; player metrics с source observation — 4,202; serve A/B — 4,134/3,219. В 31,585/32,500 случаях с имеющимися serve snapshots отсутствует подтверждённая identity provenance; player metrics часто остаются contract defaults. Полный summary и per-example JSONL сохранены в MinIO и `external_tennis_source_state`; ключ summary `rolling-20261026T23`, SHA-256 per-example `cc161ac1c509a097f3324bf868d3924c8f3c62b55277c9c29d57cb0af21d9af3`. Обучающая логика не запускалась.

Сохраненный предматчевый каталог Live Tennis API содержит 100 матчей с явными format/surface, но повторное identity-сопоставление не нашло подтверждённую Fonbet-пару ни для одного из 100. Отдельное сравнение по точным именам проверило 150 открытых Fonbet-событий и также нашло 0 совпавших пар. Поэтому значения не были перенесены на Fonbet события. Дневная квота API на 26 октября осталась исчерпана (80/80); следующий реальный запрос ожидает штатного сброса.

Проверка показала, что выбор профилей и кандидатных событий включал старые `ext_*`/тестовые записи с `is_live=true`, хотя они давно не обновлялись. В SQL worker добавлены ограничения на реальный числовой `source_event_id` Fonbet и актуальность live-среза (`last_seen_at` не старше 90 секунд). В текущем окне это сокращает профильную когорту до 34 участников из 17 numeric Fonbet матчей; текущий каталог с ними не совпадает, поэтому identities по нему не подтверждены. Предметные тесты проверяют source/ID/freshness фильтры; модуль — 54 passed.

Для 12 самых свежих snapshots реальных numeric Fonbet live матчей проведён отдельный точный re-audit из PostgreSQL; все 12 snapshot keys найдены, 11 входят в source scope, PIT принят **0/11**. По ним event_state подтверждён 11/11, но format 0/11, surface 0/11, player metrics source-observed 3/11 (8 defaults), serve evidence A/B — 1/11 и 0/11; 10/11 и 11/11 соответственно не имеют подтверждённой identity provenance. Per-example артефакт и его сводка: [`tennis_fresh_live_pit_cohort_2026-10-26.json`](tennis_fresh_live_pit_cohort_2026-10-26.json), [`tennis_fresh_live_pit_cohort_2026-10-26.jsonl`](tennis_fresh_live_pit_cohort_2026-10-26.jsonl). Обучение не запускалось.

## Проверки

- Migration `058_tennis_state_raw_evidence` применена; constraint `available_at <= snapshot_observed_at` и trigger запрещают UPDATE/DELETE.
- Raw evidence + audit tests: 42 passed.
- Neural odds-free forecast/replay tests: 32 passed.
- Sample commit: 1 inserted; повтор: 0 inserted; production table count: 6,209 уникальных строк.
- Полный historical audit: 7,792 checked; strict builder: 0 accepted.
- Rolling production coverage: 35,321 snapshots checked, 0 certified, per-example artifact archived.
- Compose config и `git diff --check` прошли до финального обновления документации.

## Приоритет каталога и квота Live Tennis API — 2026-10-26

Проверка состояния провайдера выявила, что каталог upcoming, поиск/загрузка профилей и рейтинги делили один дневной лимит в 80 вызовов. В production он был исчерпан до 23:00 UTC, поэтому следующий дневной каталог не мог получить приоритет перед вспомогательными запросами.

Worker теперь резервирует общий лимит непересекающимися категориями: каталог — 24 вызова/сутки, профили — 40, рейтинги — 16; общий предел 80 и минутный предел 24 сохранены. Вызов каталога перенесён перед объёмным API-Tennis archival job в итерации worker. Профильный запрос и его detail-запрос оба учитываются в profile bucket. Сброс квоты или обход ограничения не выполнялся.

Проверки: `tests/research/test_external_tennis_sources.py` — **54 passed**. Новый External Tennis Sources образ healthy. Исправлен и развёрнут лог rolling-аудита: он теперь читает `snapshot_source_coverage` и пишет фактические `indexed_snapshot_keys`, `certified_examples` и причины по source groups (прежде лог ошибочно выводил `accepted=None groups={}`, хотя сам отчёт сохранялся правильно). При исчерпанной дневной/категорийной квоте worker теперь планирует повтор на границе следующего UTC-дня; минутное ограничение перепроверяется на следующей минуте. Квота API не сбрасывалась; получение следующего каталога ожидает 00:00 UTC 27 октября. Сам приоритет квоты не восполняет отсутствующие raw-наблюдения, своевременные identities, профильные метрики, serve evidence или surface.

## Артефакты

- [`tennis_state_raw_evidence_full_dry_2026-10-26.json`](tennis_state_raw_evidence_full_dry_2026-10-26.json)
- [`tennis_state_raw_evidence_full_commit_2026-10-26.json`](tennis_state_raw_evidence_full_commit_2026-10-26.json)
- [`tennis_label_audit_raw_recovered_2026-10-26.json`](tennis_label_audit_raw_recovered_2026-10-26.json)
- [`tennis_training_examples_ca9aa3632307.jsonl`](tennis_training_examples_ca9aa3632307.jsonl)
- [`tennis_dataset_raw_recovered_2026-10-26.jsonl.summary.json`](tennis_dataset_raw_recovered_2026-10-26.jsonl.summary.json)
- [`tennis_dataset_raw_recovered_2026-10-26.jsonl.gz.gz`](tennis_dataset_raw_recovered_2026-10-26.jsonl.gz.gz)

## Follow-up после сброса квоты — 2026-10-27 00:00 UTC

Обновлённый worker выполнил каталог в 00:00:05 UTC без ручного сброса или превышения лимита. Получено и захешировано 100 upcoming matches; все 100 пока `identity_unresolved`, `metadata_written=0`, новых format/surface наблюдений нет. За окно потрачено 1/24 catalog и 3/40 profile calls внутри общего лимита 4/80. Один профиль (`Ozan Baris`, source player id 5081) был точно подтверждён, второй поиск завершился неоднозначно; replay каталога не нашёл полного события с обеими подтверждёнными сторонами. Следующая попытка продолжит по расписанию.

Это подтверждает восстановление автоматического сбора после квотного сброса, но не PIT-прохождение. Admission не менялся и обучение не запускалось.

В 00:01 UTC 27 октября повторно проверены 12 свежих numeric Fonbet live-срезов: загружены 12/12 ключей, 11 в source scope, **0/11 PIT**. Формат теперь подтверждён у одного среза; format у 10, surface у 11, serve A/B у 10/11 остаются без evidence, метрики игроков подтверждены у 3 против 8 contract defaults. Это движение одного признака без полного принятого примера. Отчёт и per-example причины: [`tennis_fresh_live_pit_cohort_2026-10-27.json`](tennis_fresh_live_pit_cohort_2026-10-27.json), [`tennis_fresh_live_pit_cohort_2026-10-27.jsonl`](tennis_fresh_live_pit_cohort_2026-10-27.jsonl).

## Повторный rolling-аудит — 2026-10-27 00:03 UTC

По запросу выполнен тот же production PIT resolver на срезах за последний час. Он индексировал 735 snapshot keys в 16 событиях, загрузил все 735 без пропусков; 45 исключены за пределами numeric singles source scope, 690 проверены по полному feature contract. Сертифицировано **0/690**. `event_state` подтверждён для 690/690, `event_format` — 3/690, `event_surface` — 0/690; `player_metrics` имеют своевременный snapshot у 175/690, остальные 515 используют contract defaults; `serve_metrics_a` подтверждены у 57/690, `serve_metrics_b` — у 0/690. Ни один пример не прошёл все обязательные группы.

Отчёт: [`tennis_pit_rolling_coverage_2026-10-27_0003.json`](tennis_pit_rolling_coverage_2026-10-27_0003.json). SHA-256 per-example gzip JSONL: `8ae0afb6eb448d2449a2d29cc6b0b181a5bc2fcad29a09d0c935ebf88c1b8023`; объект хранится в MinIO по ключу из JSON. Это подтверждает работу общего PIT resolver, raw-свидетельств и аудита будущих срезов, но не подтверждает PIT-прохождение. Повторный production-аудит не запускал обучение и не открывал holdout quality.

## Повторный точный аудит исторической когорты — 2026-10-27 00:07 UTC

Повторно проверен тот же индекс из 7,792 примеров и 6,706 уникальных snapshot keys; все ключи найдены в БД. Индекс построен из сохранённого training-example артефакта, включает отдельные result-time evidence snapshots, SHA-256 индекса `8deeb6aa978c2a9bf74deb4ee8c3a1e966bb4717f6776ab902805d5577c63b2e`. PIT осталось **0/7,792**. Никакое из новых наблюдений формата/поверхности не было доступно к моментам прогнозных срезов этой исторической когорты: `event_format` missing/unverifiable 7,792/7,792, `event_surface` использует unknown default 7,792/7,792. Своевременный `event_state` есть у 7,792; source-observed player metrics — у 504; serve A/B — у 14/9. Обучение не запускалось; PIT и все admission-гейты остались закрыты.

Машинный отчёт и полный admission summary: [`tennis_label_audit_2026-10-27_reaudit.json`](tennis_label_audit_2026-10-27_reaudit.json), SHA-256 `26954b0bf86895a7e351d40133a8cdabda411c4f50eabdafbf0608ee3d1a6ee4`. Это точный повторный audit по ранее сохранённой когорте, не census новых матчей.

## Проверка доступного live-каталога и свежих срезов — 2026-10-27 00:12 UTC

Raw Live Tennis API каталог от 00:00 содержит явные format и surface у **100/100** матчей. Парсер извлёк metadata у 100/100, но безопасная проекция в Fonbet events записала **0**: все 100 остались `identity_unresolved`, потому что source IDs каталога не имели подтверждённой связи с точной парой текущего Fonbet события. Проверка provider alias numeric IDs показала namespace collision между Live Tennis API ID и API-Tennis ID; совпадение числа не является допустимым identity evidence. Дополнительный read-only кросс-поиск по подтверждённым TML alias/name дал 6 пар каталога, но ни одной подтверждённой пары среди ближайших Fonbet событий. Автоматическое связывание не расширялось по одному имени или по совпадающему ID.

После новых API-запросов rolling-аудит окна 23:12–00:12 проверил 688 срезов из 742 загруженных ключей; 54 исключены по source scope, пропущенных snapshot keys нет. PIT — **0/688**: event state 688/688, format 3/688, surface 0/688, player metrics 178/688 (510 defaults), serve A/B 49/0. Персональные причины сохранены в MinIO, сводка [`tennis_pit_rolling_coverage_2026-10-27_0012.json`](tennis_pit_rolling_coverage_2026-10-27_0012.json). Это подтверждает, что автоматический resolver отклоняет неполные данные; нынешний upstream-каталог не покрывает идентичностями текущую Fonbet когорту.

Проверки текущих PIT-контрактов: `tests/research/test_odds_free_dataset.py` и `tests/research/test_tennis_label_audit.py` — **50 passed** в Docker Research. Включены положительная фикстура со всеми evidence-группами, источники до/после cutoff, поздняя identity, отсутствующие/несовпадающие raw hashes, поздняя label и запрет defaults. Значит builder умеет принимать полностью доказанный пример и отказывать при нарушениях; production data не предоставляет полного набора своевременных свидетельств.
