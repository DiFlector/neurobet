# Независимый tennis baseline v2 — проверка реализации и данных

Проверка: 2026-10-25 UTC. Production DB; никаких синтетических строк не создавалось.

## Реализация

- Входной контракт baseline: `tennis_odds_free_v2`, версия `tennis_v1.1.0`. Старый контракт v1 и ранее записанные прогнозы сохранены.
- Причинный replay рассчитывает общий и поверхностный Elo, Glicko-2/RD, rolling win-rate по 10/20 матчам и 90/365 дням, а также opponent-adjusted serve/return с явными объёмами и нейтральными priors.
- Forecast использует равновесную смесь доступных компонент рейтинга/формы и Markov-расчёт счёта. PIT provenance включает доступность участника, snapshots, рейтинга и статистики, версии и digest формулы. Формат матча остаётся fail-closed, если PIT `best_of` не подтверждён.
- Фоновый materializer подключён к `external-tennis-sources`. Он пишет неизменяемые canonical snapshots и ranking observations. Изменение результатов, identity, serve facts и относящихся к ним профилей двигает refresh generation; последующий replay пересчитывает затронутую историю для всех зависимых игроков. Миграции 047–050 применены, Alembic head: `050_tennis_ind_fact_dirty`.

## Production data

Завершённый materializer v2 сообщил:

```text
input generation: 5352
players replayed: 89963
confirmed targets: 80
snapshots written: 80
dependency digest: c83716680d5a57db69eb62d009e77325b2e6663e309b6234e8d59cd2ae2c052b
available_at: 2026-10-25T07:20:37.393934Z
```

В БД после прогона: 80 snapshots с `metrics_version=3` и `formula_version=tennis_independent_metrics_v2`; 26 имеют наблюдённый объём подачи/приёма, 76 — матчи в 90-дневном окне. Доступные знаменатели в оставшихся snapshots не выдумываются. Сохранено 10 ranking observations для 10 подтверждённых игроков; у каждой есть `available_at`, сохранённый source reference и SHA-256 content hash. Для live ranking `available_at` задан фактическим временем получения, а не архивной `ranking_date`.

После прогона новые production обновления подняли refresh generation выше 5352; завершённый snapshot фиксирует согласованный digest своего входа, а более позднее поколение остаётся ожидающим очередного фонового replay. Это не меняет уже записанные snapshots.

## Проверки

- Контейнерный regression набор `tests/neural/test_odds_free_tennis_forecast.py` и `tests/sports/test_tennis_independent_metrics.py`: **26 passed**.
- Проверены PIT-границы, симметрия A/B, одинаковое pre-match состояние рейтингов обеих сторон, neutral serve/return priors, доступные и отсутствующие компоненты, поверхность и 90-дневная форма. A→B→A остаётся тремя PIT-версиями; unit regression подтверждает изменение Elo зависимого соперника после исправления и возврат к исходному результату после исправления обратно. Потоковый production replay проверен на совпадение с дедуплицирующим replay.
- DB-триггер refresh проверен на реальной canonical match строке в rollback-only транзакции: noop update увеличил generation внутри транзакции на 1; после rollback generation вернулась к исходной. Исправлений или тестовых строк в рабочей БД не оставлено.
- Forecast writer дважды вызывался для реального Fonbet события в отдельных rollback-only транзакциях с фиксированным cutoff: `tennis_v1.1.0`, одинаковый digest и одинаковые вероятности; матч-win не выдавался при неизвестном формате. Forecast rows не сохранялись.
- Виртуальные операции: до и после восстановления ingestion `bets=1025` (`PENDING=194`) и `ledger_entries=3479`; ставок и проводок при проверке не добавлено. Обычный worker/neural settlement синхронизировал 14 прежних model-journal записей с известными победителями. Обучение, holdout и регистрация модели не запускались.
- При текущей проверке обнаружены остановленные production `collector` и `worker`. Collector завершился после внешнего SIGTERM (exit 0, OOM=false); worker был остановлен с exit 137 (OOM=false). Actor SIGTERM не сохранился в Docker events, а journal требует недоступного sudo-доступа. Compose restart policy обоих сервисов изменён на `always`, сервисы запущены.
- После запуска collector получил Fonbet HTTP 200 и разобрал 22 live tennis events; worker записал свежие события/odds. `collector`, `worker`, `neural`, `backend`, `frontend` healthy; collector и worker оставались running не менее 5 минут при контрольном срезе. Feed показывает новые snapshots с интервалом 5–10 секунд.
- Свежая API/UI browser-сверка прошла для события `69293289` (Срират Т — Ватанабэ-эрикссон А). API вернул `tennis_v1.1.0`, feature contract `tennis_odds_free_v2`, `observed_at=2026-10-25T07:48:05Z`, вероятности next point `0.62`, next game `0.775863`, current set `0.065789`; Chromium UI в карточке показал округлённые `62% / 78% / 7%`. В обоих слоях match-win недоступен из-за `MATCH_FORMAT_UNKNOWN`. Браузер показал также ограничения данных: одна профильная история stale, у второй стороны отсутствует canonical PIT snapshot, serve volume равен 0, data quality 0%; missingness не скрывается и прогноз не выдаёт match-win.

## Результат

Пункт Phase 39 о расширении odds-free baseline независимыми PIT Elo/Glicko, рейтингом тура, rolling win-rate и скорректированной подачей/приёмом закрыт по реализации, unit regression, фактическим production snapshots и свежей API/UI browser-сверке. Явный `docker stop` всё ещё может остановить сервис несмотря на `restart: always`; доступный audit trail не позволил установить инициатора предыдущего SIGTERM.
