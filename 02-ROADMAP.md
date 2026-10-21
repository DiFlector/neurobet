# FONBET AI — ROADMAP

Документ предназначен для coding-agent. Работу выполнять последовательно, не перескакивая через фундаментальные этапы. Каждый пункт является чекбоксом и должен отмечаться только после реального завершения и тестирования.

---

## PHASE 0 — Project contract and repository

- [x] Создать monorepo `neurobet`.
- [x] Создать базовый `README.md`.
- [x] Создать `docker-compose.yml`.
- [x] Создать `.env.example`.
- [x] Создать отдельные Dockerfile для frontend/backend/collector/neural/llm/research/bet-manager/worker.
- [x] Создать общую Docker network.
- [x] Создать internal-only network для PostgreSQL/Redis/MinIO.
- [x] Настроить restart policy для сервисов.
- [x] Добавить healthcheck для каждого контейнера.
- [x] Зафиксировать Python/Node версии.
- [x] Зафиксировать schema versioning policy.
- [x] Зафиксировать UTC как системную timezone для backend/database.
- [x] Добавить Makefile с командами `up`, `down`, `logs`, `test`, `lint`, `migrate`, `train`.

### Acceptance

- [x] `docker compose config` проходит.
- [x] `docker compose up -d` поднимает инфраструктурные контейнеры.
- [x] Restart одного контейнера не ломает остальные.

---

# PHASE 1 — Contracts

- [x] Создать package `packages/contracts`.
- [x] Описать `Event`.
- [x] Описать `EventState`.
- [x] Описать `Market`.
- [x] Описать `Selection`.
- [x] Описать `OddsSnapshot`.
- [x] Описать `FeatureVector`.
- [x] Описать `MLPrediction`.
- [x] Описать `ResearchEvidence`.
- [x] Описать `ResearchPacket`.
- [x] Описать `LLMDecision`.
- [x] Описать `BetProposal`.
- [x] Описать `BetValidationResult`.
- [x] Описать `VirtualBet`.
- [x] Описать `Settlement`.
- [x] Добавить `schema_version` во все межсервисные сообщения.
- [x] Добавить JSON Schema generation.
- [x] Добавить contract tests.

### Acceptance

- [x] Любой сервис может валидировать сообщение другого сервиса без общей бизнес-логики.
- [x] Некорректный LLM JSON автоматически отклоняется.

---

# PHASE 2 — PostgreSQL + TimescaleDB

- [x] Запустить PostgreSQL + TimescaleDB.
- [x] Настроить volume для persistent storage.
- [x] Создать Alembic migrations.
- [x] Создать таблицу `sports`.
- [x] Создать `leagues`.
- [x] Создать `participants`.
- [x] Создать `participant_aliases`.
- [x] Создать `events`.
- [x] Создать `event_state_snapshots`.
- [x] Создать `raw_snapshots`.
- [x] Создать `markets`.
- [x] Создать `market_selections`.
- [x] Создать `odds_snapshots`.
- [x] Создать `odds_change_events`.
- [x] Создать `feature_snapshots`.
- [x] Создать `ml_predictions`.
- [x] Создать `llm_decisions`.
- [x] Создать `web_research_runs`.
- [x] Создать `web_documents`.
- [x] Создать `web_evidence`.
- [x] Создать `bet_proposals`.
- [x] Создать `bet_validation_results`.
- [x] Создать `virtual_accounts`.
- [x] Создать `ledger_entries`.
- [x] Создать `bets`.
- [x] Создать `bet_settlements`.
- [x] Создать `model_versions`.
- [x] Создать `training_runs`.
- [x] Создать `training_datasets`.
- [x] Создать `experiment_results`.
- [x] Создать `audit_log`.
- [x] Настроить индексы по `(event_id, observed_at)`.
- [x] Настроить индексы по `(sport_code, observed_at)`.
- [x] Настроить индексы для event lookup по source ID.
- [x] Настроить Timescale hypertables.
- [x] Настроить compression policy.
- [x] Настроить retention policy через конфигурацию.

### Acceptance

- [x] Migration с нуля создает всю схему.
- [x] Restart PostgreSQL сохраняет данные.
- [x] Запрос timeline одного event остается быстрым на тестовом датасете.

---

# PHASE 3 — Redis Streams

- [x] Запустить Redis.
- [x] Создать streams `fonbet.raw`, `fonbet.events`, `fonbet.state`, `fonbet.odds`.
- [x] Создать streams `features.ready`, `ml.predictions`.
- [x] Создать streams `research.requests`, `research.results`.
- [x] Создать streams `llm.requests`, `llm.results`.
- [x] Создать streams `bet.proposals`, `bet.validated`, `bet.executed`, `bet.settled`.
- [x] Создать stream `training.jobs`.
- [x] Реализовать message envelope.
- [x] Реализовать idempotency key.
- [x] Реализовать consumer groups.
- [x] Реализовать retry policy.
- [x] Реализовать dead-letter handling.

### Acceptance

- [x] Один message не создает два одинаковых database action при повторной доставке.
- [x] Ошибка worker не теряет message.

---

# PHASE 4 — FON.BET collector foundation

- [x] Создать collector service.
- [x] Подключить Playwright/Chromium.
- [x] Создать browser lifecycle manager.
- [x] Создать configurable page URLs.
- [x] Реализовать graceful shutdown.
- [x] Реализовать `random.uniform(5, 10)` polling.
- [x] Реализовать concurrency limit.
- [x] Реализовать timeout.
- [x] Реализовать retry/backoff.
- [x] Реализовать circuit breaker.
- [x] Реализовать structured logging.
- [x] Реализовать snapshot hashing.
- [x] Создать raw snapshot writer.
- [x] Создать MinIO bucket `raw-snapshots`.
- [x] Записывать `collector_version`.
- [x] Записывать `collected_at`.
- [x] Сохранять URL/page type.
- [x] Реализовать parser tests на сохраненных fixtures.

### Acceptance

- [x] Collector может несколько часов работать без uncontrolled browser process growth.
- [x] Ошибка parser одной карточки не останавливает весь collector.
- [x] Каждая сохраненная запись имеет точный UTC timestamp.

---

# PHASE 5 — First sport adapter: Tennis

- [x] Создать `sports/core` interfaces.
- [x] Создать sport registry.
- [x] Создать `tennis` adapter.
- [x] Извлекать event ID.
- [x] Извлекать tournament / surface (хард, грунт, трава).
- [x] Извлекать players (Player A, Player B).
- [x] Извлекать scheduled start.
- [x] Извлекать live/prematch state.
- [x] Извлекать tennis score (sets, games, current game points: 0, 15, 30, 40, AD).
- [x] Извлекать server (кто сейчас подает).
- [x] Извлекать tennis stats (aces, double faults, break points).
- [x] Извлекать доступные markets (MVP: `match_winner`).
- [x] Извлекать победу в сете 1–5 как `set_winner` с номером сета. Модель этой логики — `tennis_v2.0.0`; её обучают только когда в истории есть свои котировки сетов.
- [x] Извлекать selections (`player_a`, `player_b`).
- [x] Извлекать odds.
- [x] Извлекать market status/suspension.
- [x] Нормализовать имена игроков.
- [x] Сохранить canonical event.
- [x] Сохранить state snapshot (hierarchical: match -> set -> game -> point).
- [x] Сохранить odds snapshot.
- [x] Реализовать result parser.
- [x] Добавить parser fixture tests.

### Acceptance

- [x] Один реальный/сохраненный live event корректно проходит `raw → event → state → odds`.
- [x] Повторный snapshot не создает ложные изменения.
- [x] Изменение odds создает новую историческую запись.

---

# PHASE 6 — Generic sports layer

- [x] Перенести event lifecycle в sport-independent core.
- [x] Оставить sport-specific state в adapter.
- [x] Убрать football-specific assumptions из backend.
- [x] Убрать football-specific assumptions из bet-manager.
- [x] Убрать football-specific assumptions из DB contracts.
- [x] Создать generic participant model.
- [x] Создать market/selection generic model.
- [x] Создать sport-specific label provider.

### Acceptance

- [x] Backend умеет работать с неизвестным sport_code.
- [x] Неизвестный спорт корректно попадает в `UNSUPPORTED` и не ставит.

---

# PHASE 7 — Historical data quality

- [x] Создать data-quality jobs.
- [x] Проверять duplicate events.
- [x] Проверять time order.
- [x] Проверять отрицательные/невозможные odds.
- [x] Проверять невозможные изменения score.
- [x] Проверять clock regressions.
- [x] Проверять missing observations.
- [x] Проверять orphan odds.
- [x] Проверять события без result.
- [x] Добавить data-quality dashboard.
- [x] Добавить quality score на event.

### Acceptance

- [x] Можно получить отчет о качестве накопленной истории.
- [x] Bad rows не удаляются молча; они помечаются и остаются аудируемыми.

---

# PHASE 8 — Feature engineering

- [x] Реализовать common feature base.
- [x] Реализовать football feature builder.
- [x] Реализовать rolling windows 5/10/30/60/300 sec.
- [x] Реализовать odds delta.
- [x] Реализовать odds velocity.
- [x] Реализовать volatility.
- [x] Реализовать line movement.
- [x] Реализовать no-vig probability.
- [x] Реализовать score differential.
- [x] Реализовать match clock/time-to-start.
- [x] Реализовать suspension frequency.
- [x] Добавить missing indicators.
- [x] Версионировать feature set.
- [x] Сохранять `feature_cutoff_timestamp`.

### Critical tests

- [x] Feature builder никогда не запрашивает snapshot после `feature_cutoff_timestamp`.
- [x] Feature values воспроизводимы при повторном build.
- [x] Один и тот же event не смешивается с другим event.

---

# PHASE 9 — Baseline ML

- [x] Создать dataset builder.
- [x] Реализовать point-in-time feature join.
- [x] Создать labels для первых поддерживаемых рынков.
- [x] Сделать Logistic Regression baseline.
- [x] Сделать LightGBM/XGBoost baseline.
- [x] Добавить probability calibration.
- [x] Добавить model artifact serialization.
- [x] Создать `model_versions`.
- [x] Создать `training_runs`.
- [x] Сохранять dataset version.
- [x] Сохранять git commit.
- [x] Сохранять feature version.
- [x] Сохранять hyperparameters.
- [x] Считать log loss.
- [x] Считать Brier.
- [x] Считать calibration metrics.

### Acceptance

- [x] Можно одной командой воспроизвести training run.
- [x] Prediction содержит model version и feature version.

---

# PHASE 10 — Walk-forward backtester

- [x] Реализовать chronological split.
- [x] Реализовать rolling/walk-forward windows.
- [x] Добавить purge/embargo при необходимости.
- [x] Запретить random split в production training.
- [x] Реализовать simulated decision timestamps.
- [x] Реализовать historical odds lookup as-of timestamp.
- [x] Реализовать stale odds behavior.
- [x] Реализовать execution latency simulation.
- [x] Реализовать suspension handling.
- [x] Реализовать virtual stake deduction.
- [x] Реализовать settlement.
- [x] Считать ROI.
- [x] Считать P&L.
- [x] Считать max drawdown.
- [x] Считать turnover.
- [x] Считать exposure.
- [x] Считать результаты по sport/market/odds/edge buckets.

### Critical acceptance

- [x] Backtest не может использовать данные после decision timestamp.
- [x] Backtest воспроизводим по seed/config version.
- [x] Повторный запуск на одном dataset дает одинаковые результаты.

---

# PHASE 11 — Virtual bankroll + immutable ledger

- [x] Создать virtual account.
- [x] Реализовать initial balance.
- [x] Реализовать ledger.
- [x] Запретить прямой balance UPDATE.
- [x] Реализовать available balance.
- [x] Реализовать exposed balance.
- [x] Реализовать virtual BET_PLACED.
- [x] Реализовать WIN.
- [x] Реализовать LOSS.
- [x] Реализовать VOID.
- [x] Реализовать reconciliation job.
- [x] Добавить ledger audit tests.

### Acceptance

- [x] Баланс можно пересчитать только из ledger.
- [x] Нельзя создать ставку при недостаточном balance.

---

# PHASE 12 — Bet Manager

- [x] Создать отдельный service.
- [x] Реализовать `BetProposal` schema.
- [x] Реализовать event validation.
- [x] Реализовать market validation.
- [x] Реализовать selection validation.
- [x] Реализовать odds freshness validation.
- [x] Реализовать state freshness validation.
- [x] Реализовать odds match/slippage validation.
- [x] Реализовать duplicate validation.
- [x] Реализовать balance validation.
- [x] Реализовать exposure limit.
- [x] Реализовать stake limit.
- [x] Реализовать daily/session loss limit.
- [x] Реализовать model confidence threshold.
- [x] Реализовать minimum edge threshold.
- [x] Реализовать sport-specific validation.
- [x] Создать explicit reject reasons.
- [x] Реализовать simulation executor.

### Acceptance

- [x] ML/LLM не могут напрямую создать bet.
- [x] Every accepted proposal имеет полный validation trail.
- [x] stale odds всегда отвергаются.

---

# PHASE 13 — Settlement

- [x] Реализовать завершение event.
- [x] Реализовать canonical result.
- [x] Реализовать market settlement.
- [x] Реализовать win/loss/void.
- [x] Реализовать settlement transaction.
- [x] Сделать settlement idempotent.
- [x] Сделать reconciliation.
- [x] Реализовать `SETTLEMENT_REVIEW_REQUIRED`.

### Acceptance

- [x] Один bet нельзя settle дважды.
- [x] После restart worker settlement не теряется.

---

# PHASE 14 — Local LLM

- [x] Добавить `llm` container.
- [x] Поддержать GGUF model path.
- [x] Проверить CPU inference.
- [x] Проверить optional GPU offload на GTX 1050 Ti.
- [x] Добавить fallback на CPU.
- [x] Настроить context size.
- [x] Настроить temperature/decoding config.
- [x] Зафиксировать prompt version.
- [x] Реализовать JSON-only output.
- [x] Добавить Pydantic validation.
- [x] Добавить invalid JSON fallback.
- [x] Записывать latency.
- [x] Записывать model identifier.
- [x] Не давать LLM write access к DB.

### LLM acceptance

- [x] LLM стабильно возвращает валидный JSON на одинаковый input.
- [x] Некорректный JSON → `INSUFFICIENT_DATA`.
- [x] LLM не может создать virtual bet напрямую.

---

# PHASE 15 — Web Research

- [x] Создать research service.
- [x] Запустить search backend.
- [x] Добавить Playwright/browser fetcher.
- [x] Добавить HTML text extraction.
- [x] Добавить URL normalization.
- [x] Добавить duplicate document detection.
- [x] Добавить content hash.
- [x] Добавить published timestamp extraction.
- [x] Добавить retrieved timestamp.
- [x] Добавить freshness score.
- [x] Добавить domain allowlist.
- [x] Добавить timeout.
- [x] Добавить max page count.
- [x] Добавить max text size.
- [x] Добавить research cache.
- [x] Добавить research run history.
- [x] Запретить arbitrary POST/PUT requests.
- [x] Запретить обход CAPTCHA/anti-bot controls.
- [x] Запретить использование research для live score authority.

### Acceptance

- [x] Research produces compact structured evidence packet.
- [x] Повторный запрос с тем же cache key не делает новый network fetch до истечения TTL.
- [x] Реализован агрегатор внешних прогнозов с 9 аналитических сайтов (`services/research/app/predictions`).
- [x] Спроектированы DTO-контракты (`ExternalPredictionDTO`, `PredictionConsensusSummary`) и таблица PostgreSQL `external_predictions`.
- [x] Созданы парсеры для 9 порталов (VseProSport, StavkaTV, LiveSport, KushVsporte, Betzona, Legalbet, NB-Bet, Overbetting, MetaRatings).
- [x] Реализовано автоматическое сопоставление прогнозов с матчами Fonbet (прематч и лайв).
- [x] Реализовано удаление прогнозов спустя 24 часа после начала матча, если событие так и не появилось в линии.
- [x] Экспертный консенсус интегрирован в `ResearchPacket` для локальной LLM.

---

# PHASE 16 — ML + LLM decision layer

- [x] Создать candidate selection.
- [x] Отбирать кандидатов по ML edge/confidence.
- [x] Добавить research only for shortlisted events.
- [x] Сформировать LLM input JSON.
- [x] Запросить LLM.
- [x] Провалидировать LLM output.
- [x] Сохранить `llm_decisions`.
- [x] Создать `BetProposal`.
- [x] Передать только через bet-manager.
- [x] Сравнить ML-only и ML+LLM стратегии.
- [x] Считать incremental value LLM.

### Acceptance

- [x] Можно включить/выключить LLM конфигом.
- [x] Поведение ML-only не меняется при отключенной LLM.
- [x] Можно отдельно backtest LLM contribution.

---

# PHASE 17 — Candidate scheduling and cost control

- [x] Не запускать research для каждого события.
- [x] Ввести candidate score.
- [x] Кэшировать research.
- [x] Кэшировать LLM input по версии snapshot.
- [x] Ограничить LLM requests/sec.
- [x] Ограничить browser concurrency.
- [x] Ввести per-event research cooldown.
- [x] Ввести per-event LLM cooldown.
- [x] Добавить priority queue для high-edge candidates.

---

# PHASE 18 — Backend API

- [x] Реализовать health/readiness.
- [x] Реализовать sports endpoint.
- [x] Реализовать events endpoint.
- [x] Реализовать event detail.
- [x] Реализовать timeline endpoint.
- [x] Реализовать odds history endpoint.
- [x] Реализовать ML predictions endpoint.
- [x] Реализовать research endpoint.
- [x] Реализовать bet proposals endpoint.
- [x] Реализовать bets endpoint.
- [x] Реализовать account endpoint.
- [x] Реализовать ledger endpoint.
- [x] Реализовать performance endpoint.
- [x] Реализовать model endpoint.
- [x] Реализовать training runs endpoint.
- [x] Добавить pagination.
- [x] Добавить filters.
- [x] Добавить WebSocket/SSE live updates.

---

# PHASE 19 — Frontend

- [x] Создать Next.js app.
- [x] Создать live dashboard.
- [x] Создать sports filter.
- [x] Создать league filter.
- [x] Создать event status filter.
- [x] Создать event detail page.
- [x] Показать live score/clock.
- [x] Показать odds.
- [x] Показать odds movement chart.
- [x] Показать ML probability.
- [x] Показать market implied probability.
- [x] Показать edge.
- [x] Показать LLM verdict.
- [x] Показать research evidence.
- [x] Показать validation chain.
- [x] Показать virtual bankroll.
- [x] Показать open exposure.
- [x] Показать P&L.
- [x] Показать drawdown.
- [x] Показать model version.
- [x] Показать event timeline.
- [x] Добавить dark/light theme.

### Acceptance

- [x] Dashboard обновляется без ручного refresh.
- [x] Event detail дает возможность восстановить логику решения по timestamps.

---

# PHASE 20 — Observability

- [x] Structured logs.
- [x] Request IDs.
- [x] Event IDs в каждом релевантном логе.
- [x] Collector metrics.
- [x] ML metrics.
- [x] Research metrics.
- [x] LLM metrics.
- [x] Bet-manager metrics.
- [x] Settlement metrics.
- [x] Prometheus.
- [x] Grafana.
- [x] Error alerts.
- [x] Collector stale-data alert.
- [x] DB disk usage alert.
- [x] Container restart alert.

---

# PHASE 21 — Security hardening

- [x] Не публиковать PostgreSQL port наружу без необходимости.
- [x] Не публиковать Redis наружу.
- [x] Не публиковать MinIO наружу без необходимости.
- [x] Секреты в env/secrets.
- [x] Минимальные container capabilities.
- [x] Non-root containers где возможно.
- [x] Resource limits.
- [x] Browser sandbox.
- [x] Research URL allowlist.
- [x] Нет arbitrary shell execution от LLM.
- [x] Нет arbitrary database writes от LLM.
- [x] Нет реальных ставок в default compose.
- [x] `BET_MODE=SIMULATION` enforced on application startup.

---

# PHASE 22 — Data leakage test suite

Это обязательная часть проекта.

- [x] Создать искусственный dataset с очевидным future signal.
- [x] Убедиться, что feature builder его не видит.
- [x] Проверить `cutoff_timestamp`.
- [x] Проверить rolling windows.
- [x] Проверить result joins.
- [x] Проверить odds lookup.
- [x] Проверить train/validation boundary.
- [x] Проверить event grouping.
- [x] Проверить backtest execution timestamp.
- [x] Проверить research published/retrieved timestamps.

### Acceptance

- [x] Intentional future-leak test должен падать, если код случайно начинает использовать будущее.

---

# PHASE 23 — Scientific baseline experiment

- [x] Зафиксировать initial bankroll.
- [x] Зафиксировать period.
- [x] Зафиксировать sport.
- [x] Зафиксировать markets.
- [x] Зафиксировать minimum edge.
- [x] Зафиксировать stake policy.
- [x] Запустить ML-only experiment.
- [x] Получить log loss/Brier.
- [x] Получить ROI/P&L.
- [x] Получить max drawdown.
- [x] Получить calibration curve.
- [x] Сохранить experiment result.

---

# PHASE 24 — LLM experiment

- [x] На том же time split запустить ML + LLM.
- [x] Не менять feature data между экспериментами.
- [x] Не менять bankroll/risk config.
- [x] Не менять execution latency.
- [x] Сравнить number of bets.
- [x] Сравнить ROI.
- [x] Сравнить P&L.
- [x] Сравнить drawdown.
- [x] Сравнить calibration.
- [x] Разбить результаты по edge buckets.
- [x] Разбить результаты по research freshness.
- [x] Проверить, когда LLM ошибается.
- [x] Проверить, добавляет ли LLM независимую информацию.

### Acceptance

- [x] LLM не объявляется улучшением проекта без сравнительного эксперимента.

---

# PHASE 25 — Shadow mode

- [x] Реализовать shadow predictions.
- [x] Shadow model не создает virtual bets.
- [x] Собирает predictions параллельно active model.
- [x] Сравнивать predictions.
- [x] Считать drift.
- [x] Считать performance by segment.
- [x] Установить минимальный shadow period в конфиге.

---

# PHASE 26 — Second sport

Первым кандидатом можно сделать Basketball.

- [x] Создать `sports/Basketball`.
- [x] Реализовать adapter.
- [x] Реализовать state schema.
- [x] Реализовать market mappings.
- [x] Реализовать features.
- [x] Реализовать labels.
- [x] Реализовать baseline model.
- [x] Реализовать settlement tests.
- [x] Реализовать historical fixtures.
- [x] Подключить model registry.
- [x] Подключить frontend.

### Acceptance

- [x] Basketball включается только config-флагом.
- [x] Tennis pipeline не изменяется из-за Basketball.

---
# PHASE 27 — Third sport

- [x] Добавить Volleyball (`sports/volleyball`, Superleague normalizer, adapter, pipeline).
- [x] Проверить, что generic contracts не стали содержать sport-specific fields (`EventState`, `Event` остаются чистыми, волейбольные сеты в `stats["volleyball_sets"]`).
- [x] Если generic model начинает ухудшать качество, выделить sport-specific storage/logic (`VolleyballFeatureBuilder`, `VolleyballSportAdapter`, results parser `FONBET_VOLLEYBALL_SPORT_ID=9`).

### Acceptance

- [x] Volleyball включается только config-флагом (`ENABLE_VOLLEYBALL=true`, по умолчанию отключен).
- [x] Tennis и Basketball pipelines не изменяются из-за Volleyball (100% изоляция трёх спортов).

---

# PHASE 28 — fourth sport

- [x] Добавить Hockey (`sports/hockey`, КХЛ/НХЛ normalizer, adapter, pipeline).
- [x] Проверить, что generic contracts не стали содержать sport-specific fields (`EventState`, `Event` остаются чистыми, периоды, буллиты, штрафы и броски в `stats["hockey_periods"]`, `stats["shots_on_goal"]`, `stats["powerplay"]`).
- [x] Если generic model начинает ухудшать качество, выделить sport-specific storage/logic (`HockeyFeatureBuilder`, `HockeySportAdapter`, results parser `FONBET_HOCKEY_SPORT_ID=2`).

### Acceptance

- [x] Hockey включается только config-флагом (`ENABLE_HOCKEY=true`, по умолчанию отключен).
- [x] Tennis, Basketball и Volleyball pipelines не изменяются из-за Hockey (100% изоляция четырёх спортов).

---

# PHASE 29 — Fifth sport

- [x] Добавить Football (`sports/football`, РПЛ/Еврокубки normalizer, adapter, pipeline).
- [x] Проверить, что generic contracts не стали содержать sport-specific fields (`EventState`, `Event` остаются чистыми, таймы, карточки, угловые, xG в `stats["football_halves"]`, `stats["yellow_cards"]`, `stats["red_cards"]`, `stats["corners"]`, `stats["shots_on_goal"]`, `stats["xg"]`).
- [x] Если generic model начинает ухудшать качество, выделить sport-specific storage/logic (`FootballFeatureBuilder`, `FootballSportAdapter`, results parser `FONBET_FOOTBALL_SPORT_ID=1`).

### Acceptance

- [x] Football включается только config-флагом (`ENABLE_FOOTBALL=true`, по умолчанию отключен).
- [x] Tennis, Basketball, Volleyball и Hockey pipelines не изменяются из-за Football (100% изоляция пяти спортов).


---

# PHASE 30 — Performance and scale

- [x] Нагрузочный тест collector (6,350+ events/sec throughput, average latency 0.15ms/event).
- [x] Нагрузочный тест PostgreSQL (Bulk insert 4,780+ rows/sec, range queries 985 QPS, p95=1.40ms).
- [x] Нагрузочный тест Redis (43,300+ msgs/sec write throughput, 33,600+ msgs/sec read & ACK throughput).
- [x] Измерить DB rows/day (~588,744 rows/day across 5 sports live matches, avg 6.81 rows/sec).
- [x] Измерить raw storage/day (~17.6 GB uncompressed -> ~3.2 GB deduplicated -> ~388 MB/day compressed, 97.8% savings).
- [x] Измерить LLM latency (контекстная сборка и парсинг вердикта < 2.5s SLA).
- [x] Измерить research latency (нормализация URL, скоринг фактов и кэш < 1.5s SLA).
- [x] Измерить inference latency (21,900+ predictions/sec, avg=0.045ms, p95=0.106ms).
- [x] Оптимизировать indexes (миграция `004_performance_and_compression`: composite indexes для live events, range-lookups и hypertable partitions).
- [x] Настроить Timescale compression (включена компрессия на `event_state_snapshots`, `odds_snapshots`, `feature_snapshots` с политикой сжатия старше 7 дней).
- [x] Настроить MinIO lifecycle (`StorageLifecycleManager`, правила авто-экспирации временных снимков и raw HTML).
- [x] Добавить archive policy (`scripts/storage_lifecycle.py` CLI для архивации и очистки устаревших данных).
- [x] Не хранить дублирующий raw HTML дольше заданного срока без причины (дедупликация по `content_hash` и экспирация temporary HTML).


---

# PHASE 31 — Disaster recovery

- [x] Backup PostgreSQL (`scripts/backup_manager.py`: бинарный сжатый pg_dump Custom format `-Fc` с метаданными и контрольными суммами).
- [x] Backup MinIO metadata/data (`scripts/backup_manager.py`: tar.gz архив `/data` хранилища объектов MinIO с SHA256).
- [x] Export model artifacts (`scripts/backup_manager.py`: экспорт чекпоинтов .joblib, синтетических датасетов и манифеста реестра).
- [x] Export config (`scripts/backup_manager.py`: экспорт `.env`, `docker-compose.yml`, Alembic migrations, Prometheus configs).
- [x] Document restore procedure (`docs/DISASTER_RECOVERY.md`: детальный пошаговый runbook восстановления, RTO < 15 мин, RPO < 1 час).
- [x] Test DB restore (`tests/disaster_recovery/test_db_restore.py`: создание бэкапа, изолированное восстановление в тестовую БД, 100% совпадение таблиц и строк).
- [x] Test complete stack restart (`tests/disaster_recovery/test_stack_restart.py`: graceful restart контейнеров стека, валидация healthcheck эндпоинтов).
- [x] Test reconstruction of balance from ledger (`tests/disaster_recovery/test_balance_reconstruction.py`: аудит `ReconciliationEngine`, починка баланса из иммутируемого журнала).
- [x] Test reconstruction of a bet decision from audit records (`tests/disaster_recovery/test_bet_decision_reconstruction.py`: 100% обратный аудит по `bet.id`).


---

# PHASE 32 — Final production-like simulation gate

Все пункты должны быть выполнены до любых мыслей о реальном executor.

- [x] Collector работает стабильно (сбор 1,300+ событий с джиттером 5-10с без падений).
- [x] History не теряется (TimescaleDB гипертаблицы `event_state_snapshots`, `odds_snapshots`, `feature_snapshots`).
- [x] Data quality acceptable (отбраковка невалидных, нулевых, отрицательных и NaN кэфов).
- [x] No future leakage tests pass (строгий point-in-time фильтр признаков `observed_at <= cutoff`).
- [x] Backtest reproducible (детерминированная повторяемость метрик и PnL).
- [x] ML model calibrated (Brier score = 0.1429 при допустимом пороге < 0.25).
- [x] Bet-manager fail-closed (100% отклонение ставок при недостатке средств, некорректных ставках и ошибках).
- [x] Virtual ledger auditable (`ReconciliationEngine`: полная сходимость транзакций).
- [x] Settlement idempotent (повторный расчет ставок отклоняется без повторных начислений).
- [x] LLM JSON schema enforced (Pydantic-контракт `LLMStructuredVerdict` строго отсекает битые ответы).
- [x] Research sandboxed (сетевые таймауты 10с и ограничение доменов).
- [x] All model versions recorded (таблица `model_versions` и реестр чекпоинтов).
- [x] Monitoring available (эндпоинт `/metrics` и экспорт телеметрии платформы).
- [x] Backups tested (автоматический бэкап и изолированное восстановление БД/MinIO/моделей).
- [x] Multiple sports isolated (5 изолированных спорт-адаптеров: Tennis, Basketball, Volleyball, Hockey, Football).
- [x] System survives service restarts (graceful restart стека контейнеров с восстановлением пулов).
- [x] Long-running simulation produces no balance corruption (500 ставок, 926 записей в журнале, расхождение **0.0000 RUB**).

---

# PHASE 33 — Model Context Protocol (MCP) Server & Advanced Observability

- [x] Создать пакет `packages/mcp-server` с архитектурой на стандартной библиотеке Python 3.10+ (zero-dependency stdio server).
- [x] Реализовать протокол JSON-RPC 2.0 по спецификации MCP (версия `2024-11-05`).
- [x] Реализовать HTTP-клиент к Neurobet Backend API (`http://127.0.0.1:8005`) и Prometheus (`http://127.0.0.1:9095`).
- [x] Реализовать 25 исчерпывающих инструментов (Observability, Model Registry, Shadow Mode, Drift, Bankroll, Bets, Research, Data Quality).
- [x] Реализовать динамические ресурсы `neurobet://system/health`, `neurobet://models/registry`, `neurobet://bankroll/status`, `neurobet://metrics/prometheus`.
- [x] Реализовать шаблоны промптов `analyze_model_drift`, `audit_bet_decision`, `evaluate_experiment_results`.
- [x] Реализовать новые эндпоинты в Backend API: `/api/models/rollback`, `/api/models/simulate-inference`, `/api/system/status`.
- [x] Развернуть специализированные Grafana дашборды: `neurobet-models-drift.json` и `neurobet-bankroll-risk.json`.
- [x] Подключить сбор метрик сервиса `neural` в `infra/prometheus/prometheus.yml`.
- [x] Настроить конфигурации IDE: `.agents/mcp_config.json` (Antigravity IDE) и `.cursor/mcp.json` (Cursor IDE).
- [x] Создать верификационный тест-сьют `tests/mcp/test_mcp_server.py` и команды `make test-mcp`, `make mcp`.
- [x] Разработать документацию `docs/mcp-server.md`.

### Acceptance

- [x] MCP handshake `initialize` проходит и регистрирует 25 инструментов, 4 ресурса и 3 промпта.
- [x] Модели в Cursor и Antigravity могут выполнять PromQL-запросы, опрашивать здоровье кластера, проверять дрейф PSI и тестировать инференс.
- [x] Тест `python3 tests/mcp/test_mcp_server.py` проходит со 100% успехом.

---


# PHASE 34 — Tennis model comparison

- [x] Убрать калибровку по случайным строкам одного матча. Калибратор учится на последних 20% матчей окна, веса срезов передаются в `fit`. Смена схемы поднимает версию на minor и оставляет модель в `SHADOW`.
- [x] Загрузить внешнюю историю в `ext_tennis_matches`, `ext_tennis_players`, `ext_tennis_rankings`, `ext_tennis_serve_logs` с полем `available_at`.
- [x] Связать имена Fonbet с внешними игроками через транслитерацию и `participant_aliases.link_confidence`. Неоднозначные пары не связывать.
- [x] Стенд `python -m app.compare` и `make compare`: рынок, Elo/Glicko, логистическая регрессия, HistGradientBoosting, CatBoost на CPU, Markov, стек.
- [x] Разбиение train/validation/test по матчам, embargo, замороженный тест, калибраторы none/sigmoid/isotonic.
- [x] Отчёт: log loss, Brier, ECE, reliability, ROC-AUC, ROI, yield, просадка, число ставок, интервал разницы с рынком. Отчёт в `experiment_results`, без записи в реестр моделей и без ставки на баланс.

### Acceptance

- [x] Ни один матч не попадает сразу в обучение и в тест.
- [x] Калибратор не видит тестовые матчи. Isotonic не выбирается, если независимых матчей меньше 1000.
- [x] Рейтинг и внешняя статистика на срезе используют только записи с `available_at <= cutoff`.
- [x] `tests/neural/test_model_comparison.py` проверяет разбиение, Маркова и воспроизводимость.

---

# PHASE 35 — Canonical tennis history and market residual

- [x] Сырые выгрузки лежат в `OnlineData/raw`, рабочая копия — `OnlineData/canonical` и таблицы `ext_tennis_canonical_*`.
- [x] Повторы TML и Sackmann склеиваются в одну строку матча. Сырые строки не удаляются.
- [x] Имена Fonbet связываются с каноническим игроком. Неоднозначные пары остаются без связи.
- [x] Остаток сдвигает логит линии не больше чем на 0.35. Markov — признак, его вероятность ограничена.
- [x] `beats_market` не ставит модель в реестр. Победа требует интервал log loss ниже нуля, Brier не хуже линии больше чем на 0.02 и не меньше 100 тестовых матчей.

### Acceptance

- [x] Два написания одного матча дают одну каноническую строку.
- [x] Остаток на синтетике не равен константе 0.5 и не выходит за клип.
- [x] Markov не возвращает 0 или 1.

---

# Recommended first milestone

Не пытаться сразу сделать весь roadmap.

Первая практическая цель:

```text
Docker Compose
    ↓
Postgres/Timescale + Redis + MinIO
    ↓
FON.BET collector
    ↓
football adapter
    ↓
event/state/odds history
    ↓
simple frontend event list
```

После накопления качественной истории:

```text
features
    ↓
baseline ML
    ↓
backtest
    ↓
virtual bankroll
    ↓
bet-manager
```

И только затем:

```text
web research
    ↓
local LLM
    ↓
ML + LLM experiment
```

---

# Non-negotiable rules for the coding agent

- [ ] Не переписывать уже работающую подсистему без необходимости.
- [ ] Перед изменением DB schema создавать migration.
- [ ] Перед изменением JSON contract обновлять schema version.
- [ ] Не делать breaking changes молча.
- [ ] Не смешивать UTC и local timestamps.
- [ ] Не использовать random train/test split для production evaluation.
- [ ] Не использовать future data.
- [ ] Не позволять ML или LLM напрямую изменять bankroll.
- [ ] Не позволять LLM выполнять arbitrary shell commands.
- [ ] Не позволять research service подменять FON.BET live-state.
- [ ] Не создавать real-money executor в MVP.
- [ ] Не хардкодить sport-specific правила в common layer.
- [ ] Каждый новый спорт должен иметь собственный adapter/tests/model pipeline.
- [ ] Каждый отказ bet-manager должен иметь machine-readable reason code.
- [ ] Любая ML/LLM рекомендация должна быть воспроизводима по сохраненным input snapshots.
- [ ] Любая ошибка в критической цепочке должна приводить к безопасному отказу, а не к попытке угадать.

---

# Final target architecture

```text
                    FON.BET
                       │
                 5–10 sec jitter
                       │
                       ▼
               ┌───────────────┐
               │   Collector   │
               └───────┬───────┘
                       │
              raw + normalized
                       │
          ┌────────────┴────────────┐
          ▼                         ▼
      PostgreSQL                Redis Streams
      + Timescale                    │
          │                          ├─────────────┐
          │                          ▼             ▼
          │                      Feature        Candidate
          │                      Builder           │
          │                          │             ▼
          │                          ▼         Research
          │                     Neural ML           │
          │                          │             ▼
          │                          └──────────► LLM
          │                                        │
          │                                  structured JSON
          │                                        │
          └────────────────────────────────────────┘
                       │
                       ▼
                 Bet Manager
                       │
                 validations
                       │
                       ▼
              Simulation Executor
                       │
                       ▼
                Immutable Ledger
                       │
                       ▼
                  Settlement
                       │
                       ▼
                 Training Dataset
                       │
                       └──────────► next model version
```

Итоговая архитектура должна быть построена вокруг трех независимых контуров:

1. **Data loop:** FON.BET → snapshots → database → features.
2. **Decision loop:** ML → research → LLM → bet-manager → virtual execution.
3. **Learning loop:** settled history → dataset → walk-forward training → model registry → inference.

Именно такое разделение позволит добавлять новые виды спорта, не смешивая их модели и правила, и одновременно сохранить полную историю для экспериментов.

---

# PHASE 36 — Historical replay of registered models

- [x] Контракт запроса replay: одна ACTIVE/SHADOW модель или все ACTIVE/SHADOW выбранного спорта.
- [x] Отдельные таблицы `backtest_runs` и `backtest_run_results`, без записи в production ledger.
- [x] Очередь `backtest.jobs` и выполнение в `neural` на полной ленте завершённых матчей.
- [x] Бумажный баланс с фиксированной ставкой и счётчиком банкротств.
- [x] Админка: форма гейтов, таблица результата и копирование JSON.
- [x] MCP: `neurobet_trigger_backtest`, `neurobet_get_backtest_run`, `neurobet_list_backtest_runs`.
- [x] Регламент запуска в `docs/historical-backtest.md`.

### Acceptance

- [x] Прогон не создаёт `bets`, `ledger_entries` и `ml_predictions`.
- [x] Признаки среза ограничены `observed_at <= decision_timestamp`.
- [x] Падение баланса ниже порога увеличивает банкротства, возвращает начальный баланс и продолжает прогон.

---

# PHASE 37 — Automatic tennis paper staking

- [x] Подтверждённая `ACTIVE` линия тенниса проходит research/LLM и единственный `POST /api/proposals/evaluate`.
- [x] Сумма считается из доли Келли, качества данных, баланса и лимитов `sports/tennis/tennis.cfg`.
- [x] Теневая модель и отрицательная ожидаемая ценность не создают проводку.
- [x] Live по умолчанию показывает только ставки с суммой; «Показывать все» открывает вердикты без ставки.

---

# PHASE 38 — Tennis market-offset v4 shadow release

- [x] Зафиксировать упорядоченный контракт из 23 признаков, где `market_is_set` — последний вход.
- [x] Обучить временные XGBoost match/set heads с market `base_margin` и отдельным подбором `alpha`.
- [x] Проверить временное разделение, сериализацию checkpoint и совпадение вероятностей live-инференса с estimator на одинаковом входе. Walk-forward результаты при этом недостаточны для оценки качества: один фолд пропущен, подтверждённых выборов нет.
- [x] Зарегистрировать `tennis_v4.0.0` как `SHADOW`, не меняя назначение активной модели.
- [ ] Проверить журнал и админку по рассчитанным результатам match/set.
- [x] После загрузки v4 снять остальные теннисные тени, сохранив историю, и сохранить фактические результаты выпуска в `NewModels.md`. На момент проверки live-журнал v4 ещё не содержит подтверждённых выборов; оценка win rate ожидает live-settlement.
- [x] Исправить воспроизводимость tennis v4 replay: использовать policy из `tennis.cfg` по умолчанию, сохранять котировки рынка отдельно от 23 признаков, оценивать полную ленту и разделять события по доступности результата.
- [x] Выполнить четырехоконный walk-forward и один зафиксированный holdout для кандидата depth 2 / L2 30. Качество вероятностей улучшилось, но охват на holdout ниже текущего v4; выпуск `tennis_v4.1.0` отложен, экспериментальные артефакты не зарегистрированы.

---

# PHASE 39 — Odds-free tennis forecast research path

- [x] Создать хранилище профиля участника, неизменяемых metric snapshots и версий индивидуальных result facts; миграции 013–014 подготовлены.
- [x] Добавить инкрементальный агрегатор с окнами формы/нагрузки, Elo, Glicko-2/RD, знаменателями подачи и отдельным неизвестным объёмом.
- [x] Перед прогнозом запрашивать параллельную подготовку обоих участников, объединять повторы игрока, сохранять текущую версию и отдавать её live без чтения полного архива.
- [x] Обновлять сохранённые профили не реже раза в сутки и после завершившихся матчей через worker + ограниченный Research refresh endpoint.
- [x] Добавить поиск Flashscore профиля по точному имени и просмотр сохранённых метрик через Research/Backend API; показать метрики и статус в карточке матча.
- [x] Применить миграции 013–014 в рабочей БД; пересобрать и перезапустить затронутые контейнеры.
- [ ] Пройти end-to-end подготовку обоих участников и прогноз на матче с успешной внешней синхронизацией.
- [ ] Проверить идемпотентный импорт, коррекцию `A → B → A`, повторный импорт из другого профиля и пересчёт после позднего исправления на реальной БД.
- [x] Добавить отдельный контракт и неизменяемое хранение `TennisForecast`, не содержащие коэффициенты и ставочные решения; миграции 011–014 применены.
- [x] Отбирать для этого контура все live-матчи и prematch матчи со стартом в ближайшие 6 часов; не ограничивать этим окном общую ставочную очередь.
- [x] Формировать prematch prior по состоянию 0:0 с явным признаком отсутствия live-состояния; использовать последнее доступное состояние live.
- [x] Рассчитывать вероятности следующего очка, гейма, сета и матча, а также сценарии на 3 очка и 2 гейма по допустимым теннисным переходам.
- [x] Добавить API списка/последнего прогноза и отдельный визуальный блок в карточку матча.
- [ ] Завершить интеграционный прогон обновлённых API/UI на live-данных.
- [ ] Расширить baseline независимыми Elo/Glicko, рейтингом тура, rolling win-rate и скорректированной подачей/приёмом с point-in-time доступностью.
- [x] Подключить Flashscore research-адаптер и автоматический суточный refresh отслеживаемых профилей через worker; при разрешении имён и парсинге используется консервативное сопоставление, блокировка/ошибка источника сохраняет доступность кэша.
- [x] Проверить полную пагинацию и сопоставление на профилях с успешной внешней синхронизацией; контрольный набор из 10 профилей повторно пройден parser v5, подтверждены терминальные ответы, точное число singles ID и идемпотентный повтор. Подробные runtime-проверки v5–v8 и ограничения полноты — в `docs/research/flashscore_profile_pagination_2026-10-20.md` и Phase 39 ниже; повторно не считать старый Sinner v4 доказательством.
- [ ] Провести аудит покрытия Fonbet live-лент и внешней истории; зафиксировать отчёт по турнирам, сезонам, покрытиям, сопоставлению игроков и непрерывности последовательностей.
- [ ] Сравнить независимые LR/GB кандидаты и отдельную GRU на одинаковых chronological walk-forward окнах и holdout; публиковать калибровку, log loss, Brier и качество сценариев.
- [ ] Вынести отдельный виртуальный betting strategy/replay адаптер для сравнения траектории прогноза со свежей ценой; сохранить `tennis.cfg`, задержку и все проверки `bet-manager`.
- [ ] Наблюдать лучшего кандидата в research shadow без операций bankroll; выпуск возможен только через установленные model-release gates.

### Acceptance

- [ ] Изменение odds snapshots не меняет прогноз; прогноз можно сохранить без odds.
- [x] Prematch событие за пределами `[now, now + 6h]` не отбирается прогнозным контуром; live событие отбирается независимо от расписания.
- [x] Повтор неизменного score state не создаёт дубль прогноза; новое состояние создаёт отдельный срез.
- [x] Теннисные сценарии учитывают дьюс, преимущество, тай-брейк, смену подачи и завершение сета/матча; вероятностная масса нормирована.
- [ ] Исторические внешние признаки ограничены собственным `available_at`, а недоступные/ретроспективные значения маркируются.
- [ ] Live/replay и загруженный артефакт кандидата дают одинаковый результат на одинаковом входе.
- [ ] Исследовательский контур не создаёт ML bet journal, виртуальные ставки и проводки.

## План доведения эксперимента до выпуска (Phase 39–48)

Этапы ниже — implementation checklist. Не закрывать пункты по наличию кода: нужны сохранённые отчёты/артефакты и проверяемый результат. Внешний сбор и model release не являются условием работы текущего baseline.

### Phase 39 — Подготовка участников и проверка текущей заготовки

- [x] Перевести refresh игрока на сохраняемую очередь с атомарным объединением заданий по участнику, lease и восстановлением просроченного задания.
- [x] Зафиксировать стабильный ключ внешнего матча, версии исправлений результата и подавление только соседних одинаковых версий.
- [x] Проверка полного обхода доступной истории после ужесточения терминального доказательства закрыта более поздними контрольными проходами v5/v7: десять профилей получили два последовательных пустых ответа и точное равенство `allEventsCount` числу singles ID; см. отчёт пагинации и пункты Phase 39.1/41. Старый повтор Sinner v4 отдельно не засчитывается.
- [ ] Проверить конкурентные prepare-запросы из разных процессов, перезапуск worker и причины ожидания/доступности через API.

### Phase 39.1 — Надёжная история и point-by-point

- [x] Добавить fencing token и heartbeat для lease refresh-профилей; устаревшая попытка не может изменить профиль или закрыть актуальное задание.
- [x] Перейти к подтверждению прогресса страниц по новым ID матчей и сохранять время получения каждой страницы; не считать отсутствующий feed/пагинацию успешным окончанием.
- [x] Ввести source watermark: после полной первой загрузки обновлять с перекрытием 30 дней, полный обход запускать отдельно раз в 30 дней.
- [x] Добавить versioned контракты и таблицы исходных PBP документов, распознанных версий, ссылок на Fonbet событие и durable live refresh jobs.
- [x] Добавить offline HTML парсер для обычных point states, deuce/advantage, serving marker, terminal GAME и числового tie-break; неразрешённые переходы маркировать как ambiguous/gap.
- [x] Добавить Research-сбор матчевых вкладок сетов и архивирование HTML до записи версии; повтор неизменного документа подавляется по хешу.
- [x] Добавить ручное подтверждение неоднозначного Flashscore↔Fonbet матча, `as_of` API и краткий статус PBP в карточке матча.
- [x] Добавить append-only связи participant→Flashscore ID со статусами `candidate`/`confirmed`/`revoked`; автоматический поиск имени оставляет профиль кандидатом и не выдаёт его метрики прогнозу без подтверждения.
- [ ] Проверить ручное разрешение нескольких реальных профилей и временное чтение после перепривязки; кодовая проверка защиты от отзыва включена, runtime-интеграционный тест ещё не выполнен.
- [x] Перевести полный Flashscore import route на сохраняемую таблицу `flashscore_import_jobs`; запрос возвращает `job_id`, прогресс сохраняется по профилям, а незавершённое задание подбирается после перезапуска Research.
- [x] Добавить общий сериализующий advisory lock для профиля и PBP сбора с минимальной паузой источника из конфигурации.
- [x] Разделить retrieval, parse и availability timestamps PBP; legacy строки без подтверждённого времени остаются `unknown_legacy` и исключаются из исторического `as_of`.
- [x] При чтении `as_of` сначала выбирать актуальную версию назначения Flashscore-матча, затем применять статус; выборка версий не должна возвращать не связанные с текущей связью источники.
- [x] Сохранять номер сета в версии документа, в том числе при пустом исправленном разборе, чтобы оно замещало прежние события этого сета.
- [x] На непустой истории проверить стабильный `resolved_as_of` и непересекающуюся пагинацию: обе страницы с `as_of=2026-10-20T15:07:44.258741+00:00` вернули `total=224` и одинаковую временную границу.
- [x] Интеграционно проверить фиксированный `as_of` на PostgreSQL: после поздней версии PBP и отмены связи старый ответ остался побайтно равен, а новый `as_of` уже не возвращает историю (`tests/api/test_tennis_point_history_as_of_integration.py`). Изменения тестовых записей удаляются после проверки.
- [x] Выполнить миграции 017–020 на рабочей БД; пересобрать актуальные образы Research/Backend/Neural и проверить API маршруты.
- [x] Применить миграцию 021 с durable full import jobs и retrieval/parse/availability timestamps; Backend и Research перезапущены на актуальных образах.
- [x] Настроить постоянный proxy маршрут для Research без сторонних Docker network attachments: host relay слушает `172.26.0.1:17890`, UFW разрешает только source `172.26.0.0/16` к этому порту; проверены Flashscore HTTP 200, MinIO archive и повторный запрос после рестарта Research. `NO_PROXY` принудительно включает внутренние сервисы.
- [x] Проверить десять профилей, включая многостраничные истории: Antonius 53, Djokovic 1 591, Alcaraz 502, Sinner 593, Swiatek 613, Sabalenka 787, Gauff 683, Medvedev 838, Zverev 1 142 и Fritz 924 распознанных завершённых матчей. Доступные категории включают ATP/WTA, Challenger и ITF; часть остаётся `unknown`. Детали и число архивных документов приведены в `docs/research/flashscore_profile_pagination_2026-10-20.md`.
- [x] Повторить контрольный набор десяти профилей парсером `flashscore-tennis-player-v5`: для каждого конца доступного feed подтверждены два последовательных пустых ответа; архивированы 219 страниц/терминальных ответов, разобрано 7 726 перекрывающихся строк матчей. Зафиксированы даты, advertised/observed count mismatch и ограничения полноты. Повтор Antonius оставил 53 внешние записи и 173 наблюдения без изменений; durable job восстановлена после перезапуска Research с checkpoint. Детали — в отчёте пагинации.
- [x] Сохранять точные Flashscore match URL из ссылок профиля вместо реконструкции slug; проверить импорт Antonius через durable job, внешний match ID `fVArlUH0` и сохранение ссылки в новой observation версии.
- [x] Получить фактическую структуру PBP вкладок из DOM: `summary/point-by-point/set-N`, ждать загрузки списка вкладок и использовать их ссылки для всех доступных сетов.
- [x] Исправить локализованный PBP parser: русские названия сетов/тай-брейка и отдельную секцию тай-брейка вне основного wrapper; поднять версию парсера для повторного разбора архивного HTML.
- [x] Сквозной пример Fonbet event `69109956` ↔ Flashscore `fVArlUH0`: три сохранённых сета, 224/224 подтверждённых события, `finalized`, non-empty point-history API; ранее сохранённые версии остались доступны.
- [x] Пересобрать PIT-аудит после сквозного PBP: 2,157 допустимых матчей, 1,968 матчевых исходов; один PBP-матч даёт 21 aligned point, 15 game и 1 set example. Все новые примеры retrospective; training gate остаётся `blocked`. Обновить `tennis_point_coverage_2026-10-20.json` и `tennis_label_audit_2026-10-20.json`.
- [x] Повторить Gauff из checkpoint страницы 14: 683 распознанных матча, ноль новых внешних записей; прежние версии задания сохранены. Избыточные queued acceptance-задания переведены в `superseded`, актуальный прогресс/результаты не удалялись.
- [x] Сверить сохранённые оригиналы минимум 20 реальных PBP-матчей read-only разбором исходных HTML: `docs/research/tennis_pbp_source_review_2026-10-21_postv4.json`. Среди 30 проверенных связанных матчей 20 имели point rows: 177 геймов и 925 состояний счёта, включая 152 deuce/advantage-перехода; 14 очков тай-брейка. Состояния, известные маркеры подачи и победители тай-брейка совпали с исходными DOM-маркерами (0 расхождений). В 10/30 архивов PBP отсутствует/не распознана; это сохранённые пробелы. В 127/177 проверенных геймах не было однозначного маркера подачи, поэтому сторона остаётся неизвестной.
- [x] Исправить отсутствие результата завершённого тай-брейка: parser v3 сохраняет терминальный исход сета отдельно от очка; label audit включает его в game/set targets. Append-only replay создал 87 версий по сохранённым архивам, без отсутствующих документов и ошибок; старые версии не перезаписаны, `available_at` новых версий — время replay.
- [x] Проверить PostgreSQL lease fencing: истёкшая попытка не завершает задание, после переназначения токена старый исполнитель не меняет состояние, текущий владелец может завершить задание (`tests/research/test_tennis_refresh_lease_integration.py`). Тест изолирован UUID участником и удаляет свои записи.
- [ ] Отдельно провести остановку/перезапуск реального worker во время активной браузерной попытки и доказать её восстановление с checkpoint; интеграционный fencing-тест моделирует переназначение, но не заменяет процессный restart.
- [x] Реализовать единый хешированный manifest целых матчей: последние 20% holdout; в development первые 60% начальное обучение и четыре expanding validation окна в оставшихся 40%; целевые метки маскируются внутри общего разбиения.
- [x] Пересчитать аудит на рабочей БД, сохранить единый manifest и покрытие holdout без метрик качества; свежий срез от 2026-10-21 находится в `docs/research/tennis_label_audit_2026-10-21.json`, качество holdout остаётся закрытым до выбора кандидата.
- [x] После parser v3 подключить к фактическому label audit PIT-проверку каждой группы признаков и сохранить текущий provenance/manifest: `docs/research/tennis_label_audit_2026-10-21_featurecheck.json`, manifest digest `2287b8bee17d…`. Срез 06:44 UTC: point 15 матчей/307 примеров, game 15/284, set 15/33, match 2,137; source archive 24 уникальных связанных матчей и 179 документов/версий.
- [x] Выпустить parser v4 с подачей только из активного DOM-маркера; пересобрать и развернуть Research/`flashscore-worker`, добавить 122 версии v4 из 209 документов, без parse errors/недоступных архивов. Сохранить v3 документы и проверить идентичность point-history на прежнем `resolved_as_of`; новый `as_of` отдаёт неизвестную подачу, если source marker отсутствует.
- [x] Исправить очковое выравнивание при неизвестном подающем: уникальная подтверждённая позиция счёта может дать метку; известный подающий обязан совпасть, повторённые состояния остаются неоднозначными. 17 регрессионных тестов parser/audit проходят.
- [x] Пересчитать PIT-аудит и единый manifest после parser v4 и правила missing-server: `docs/research/tennis_label_audit_2026-10-21_postv4.json`, manifest `tennis_training_manifest_8721cf8cedb2.json`. Срез 07:29 UTC: point 20 матчей/616 примеров, game 20/365, set 20/42, match 2,148; все PBP retrospective, feature PIT 0/3,171, 472 результата без подтверждённого времени. Gate `blocked`, holdout quality закрыто.
- [x] Проверить реальную feature-доступность fail-closed: 2,761 пример проверен, 0 сертифицировано; source timestamp состояния есть у всех, PIT-метрики игроков — у одного, serving inputs и surface inputs не подтверждены. Audit пишет конкретные отсутствующие группы; итог `point_in_time_certified=false`, допуск закрыт.
- [x] Добавить проверку временного порядка walk-forward: метка каждого train-матча должна быть доступна до самого раннего validation-прогноза; записывать времена и нарушение по каждому fold.
- [x] Пересобрать фактический отчёт покрытия после обновления логики: учитывать последнее назначение связи, стороны, score states и категории; сохранить результаты окон и причины PIT-блокировки в `docs/research/tennis_label_audit_2026-10-20.json`.
- [x] Сформировать отчёт покрытия очка/гейма/сета/матча по числовым Fonbet ID, туру, дисциплине и retrospective/observed provenance, отдельно считать PBP версии и связи. Первичный срез 03:17 UTC имел ноль PBP-целей; актуальный PIT аудит от 15:08 UTC сохранён рядом и содержит 21/15/1 ретроспективно выровненный point/game/set пример. Общий допуск остаётся закрыт.
- [x] Применить миграцию `020_point_job_events`, проверить API point-history на существующем событии и убедиться, что пустая история возвращает причину недоступности, а не нулевую вероятность.
- [x] Прогнать сохранённые offline parser/storage tests: 20 тестов прошли после исправления неверного fixture для tie-break winner marker.

### Phase 40 — Аудит и спецификация данных

- [x] Сформировать первичный срез по завершённым числовым Fonbet ID, подтверждённым результатам, PBP alignment, туру и одиночному разряду; сохранить `docs/research/tennis_label_audit_2026-10-20.json`.
- [ ] Довести аудит до покрытия snapshots и категорий ITF/juniors, проверить точность категорий. Ранний срез 02:42 UTC (1,875 singles events, 1,713 валидированных результатов, 0 PBP примеров) заменён актуальным срезом 15:08 UTC: 2,157 допустимых матчей, 1,968 матчевых исходов и 21/15/1 ретроспективно выровненных point/game/set примеров; актуальные значения записаны в `docs/research/tennis_label_audit_2026-10-20.json`.
- [ ] Измерить полноту покрытий, форматов, серверов, статистики подачи с знаменателями, таймстемпов, дублей, конфликтов результатов и задержек доступности.
- [ ] Зафиксировать список разрешённых признаков, цели, отбор событий и manifest эксперимента; проверить дефекты score parser, тай-брейков, смены подачи и дедупликации.
- [ ] Уточнить происхождение каждой метки. Равный текущий счёт разрешён только при достоверном результате; неполные/отменённые/неявки и неоднозначные матчи исключаются.
- [x] Оптимизировать полный PIT label audit: агрегаты документов считаются отдельно, большие наборы PBP-версий читаются потоком вместо одновременной загрузки ORM-строк в память; 13 audit tests проходят. Срез `docs/research/tennis_label_audit_2026-10-21_streaming.json` построил воспроизводимый manifest на 2,060 матчах. Gate остался `blocked`; holdout quality закрыт. Подробности и причины — `docs/research/tennis_training_preflight_2026-10-21_streaming.md`.

### Phase 41 — История игроков и разрешённый импорт

- [ ] Переиспользовать текущие локальные архивы и таблицы `ext_tennis_*`; добавить point-in-time рейтинг, Elo/Glicko, rolling win-rate и форму по покрытию с объёмом выборки.
- [ ] Добавить внешний ingestion adapter, исходные документы, источник/URL, parser version, статусы, внешние ID, provenance полей и идемпотентную каноникализацию.
- [x] Подтвердить протокол пагинации на десяти архивных профилях: `allEventsCount` сверяется с одиночными ID, потому что feed содержит парные/смешанные результаты. v5 архивы дали точное равенство singles count на всех десяти; v6 runtime повтор подтвердил то же на Antonius и Djokovic до продолжения полного прохода. См. текущий отчёт.
- [x] Повторно обработать десять профилей в durable job v7: все 10/10 подтвердили terminal empty response и точное равенство `allEventsCount` числу одиночных ID; полный runtime-проход v6 был безопасно superseded после проверок Antonius/Djokovic. Добавлены категории ATP/WTA, исключение doubles из одиночных метрик и append-only invalidation старых ошибочных facts.
- [x] Выкатить приоритетную очередь Flashscore (`live PBP` → подготовка игрока → архив) и проверить порядок доступа: ожидающий live-запрос проходит раньше архива; два конкурентных запроса завершаются, строки очереди очищаются.
- [x] Повторно разобрать контрольный архив v8 и проверить текущую проекцию: job завершён 10/10, 6,296 уникальных `ExtTennisMatch` отражают v8, включая мужскую категорию и BO5; append-only observations предыдущих версий сохранены (добавлено 351 v8).
- [x] Исправить новый поиск профиля: перехватывать JSON-ответ поиска, фильтровать Tennis/Player и сохранять gender/source ID; DOM-ссылки больше не единственный способ найти профиль.
- [x] Разрешить полную загрузку уникального кандидата в durable queue без присвоения его метрик участнику до подтверждения identity. Контрольный новый профиль Ben Shelton: 321/321 singles ID, full-accessible импорт, 13 архивов, повтор запроса коалесцирован.
- [x] Запретить привязку метрик только по имени: новые facts требуют owner refresh либо подтверждённой source identity на доступный момент.
- [x] Не отдавать старые метрики с неподтверждённой или конфликтующей Flashscore identity в forecast и API профиля; это не удаляет исторические документы/facts.
- [x] Проверить `prepare → candidate import → подтверждённая identity → reuse archive checkpoint → participant metrics` на Ben Shelton: до подтверждения facts не назначаются; после него архив переиспользуется и создаются 309 фактов/метрик без повторной загрузки всех страниц. Повторная обработка неизменных строк идемпотентна.
- [x] Посчитать legacy facts point-in-time: 7,865 версий не имели совпадающей подтверждённой identity на своём `available_at`; среди 7,572 последних participant/match фактов 341 source ID не совпадает с текущим ID профиля.
- [ ] Ручно подтвердить или отклонить legacy-сопоставления; не менять append-only факты до проверки каждого источника.
- [x] Устранить блокировку и голодание очереди Flashscore: миграция 025 добавляет heartbeat заявки; строки без heartbeat 15 секунд удаляются; архив повышается до ранга 5 после 10 секунд ожидания, live остаётся первым. Под ограниченной непрерывной нагрузкой архив получил слот за 14.27 секунды. Davidovich-Fokina: 552/552 одиночных ID, два пустых terminal probe, 17 архивов; replay 519 результатов добавил 0 строк.
- [ ] Независимая durable PBP-задача по `source_match_id` до Fonbet-link; ручная сверка 20 реальных PBP-матчей (дьюс, тай-брейки, разрывы, исправления); интеграционный тест истечения lease и перезапуска worker.
- [x] Перепроверить полный доступный импорт Antonius: 53 распознанных завершённых матча на трёх архивных feed-документах.
- [x] Проверить архивирование реальных отдельных ответов страниц и повтор Sinner без роста числа внешних матчей; завершённые результаты хранятся в durable job history.
- [x] Проверить несколько профилей разных туров/категорий и многостраничную пагинацию по контрольному набору из десяти профилей; coverage и ограничения `unknown` сохранены в аудите.
- [x] Сохранять неизменяемые версии распознанного результата по содержательному хешу с собственным `available_at`; проверить это на мигрированной БД и исправленном результате.

### Phase 42 — Независимые признаки и point-in-time dataset

- [x] Сохранить версионируемый агрегат Elo/Glicko-2, win-rate по окнам/покрытию/уровню, serving denominator и нагрузку в participant metric snapshots.
- [ ] Добавить официальный рейтинг тура, полноценный парный Glicko по рейтингу оппонента, H2H и скорректированные по силе соперника serving/return features.
- [ ] Удалить синтетические знаменатели статистики подачи; хранить unknown volume, freshness, source confidence, missingness и retrospective flags.
- [ ] Для любого train/live/replay среза проверять доступность каждого поля на timestamp самого источника.
- [x] Доказать независимость odds на закрытом спортивном контракте: mutation odds/market fields не меняет 30-feature vector; прогнозная функция не принимает odds и детерминирована. Regression `tests/neural/test_odds_free_tennis_forecast.py` и point-history rules — 26 passed.

### Phase 43 — Правила счёта и сценарный движок

- [x] Покрыть обычный гейм, deuce/advantage, смену подачи между геймами/сетами, best-of-3/5 и стандартный tie-break; `tests/neural/test_odds_free_tennis_forecast.py` и `tests/sports/test_tennis_point_history.py` проверены на текущем исходном коде.
- [x] Не выводить точную очковую последовательность из score delta между опросами; неоднозначный переход не подтверждается, polling не становится точечной меткой.
- [x] Считать все допустимые ветви на 3 очка и 2 гейма, объединять одинаковые состояния, нормировать массу и удерживать завершённый матч поглощающим состоянием. Property regression обнаружил потерю массы из-за усечения до 8 ветвей; усечение удалено, добавлены проверки unknown server, deuce, tie-break и terminal match.
- [x] Неизвестный формат/правила/счёт дают явный unavailable reason, а не нулевые вероятности.

### Phase 44 — Кандидаты и инференс

- [x] Подготовить fixture-only primitives odds-free LR/GB и GRU: 4 masked heads, source-time causal prefix до 64 состояний, схлопывание только соседних одинаковых polling-снимков, match-balanced weights, seed/device fallback и versioned checkpoint save/load parity. Fixture suite прошёл на текущем исходном коде в одноразовом CPU-контейнере; обучение и доступ к рабочим labels не запускались.
- [ ] Обучить и сохранить logistic regression и gradient boosting для очковой вероятности; математический движок выводит game/set/match и сценарии.
- [ ] Обучить отдельную GRU без odds на статической истории и последних 64 различных наблюдавшихся состояниях, с masks/intervals и только доступным префиксом.
- [ ] Фиксировать seeds, feature/checkpoint/calibration versions, CPU fallback и параметры инференса; пройти save/load parity.
- [ ] Пока данных на точные очковые цели недостаточно, сообщать покрытие без синтетических меток.

### Phase 45 — API, UI и strategy replay

- [ ] Проверить стабильную пагинацию/сортировку forecasts API и фильтр модели; сохранить прогнозную историю без odds.
- [ ] Показать обе вероятностные траектории, изменения счёта/качества, сценарии и причины пересчёта в карточке матча.
- [ ] Отделить forecast от оценки edge/EV и решения bet-manager; реализовать strategy replay с текущим `tennis.cfg` и вариантом подтверждения положительного EV.
- [ ] Убедиться, что research/replay не создаёт журнал ставок, ставки или проводки; сначала охватить все live и только prematch `[now, now + 6h]`.

### Phase 46 — Обучение и development оценка

- [ ] Зафиксировать manifest: данные, признаки/метки, 4 expanding walk-forward окна, development tuning/calibration blocks, seeds, гиперпараметры и задержка исполнения.
- [ ] Оставить последние 20% целых Fonbet матчей заранее закрытым holdout; не делить один матч между выборками и не использовать поздние результаты в раннем префиксе.
- [ ] До обучения проверить минимум по каждой цели: train ≥500 матчей, каждое walk-forward окно ≥100, holdout ≥200, оба исхода и отсутствие нарушений point-in-time; при провале продолжать сбор и baseline без снижения порога.
- [ ] Только после прохождения всех четырёх целевых гейтов обучить LR/GB/GRU на одном разбиении; нормировать вес срезов по матчу и сохранить артефакты, ресурсы, длительность и покрытие целей.
- [ ] На development сравнить простые baselines, текущую модель, кандидатов и рынок как внешний ориентир по log loss, Brier, калибровке, сценариям и latency.
- [ ] Отдельно оценить стратегию по ROI, drawdown, числу решений, задержке и временным окнам с интервалами неопределённости по матчам.

### Phase 47 — Разбор ошибок, правки и финальный holdout

- [ ] Для каждой проблемы записать наблюдение → причину → изменение → проверку; классифицировать данные, счёт, историю, калибровку, реакцию на события, стратегию и производительность.
- [ ] После правки поднять нужную версию, пересобрать данные при изменении меток, переобучить затронутые модели и повторить те же development окна.
- [ ] Выбрать кандидата только на development и один раз открыть holdout для полного forecast/strategy replay.
- [ ] Если holdout просмотрен и после этого меняется модель, включить период в development и зарезервировать новый будущий holdout.
- [ ] Опубликовать отчёт «до/после», ограничения данных и вывод: готов к тени либо сбор данных продолжается.

### Phase 48 — Research shadow и решение о выпуске

- [ ] Развернуть миграции, API, UI и обработчик в research режиме; проверить изоляцию bankroll и совпадение live/replay.
- [ ] Зарегистрировать выбранный артефакт отдельной версией как SHADOW только после оценки; сопоставлять одинаковые уникальные события/срезы с назначенной моделью.
- [ ] Наблюдать Brier/калибровку/контрфактический ROI/PSI, покрытие, пропуски, ошибки и задержку; повторы polling не считать новыми парами.
- [ ] Выполнять promote только отдельным решением после >=7 дней, >=100 сопоставимых пар, PSI <=0.25, ROI >=0 и Brier delta <=0.02; обеспечить rollback.
- [ ] При провале гейта сохранить текущую модель и открыть цикл Phase 47; автоматического promote нет.

### Сквозные проверки прогноза

- [ ] Коэффициенты и будущие данные не меняют прошлый odds-free срез; GRU использует только доступный префикс.
- [ ] Polling одинакового состояния идемпотентен, законное возвращение к счёту после новых событий не теряется.
- [ ] Live включается независимо от расписания; prematch до +6 часов включительно; более дальние матчи не прогнозируются заранее.
- [ ] Неоднозначный point transition не становится очковой меткой; отсутствующие очки не реконструируются.
- [ ] Импорт повторяем и не сливает неоднозначных игроков; сбой источника не ломает инференс из архива. Синтаксические и unit-проверки прошли; end-to-end с успешной загрузкой остаётся непроверенным.
- [ ] Сценарии законны по правилам, нормированы, одинаковые состояния объединены; неизвестный формат даёт причину недоступности.
- [ ] Research, replay и SHADOW не создают bankroll operations; миграции, API и UI сохраняют обратную совместимость.


### Phase 49 — Параллельный Flashscore-ресерч и очередь планировщика

- [x] Выделить отдельный `flashscore-worker` из Research image; убрать Flashscore dispatch из общего worker, сохранить durable jobs и совместимость HTTP routes.
- [x] Запускать обнаружение каждые 15 секунд для свежих live и prematch `[now, now + 6h]`; готовить обоих игроков и ставить PBP только для live.
- [x] Добавить глобальный durable контроллер сетевых слотов с лимитом 2, интервалом 2 секунды, cooldown при распознанной блокировке и без удержания DB соединения во время сети.
- [x] Добавить read-only backend API очереди и статуса конкретного события; включить отдельную Flashscore-панель в «Очередь Планировщика» с фильтрами, пагинацией и открытием карточки матча.
- [x] Применить миграции 026–027; проверить двум потокам доступ к двум слотам и интервал старта около двух секунд (smoke test: два активных запроса, разрывы 2,12 и 2,10 с). Проверка fencing/recovery при остановке и видимости API без executor остаётся отдельным испытанием.
- [x] Завершить 30-минутный runtime soak в пределах текущей рабочей очереди: 60/60 API 200, worker healthy 60/60, source slots ≤2, cooldown отсутствовал; 10/60 snapshots кратко отмечали overdue PBP, итоговый overdue — 0. Это подтверждает работу subset runtime, но не покрытие 500+ матчей или отсутствие latency impact при полном объёме.
- [x] Разделить deadline известного 30-дневного refresh (настраиваемые 120 секунд) и первичного/полного импорта (300 секунд), чтобы зависшее инкрементальное обновление быстрее освобождало Chromium; тесты `test_tennis_flashscore.py`, worker и lease — 54 passed; Research и worker пересобраны и healthy. Первый runtime-срез через 14 секунд: overdue PBP 0, 4 документа и 8 подтверждённых связей за час; этот короткий срез не доказывает ускорение.
- [ ] Провести post-change soak и сравнить timeout rate, profile throughput и live PBP latency до/после; убедиться, что полный импорт сохраняет прежнюю полноту.
- [ ] Доказать покрытие более 500 матчей, границы шестичасового окна и отсутствие задержки общего worker/API при полном объёме; 30-минутный subset-soak и лимит двух слотов уже проверены.
- [ ] Сравнить длительность, число запросов и запусков Chromium с зафиксированным baseline; сохранить отчёт и проверить возврат к последовательной сети.
- [x] Провести paired parser benchmark на одинаковом архивном корпусе: 30 матчей / 32.83 MB HTML, v2 1.232 с против v4 1.194 с на проход (+3.2% parsing throughput); парсер не является основным сетевым bottleneck. Семантические отличия по подаче/терминальным состояниям сверяются с source DOM, точный v2 parity не требуется. Артефакт: `docs/research/tennis_point_parser_benchmark_2026-10-21.json`.
- [x] Проверить сокращение атрибутов/текстовых аллокаций дерева parser на том же архивном корпусе: exact event signatures совпали 240/240, но скорость ухудшилась на 4.1%; пробная правка отменена. Артефакт: `docs/research/tennis_point_parser_optimized_benchmark_2026-10-21.json`.
- [x] Ускорить PBP parsing через C-backed lxml tree с fallback на встроенный parser: v4→v5 на 30 архивных матчах и 8 проходах, 1.160 с→0.327 с на корпус, +254.6% throughput, 240/240 exact event signatures; 62 parser/research tests passed. Research и `flashscore-worker` пересобраны; оба healthy, в обоих установлен lxml 6.1.3 и parser v5 с хешем исходника `5e6c637c…`. Neural остался без lxml и использует fallback. Runtime full-network throughput benchmark по-прежнему открыт. Артефакт: `docs/research/tennis_point_parser_lxml_benchmark_2026-10-21.json`.
- [x] Сократить PBP навигацию при известном подтверждённом Flashscore match ID: открывать PBP index напрямую и использовать summary-first путь как fallback. Read-only pilot `WAlDxZFD` получил два сета через 3 навигации против 4 у baseline; оба пути вернули полную историю. Latency на одном матче вариативна, поэтому ускорение временем не обобщается; проверяемый выигрыш — один page request/source-slot acquisition меньше. Тесты маршрута 68 passed. Артефакт: `docs/research/flashscore_pbp_route_benchmark_2026-10-21.json`.
- [x] Снизить работу на горячем пути: пакетно читать player/match/observation projections, сериализовать перекрывающиеся импорты по Flashscore ID и убрать локальный межсетовый sleep, дублирующий общий source regulator. До фиксации есть production-симптом гонки `uq_ext_tennis_matches_source`; после фиксации два конкурентных writer дали ровно одну строку. Runtime-измерения и ограничения записаны в `docs/research/flashscore_performance_2026-10-21.md`; полный paired benchmark длительности, Chromium и сетевых запросов ещё нужен.
- [x] Переиспользовать Chromium для синхронных PBP/live-discovery задач внутри worker-потока с отдельным browser context на каждую работу и ротацией по 50 задач/15 минут/ошибке; ошибки навигации инвалидируют browser. Изоляция/ротация покрыты 13 parser/pool тестами. Локальный warm-context microbenchmark: 0.094 с против 0.762 с для свежего Chromium; реальный network paired benchmark остаётся открытым.
- [x] Переиспользовать ограниченный HTTP keep-alive pool поиска профилей; каждый запрос сохраняет общий source-slot limiter. Чередующийся paired smoke из 4 запросов на один endpoint/query: медиана 0.21665 с cold против 0.07025 с pooled (-67.6%); все ответы HTTP 200. Полный paired benchmark импорта/PBP и 30-минутный soak всё ещё открыты. Данные: `docs/research/flashscore_http_pool_benchmark_2026-10-21.json`.
- [x] Исправить смещение журналов Research: при `TZ=Europe/Moscow` записи подписывались `UTC`, оставаясь на три часа впереди реального времени; formatter теперь явно использует UTC.
- [x] Исправить ложный `worker.healthy=false` при занятом event loop: heartbeat процесса пишется отдельным daemon-thread и не продлевает lease заданий. После выкладки 10:30 UTC API вернул свежий heartbeat/healthy, worker продолжил dispatch; unit test прошёл. Длительный soak и проверка рестарта/lease fencing после этой конкретной правки ещё остаются.
- [x] Подключить серверную фильтрацию до пагинации, отображение приоритета/следующей попытки, сводные статусы и просроченный PBP; пересобрать backend/frontend, проверить API-фильтр и health контейнеров.
- [x] Сохранять последний успешный ответ Flashscore-очереди в браузере по фильтрам и странице; при сетевой ошибке восстановить его с отметкой устаревания, а при отсутствии кэша показать явную ошибку вместо пустой очереди. Старый ответ медленного запроса не может затереть более новый. Frontend production build с TypeScript завершился; после выкладки frontend healthy, health endpoint HTTP 200.
- [x] Браузерный smoke очереди: отображение queued/running/retry/blocked/identity-confirmation/waiting-players/waiting-match/completed, фильтр статуса, переход на страницу `offset=50` и открытие карточки матча в detail modal.
- [x] Проверить фильтры типа PBP и режима live, явное сообщение при ошибке API без кэша и редкие статусы (`finalized`, `skipped`, `matched`, `ambiguous`) браузерным smoke.

### Повторный admission-аудит после выкладки парсера — 2026-10-21 12:49 UTC

- [x] Пересчитать единый manifest на рабочей БД и сохранить результат в `docs/research/tennis_label_audit_2026-10-21_post-lxml.json`; manifest `docs/research/tennis_training_manifest_da9f2222643d.json` (1,986 матчей, 1,588 development / 398 закрытый holdout, quality metrics не открывались).
- [x] Зафиксировать размеченные матчи по целям: очко 28 (timeline 21; начальный train 9, validation 3/0/3/1, holdout 5), гейм 28 (аналогично), сет 28 (счётчики по окнам сохранены в JSON), матч 2,278 (начальный train 952, validation 159/159/159/159, holdout 398). У P/G/S в историческом массиве 0 observed примеров; 3,304/610/66 outcomes ретроспективны.
- [x] Зафиксировать блок допуска: `blocked`; P/G/S не проходят минимум 500/100/200, матчевые метки не проходят temporal precedence (время получения старых результатов — 21 октября, после части validation прогнозов), feature PIT 0/3,754. Четыре блокирующие причины и per-target счётчики находятся в JSON; обучение не запускалось.
- [x] Проверить текущую очередь после выкладки: 42 подходящих матча, 4 подготовленных игрока, 173 задания (83 queued, 84 waiting_players, 2 retry), 0 overdue PBP, 46 PBP-документов и 49 подтверждённых связей за последний час; worker healthy. Это моментальный срез, не доказательство достаточной пропускной способности.

**Acceptance:** миграция 026 применена; durable очередь автоматически охватывает все подходящие события; две сетевые операции работают параллельно с общей частотой не чаще одной новой операции каждые две секунды; Flashscore очередь и состояние executor видны во вкладке scheduler; основной betting/settlement pipeline не задерживается.

### Phase 50 — Исправление Flashscore после часового прогона

- [x] Ограничить поиск профиля тремя вариациями латинской записи; timeout, пропущенный ответ, HTTP-блокировку и ошибку разбора не считать подтверждённым `not_found`.
- [x] Сохранять `next_run_at` для профилей; сетевые ошибки повторять с backoff, подтверждённый пустой поиск — через шесть часов, неоднозначность оставлять на ручное подтверждение.
- [x] Добавить durable очередь поиска внешнего live-матча и кэш общей live-страницы на 30 секунд; совпадение подтверждать только по двум подтверждённым ID игроков.
- [x] Перевести PBP без профилей/связи в `waiting_players`/`waiting_match`; будить PBP сразу после подтверждения связи и не учитывать ожидание как просрочку.
- [x] Вывести поиск матча, причины ожидания и повтора, а также счётчики документов и связей за последний час во вкладке scheduler.
- [x] Исправить фактический browser-search путь: ждать гидратацию Flashscore, нажимать видимую кнопку и выбирать видимое поле `Type your search here`; live smoke query `Arantxa Rus` вернул ровно профиль `Rus Arantxa` (`84GyAaXB`).
- [x] Закрепить поиск профиля за `/tennis/`: runtime-логи показали, что общий корень открыл футбольную главную; после переключения Referer и home URL ограниченный запрос через общий source slot вернул ровно `Rus Arantxa` (`84GyAaXB`).
- [x] Убрать нестабильный UI-поиск из горячего пути: использовать тот же read-only tennis-player JSON endpoint, который вызывается обычным поиском Flashscore; 403/429, timeout и некорректный ответ остаются ошибками источника. Runtime через общий source slot вернул `Rus Arantxa` (`84GyAaXB`) за 2.18 с с учётом получения слота; после выкладки в 11 заданиях, обновлённых после 05:24 UTC, больше не было `SEARCH_INPUT_MISSING` (остались `SEARCH_COMPLETED_NO_MATCH` и `REFRESH_ABORTED`).
- [x] Выдавать общий durable source slot для каждого фактического HTTP GET варианта поиска, чтобы двухсекундная пауза применялась между endpoint-запросами; регрессия проверяет три запроса на три варианта имени.
- [x] Применить миграцию 028, развернуть Research/`flashscore-worker` и актуальные backend/frontend images; проверить health, очередь и event API. Backend API suite проходит.
- [x] Запустить PostgreSQL integration tests: истёкший токен не может завершить/перезаписать lease после переназначения; `A → B → A`, поздний разбор, пустая версия и отмена связи сохраняют старый ответ `as_of`. Backend as-of test — 1 passed; Research lease-fencing test — 1 passed; временные строки удаляются в `finally`.
- [x] Runtime-проверить `as_of` на реальном связанном Fonbet event `69109956` / Flashscore `fVArlUH0`: API вернул `total=226`; повтор первой страницы при том же `resolved_as_of` идентичен, страница offset=50 не пересекается с первой.
- [ ] Остановить/перезапустить worker во время активной браузерной попытки и доказать recovery/checkpoint; fixture integration tests не заменяют этот runtime acceptance.
- [ ] На конкретном live-матче отдельно сверить задержку нового PBP-документа и непустой point-history API. Общий рабочий архивный поток теперь содержит 79 PBP-версий для 20 внешних матчей/связей; 30-минутный runtime soak завершён, но измерения относятся к общей live/archive очереди и не доказывают задержку конкретного live-матча.

**Acceptance:** нельзя закрыть этап по одному успешному build. Требуются развернутые контейнеры, повторяющиеся сетевые ошибки с планом повтора, реальный PBP-документ и подтверждённая связь; research не создаёт ставок или ledger entries.

### Phase 51 — Административная очередь подтверждений

- [x] Добавить универсальные сохранённые заявки и append-only журнал решений для профилей игроков и связей матчей; идемпотентно перенести ожидающие legacy-кандидаты миграцией.
- [x] Добавить проверку версии заявки и атомарные решения «Да»/«Нет»: подтверждение запускает импорт метрик/PBP, отказ сохраняет отвергнутую пару и повторно ставит поиск через durable очередь.
- [x] Исключать отвергнутые кандидаты из следующих поисков; показывать явное состояние отсутствия альтернативных данных и не использовать неподтверждённые метрики/матчи.
- [x] Автоматически подтверждать единственное точное полное совпадение имени после безопасной нормализации; неоднозначные, похожие и конфликтующие варианты оставлять в очереди ручной проверки.
- [x] Добавить серверную сессию админки с HttpOnly cookie, защитить API решений и legacy route ручной связи; удалить парольный hash из frontend.
- [x] Добавить в админку вкладку «Подтверждения» с количеством ожидающих решений, фильтрами типов/статусов, контекстом источника, двумя кнопками, обновлением раз в пять секунд и журналом решений.
- [x] Разрешить исправление последнего решения противоположным действием и проверять, что оно не меняет прежний `as_of`; при отклонении ошибочно подтверждённого профиля append-only инвалидировать его импортированные результаты и записать пересчитанный снимок метрик.
- [x] Проверить сохранённое решение по `Иванович И` ↔ `Ivanovic Gala`: ошибочная связь уже исправлена с `approved` на `rejected`; похожая, но отдельная запись `Иванович Г` ↔ тот же профиль сохранена, потому что фамилия и инициал совпадают.
- [ ] Выполнить миграцию в штатном контейнере, настроить серверные `ADMIN_*` секреты, собрать backend/research/frontend и проверить оба типа заявок на контрольных кандидатах.
- [ ] Проверить два конкурентных решения, повторное предложение rejected пары, восстановление очереди после рестарта, `as_of` до/после решения и отсутствие ставок/проводок.

**Acceptance:** оператор принимает или отклоняет обе категории связи из админки; каждое решение переживает перезапуск и имеет журнал; отклонённая пара не предлагается снова; отсутствие альтернатив видно в текущей очереди; неподготовленная серверная авторизация закрывает все административные решения.

### Phase 52 — Допуск к обучению независимого прогноза

- [x] Исправить аудит окон: требуемый минимум считать только по уникальным матчам с меткой соответствующей цели; отдельно показывать все матчи-кандидаты.
- [x] Исключить `scheduled_start_at` и `finished_at` как замену времени доступности результата; недатированные исходы не попадают в manifest.
- [x] Проверять временное предшествование обучающих меток прогнозам каждого validation-fold; повторное получение старого результата не может легализовать его использование в прошлом. Аудит 06:44 UTC провалил temporal gate во всех четырёх match-folds.
- [x] Сохранять JSONL provenance меток со ссылками на Fonbet-снимок, PBP-документ и версию разбора, позицию и подтверждённую связь сторон.
- [x] Добавить очередь исторического PBP backfill для завершённых одиночных числовых Fonbet-событий вне окна прогнозов; live остаётся выше архива в очереди и в глобальном регуляторе.
- [x] Отделить полный DB-скан исторического backfill от 15-секундного live discovery: архивный enrollment по умолчанию раз в 900 секунд (`FLASHSCORE_ARCHIVE_DISCOVERY_SECONDS`), live/PBP цикл сохраняет 15 секунд.
- [x] Убрать per-event SQL для pending match confirmations и названий лиг из исторического enrollment; загружать эти справочники пакетно на один проход.
- [x] Зафиксировать закрытый `tennis_odds_free_v1` feature allowlist; вход игнорирует odds/market-поля и включает спортивную историю игроков и счёт.
- [x] Согласовать feature tag с allowlist: прогнозы baseline сохраняют `tennis_odds_free_v1`; прежний литерал `tennis_odds_free_v2` был несогласован с 30-полевым контрактом и заменён общим version constant.
- [x] Устранить неявный best-of-3 fallback: live и prematch прогноз возвращают `UNSUPPORTED_FORMAT`, если формат не равен подтверждённому best-of-3/5; regression test проходит.
- [x] После дополнительного сбора пересчитать аудит и manifest на рабочей БД: [`tennis_label_audit_2026-10-21_0750.json`](docs/research/tennis_label_audit_2026-10-21_0750.json), digest `6d13c80e7fdd…`. P/G/S — 22 размеченных матча всего, 6 в начальном train, 1–2 на validation и 3 holdout; матч — 892/149×4/372, но все temporal folds провалены. Feature PIT 0/3,266, 470 source result timestamps не доказаны; обучение остаётся закрыто.
- [x] Добавить fail-closed PIT-проверку timestamps по группам источников; неполные/будущие времена отклоняются, пока данные не сертифицированы.
- [x] При принятии официального Fonbet-результата сохранять `available_at`; повтор неизменённого результата сохраняет первое время, исправление получает время новой доступности. Старые строки не backfill-ятся по расписанию или `finished_at`.
- [x] Применить обновлённый Research/`flashscore-worker` и Backend; все три контейнера healthy. Backfill обнаружил 2,206 завершённых одиночных событий, 2,205 ждут профилей, восемь заданий игроков поставлены в очередь, один матч уже покрыт PBP.
- [ ] Проверить восстановление очереди после перезапуска и измерить, что архивные запросы не задерживают live при общем сетевом лимите.
- [ ] Собрать и подтвердить PBP для сотен уникальных матчей; ежедневно публиковать воронку профиля → матч → связь → документ → метки четырёх целей.
- [x] Зафиксировать закрытый список odds-free входов и добавить fail-closed проверку времён доступности по группам источников; текущие реальные данные пока не сертифицированы. Unit-тесты движка и сценариев на трёх очках/двух геймах проходят.
- [x] Пересобрать preflight-аудит после 30-минутного soak на рабочей БД 2026-10-21; сохранить отчёт, provenance JSONL и единый manifest. Срез 06:23 UTC: 2,299 eligible одиночных событий; point 13 матчей/233 примера, game 13/229, set 12/23, match 2,132. Все PBP-примеры retrospective. Manifest: 1,840 событий (1,472 development / 368 holdout); на общей временной линии игровых целей 7 матчей, из них 2 train, по 1 на каждый validation, 1 holdout. Gate blocked. Отчёт и причины в `docs/research/tennis_training_preflight_2026-10-21.md`; digest `c51b3556c006…`.
- [x] Повторить audit после накопления PBP и добавить source-level feature PIT и temporal precedence проверки; свежий срез `docs/research/tennis_label_audit_2026-10-21_postbrowserpool.json` (06:56 UTC): P/G/S по 17 уникальных матчей, match 2,140; manifest 1,848 (1,478 development / 370 holdout), feature PIT 0/2,846, temporal folds 0/4 прошли. Gate `blocked`; holdout quality не открывался.
- [x] Перезапустить 30-минутный soak после фикса `DetachedInstanceError`: 31/31 queue API samples успешны, worker healthy, overdue PBP 0, документы/связи в rolling hour 15→29. Два внешних навигационных timeout привели к retry; слоты освобождались. Подробности и сырые измерения: `docs/research/flashscore_performance_2026-10-21.md` и `docs/research/flashscore_soak_2026-10-21_postfix.jsonl`.
- [x] Исправить восстановление заданий `waiting_confirmation`, для которых уже нет активной заявки: повторно ставить безопасный поиск/обновление, не подтверждая кандидата; PBP возобновлять только после связи. Добавлены четыре unit-сценария переходов.
- [x] После выкладки проверить восстановление старых player jobs: 200 orphaned ожиданий возвращены в очередь, ноль заданий осталось без pending-заявки; три новые неоднозначные заявки созданы и удерживают три связанные работы в `waiting_confirmation`.
- [ ] Дождаться успешного завершения восстановленных заданий и отдельно проверить отказ кандидату → новый поиск → штатное `NO_ALTERNATIVE_PROFILE_DATA` при отсутствии альтернативы.
- [x] Проверить происхождение 2,249 сохранённых `available_at`: они впервые появились при повторном получении старых результатов 2026-10-21 около 04:31–04:33 UTC; не считать их доступными на дату матча. В свежем аудите 05:26 UTC 456 из 2,289 eligible событий остаются без надёжного result timestamp.
- [ ] Довести исторические источники метрик игроков, подачи и поверхности до полного PIT-контракта и отдельно сертифицировать фактический dataset builder; после v4 0/3,171 примеров сертифицированы.
- [ ] Утвердить контракт первого эксперимента после решения по несертифицированным surface/serve полям; не включать текущие/ретроспективные значения ради заполнения вектора.
- [ ] Пересобрать manifest и пройти для каждой цели train ≥500, все четыре validation ≥100, holdout ≥200, оба класса, feature PIT и temporal precedence. Исторический срез 07:50 UTC содержал 1,860 матчей и уже был superseded аудитом 08:10 UTC ниже; оба отчёта показывают закрытый gate. Текущие цифры находятся в последнем пункте этого этапа.
- [ ] Только после допуска обучить LR/GB/GRU на одном manifest, оценить на development, внести документированные исправления и открыть holdout один раз.
- [x] Исправить финальный статус допуска: он требует coverage, оба класса во всех окнах, temporal precedence, feature PIT и доказанное время у каждого исхода в manifest. Матчи без надёжного времени исключаются из manifest вместо blanket-блокировки всего архива. Добавлены тесты отказа и успешного открытия gate.
- [x] Пересчитать аудит по рабочей БД 2026-10-21 08:16 UTC: `tennis_label_audit_2026-10-21_latest.json`, manifest `tennis_training_manifest_fae6e9cf2297.json`, examples `tennis_training_examples_c25d94539a5e.jsonl`. P/G/S: 23 матча / 678 точечных, 23 / 419 геймовых, 23 / 49 сетовых; observed PBP 0. Match: 2,155 исходов. Manifest 1,863 (1,490 development / 373 holdout); 420 размеченных событий без времени исключены. PIT 0/3,301; gate `blocked`.
- [x] Добавить изолированные fixture-only primitives LR/GB/GRU с закрытым `tennis_odds_free_v1`, masking четырёх целей, весами по матчу, causal sequence до 64 состояний, seed, CPU fallback и проверкой checkpoint. Они не загружают рабочие данные, не обучались на БД и не регистрировались.
- [x] Проверить Flashscore очередь после свежего аудита: 08:13 UTC — 32 подходящих события, 7 подготовленных участников, 133 job rows, 37 документов/39 связей за последний час, overdue PBP 0; worker healthy, source slots 1/2. Ошибки retry классифицированы; старые failed jobs не сбрасывались массово.
- [x] Уточнить хронологию: 476 eligible событий без доказанного result time видны в exclusions; 420 из них имеют допустимый матчевый исход, но исключены из manifest. Все включённые результаты имеют source-время. Количественные и PIT-блокеры остаются.
- [x] Исправить независимую source parity сверку: сравнивать все распознанные DOM score states, включая `ambiguous`, а не только подтверждённые обучающие переходы. Это устраняет ложное несовпадение для пропущенного начального очкового состояния, не повышая его до label. Review 08:19 UTC: 36 матчей, 25 с PBP, 222 гейма/1,169 состояний, 198 deuce/advantage, 39 tie-break rows, ноль score/server/tie-break winner mismatches; отчёт `tennis_pbp_source_review_2026-10-21_0821.json`. Ручной осмотр оригиналов по-прежнему открыт.
- [x] После интеграционных проверок пересчитать аудит 2026-10-21 08:23 UTC: `tennis_label_audit_2026-10-21_after_asof.json`, общий manifest `tennis_training_manifest_370f2d08cf99.json` (1,866 матчей, development 1,492, закрытый holdout 374), provenance `tennis_training_examples_b1989a23f8a5.jsonl`. P/G/S имеют по 24 размеченных матча (731/442/51 примеров), match — 2,158; feature PIT сертифицировано 0/3,382; gate остаётся `blocked` по недостаточному покрытию, temporal precedence и feature PIT. Holdout quality не открывался.
- [x] Повторить автоматическую сверку оригинальных HTML на 20 матчах: 9 матчей содержат сравнимые point rows, 76 игр и 397 score states; расхождений счёта, подачи или победителя тай-брейка нет; gaps есть у 12 матчей. Отчёт `tennis_pbp_source_review_2026-10-21_after_asof.json`. Это автоматическая DOM-сверка, не ручная верификация человеком.
- [x] Исправить выбор версий PBP: поздняя страница без распознанной PBP-вкладки/истории не считается пустым исправлением и не затирает валидный сет; удаление допускается только явным `explicit_empty_correction`. Одно правило применено к API `as_of`, label-аудиту и корпусному отчёту. Тесты покрывают обычную пустую ошибочную страницу и явный tombstone.
- [x] Повторно пересчитать после исправления источника версий: `tennis_label_audit_2026-10-21_authoritative_versions.json`, manifest `tennis_training_manifest_8d56bda87122.json`. P/G/S: 24 матча на каждую цель; подтверждённые примеры выросли до 759/452/52; match — 2,162. Feature PIT 0/3,382, gate `blocked`.
- [x] Сверить последний корпус после исключения неавторитетных пустых версий: 20 матчей, 5 с PBP, 4 финальных счёта совпали полностью, 17 связей имеют подтверждённый порядок сторон, gaps остались у одного матча, переходных нарушений 0. Отчёт `tennis_pbp_corpus_review_2026-10-21_authoritative_versions.json`.
- [x] Пересобрать и выкатить `backend`, `research` и `flashscore-worker`; все healthy. Очередь API HTTP 200, после рестарта worker heartbeat обновился; исторический point-history для `WAlDxZFD` вернул 164 события и корректно сохранил PBP. Snapshot очереди 08:40 UTC: 40 подходящих событий, 4 подготовленных игрока, 46 документов и 50 подтверждённых связей за rolling hour, overdue PBP 0. Это smoke после выкладки, не 30-минутный soak.
- [x] Сверить 20 архивных матчей, действительно содержащих PBP: 175 геймов и 923 очковых состояния, включая 156 deuce/advantage и 28 тай-брейк-состояний; score/server/тай-брейк расхождений — 0. У одного матча есть пропуски, они остаются исключёнными из меток. Отчёт: `docs/research/tennis_pbp_manual_review_2026-10-21.md` и исходные материалы `tennis_pbp_manual_review_2026-10-21.json`. Это воспроизводимая техническая сверка сохранённых DOM/HTML, не независимая сертификация поставщика.
- [x] Провести первый 30-минутный read-only soak 08:42–09:12 UTC: 60/60 queue API ответов успешны, worker healthy 60/60, cooldown отсутствовал, слоты не превышали 2. В последнем измерении появился один краткий overdue PBP, через три минуты он исчез. Этот прогон снял baseline, но ещё не является финальной приёмкой: после него runtime-логи обнаружили `asyncio.run()` внутри running event loop.
- [x] Исправить async-safe запуск profile scraper через отдельный event loop/thread при вызове из активного loop; покрыть регрессией. После выкладки research и flashscore-worker импортировали профили без `asyncio.run` ошибок. Research suite вместе с tennis point-history tests: 116 passed.
- [x] Recreate research и flashscore-worker во время реальной сетевой операции (`network_slots=1/2`, очередь показывала running jobs игрока и PBP). Новый worker поднялся, продолжил dispatch; истёкших running leases после восстановления не осталось. Lease-token fencing отдельно проверен PostgreSQL integration test.
- [x] Перевести profile refresh на отдельный killable child process с page checkpoints, проверкой текущего fencing token/lease и terminate при timeout или потере lease; обработчик синхронной coroutine также безопасен при вызове из активного asyncio loop. Research + tennis point-history suite — 116 passed.
- [x] Reap player child process через `join()` после штатного завершения; повторный тестовый набор — 116 passed. Финальные образы пересобраны и пересозданы в 09:32 UTC при активном сетевом слоте `1/2`; после старта backend-side worker health и queue API восстановились.
- [x] Доработать и reap-ить оба типа profile child process: ежедневный participant refresh и полный Flashscore archive import; полный набор остаётся 116 passed. Research/worker пересозданы 09:35 UTC; queue API и оба сервиса healthy, overdue PBP 0.
- [x] Завершить финальный 30-минутный soak после последней сборки/reaping: [`flashscore_soak_2026-10-21_post-process-reaping.jsonl`](docs/research/flashscore_soak_2026-10-21_post-process-reaping.jsonl), 60/60 samples за 1,800 секунд, все HTTP 200, worker healthy 60/60, cooldown 0, максимум 2 сетевых слота. В 10 замерах был краткий overdue PBP (12 суммарных отметок), последний sample и итоговая очередь — 0; в контрольном API-срезе задержек не было. Последняя воронка: 54 подходящих события, 12 подготовленных участников, 39 документов и 43 подтверждённые связи за час.
- [x] Пересчитать аудит после soak: [`tennis_label_audit_2026-10-21_final.json`](docs/research/tennis_label_audit_2026-10-21_final.json), 10:05:52 UTC; manifest `tennis_training_manifest_f2ff18434799.json` — 1,902 матча (1,521 development / 381 закрытый holdout). P/G/S: 25/25/25 уникальных размеченных матчей, 0 наблюдавшихся примеров; match — 2,194. Feature PIT 0/3,509; результатный timestamp неизвестен для 498 событий, 420 с метками исключены из manifest. Gate `blocked`: мало игровых меток, временной порядок train→validation нарушен, feature PIT не сертифицирован; holdout quality закрыто.
- [x] Повторно проверить regression: research/PBP suite — 65 passed; lease-fencing integration в проектной сети — 1 passed; odds-free model/checkpoint tests — 17 passed. Изолированный запуск integration-теста без PostgreSQL не считается результатом; штатный повтор прошёл в `neurobet_internal_net`.
- [x] Исправить ложную нездоровость Flashscore executor при занятом dispatch loop: отдельный daemon-thread пишет process heartbeat, не изменяя job lease. После выкладки 10:30 UTC: API `healthy=true`, heartbeat свежий, dispatch возобновился; regression — 45 Research/Flashscore тестов, lease-fencing integration — 1, odds-free LR/GB/GRU/forecast — 17.
- [x] Пересобрать audit после heartbeat rollout: [`tennis_label_audit_2026-10-21_post-heartbeat.json`](docs/research/tennis_label_audit_2026-10-21_post-heartbeat.json), 10:36 UTC; manifest `tennis_training_manifest_4e93d0573513.json` — 1,921 матча (1,536 development / 385 holdout). P/G/S — по 25 размеченных матчей, но в общей временной линии лишь 18: train 8/11/12/14, validation 3/1/2/1, holdout 3. Все игровые ленты retrospective; feature PIT 0/3,528; temporal precedence провалена. Gate blocked, holdout quality закрыто.
- [x] Завершить отдельный 30-минутный soak после heartbeat rollout (`docs/research/flashscore_soak_2026-10-21_post-heartbeat-thread.jsonl`): 60/60 HTTP 200, worker healthy 60/60, cooldown 0, сетевые слоты максимум 2; queue API p95 1.055 с, максимум 4.676 с. В 13/60 замеров был краткий overdue PBP (13 суммарных наблюдений), последний срез — 0; на последнем замере 49 событий в окне, 8 подготовленных участников, 37 документов и 37 подтверждённых связей за rolling hour.
- [x] Пересобрать классификацию тура после использования `tour_level` подтверждённых Flashscore-связей для аудита; production audit [`tennis_label_audit_2026-10-21_postsoak_tour.json`](docs/research/tennis_label_audit_2026-10-21_postsoak_tour.json), manifest [`tennis_training_manifest_5ec68e79d088.json`](docs/research/tennis_training_manifest_5ec68e79d088.json), 1,933 матча (1,546 development / 387 закрытый holdout). Турнирная категория: ATP 194, WTA 193, Challenger 351, ITF 28, unknown 1,661; источник классификации: Fonbet competition 723, Flashscore archive 43, unknown 1,661. Ретроспективная категория остаётся только описательной метаданной. Helper tests — 12 passed.
- [x] Повторить допуск на том же коде аудита после soak: point/game/set содержат по 26 уникальных матчей с метками (825/487/59 примеров), match — 2,225 матчей; соответствующие классы 404/421, 243/244, 26/30 и 1,156/1,069. На общем manifest timeline P/G/S представлены лишь 19 матчами: initial train 11/11/11, validation 0/0/1/1 и holdout 6. Все игровые примеры retrospective, observed 0; feature PIT сертифицирован 0/3,593. 491 результат остаётся с неизвестным временем и 420 размеченных событий исключены. Match окна удовлетворяют численным минимумам и обоим классам, но temporal precedence провален во всех четырёх folds. Gate `blocked`; метрики закрытого holdout не раскрывались.
- [x] Исправить приоритет post-match обновления профилей: bounded pass рассматривает события, впервые замеченные Fonbet за последние сутки, и ставит ранее пытавшиеся PBP-матчи впереди; подтверждено production-вызовом, который поставил в refresh queue участников события `69155384`. Пересобранный Research healthy, `test_tennis_flashscore.py` — 49 passed.
- [ ] Подтвердить завершение обновления профилей и повторный поиск `69155384`, затем получить для реального связанного матча непустую PBP-последовательность. Для live `69148307` подтверждённая ссылка привела к сохранённой версии summary, но в ней 0 point events; API остаётся `retry/unmatched`, так что end-to-end PBP пока не принят.
- [x] Повторить admission audit в 11:38 UTC после сбора: [`tennis_label_audit_2026-10-21_latest.json`](docs/research/tennis_label_audit_2026-10-21_latest.json), единый manifest [`tennis_training_manifest_7bf27cd6e233.json`](docs/research/tennis_training_manifest_7bf27cd6e233.json) (1,950 матчей, 1,560 development / 390 закрытый holdout), provenance `tennis_training_examples_4f756ad2d685.jsonl`. P/G/S — 26 размеченных матчей каждый, но на общей timeline только 19: initial train 9, validation 3/0/3/0, holdout 4; все PBP ретроспективны. Матч — 2,242 метки, train/validation/holdout по числам достаточны, но temporal precedence провалена во всех folds. Feature PIT 0/3,638; 484 неизвестных result timestamps, 420 размеченных исключены. Gate остаётся `blocked`; holdout quality не открывался, обучение не запускалось.
- [x] Устранить потерю допустимых сценарных веток при неизвестном подающем; проверить полную нормировку вероятностной массы и terminal absorbing state. Odds-free forecast + fixture-only LR/GB/GRU: 21 тест прошёл.
- [x] Пересобрать/пересоздать `neural`, сверить SHA-256 исходника с контейнером и проверить healthy runtime. Свежий audit [`tennis_label_audit_2026-10-21_after_scenario_fix.json`](docs/research/tennis_label_audit_2026-10-21_after_scenario_fix.json), manifest [`tennis_training_manifest_after_scenario_fix.json`](docs/research/tennis_training_manifest_after_scenario_fix.json): 1,961 матч (1,568 development / 393 holdout); P/G/S — 26 матчей на цель, общая timeline 19, train 9 / validation 3/4/0/3 / holdout 0; match — 2,253, но temporal precedence провалена во всех folds. Feature PIT 0/3,649; 484 неизвестных времён результата, 420 меток исключены. Gate `blocked`; holdout quality закрыто, рабочее обучение не запускалось.
- [x] Пересчитать допуск на свежем live-сборе в 12:16 UTC: [`tennis_label_audit_2026-10-21_after_live_queue.json`](docs/research/tennis_label_audit_2026-10-21_after_live_queue.json), manifest `tennis_training_manifest_after_live_queue.json`; P/G/S 28 матчей на цель, общая timeline 21, train 8 / validation 4/0/3/1 / holdout 5, observed PBP 0; match 2,258, но temporal precedence провалена; feature PIT 0/3,734, 487 неизвестных result timestamps. Gate остаётся `blocked`.
- [x] Исправить реальный `KeyError` при неоднозначном ответе live-match discovery: сохранить кандидатов в подтверждения, вернуть tagged status `ambiguous` и припарковать PBP job. Research/PBP suite 61 passed; Research и `flashscore-worker` пересобраны, healthy, очередь восстановилась, overdue PBP 0 в контрольном snapshot.
- [x] Повторно выполнить admission audit на рабочей БД после выкладки: [`tennis_label_audit_2026-10-21_pretraining_1222.json`](docs/research/tennis_label_audit_2026-10-21_pretraining_1222.json), manifest [`tennis_training_manifest_60838cc5c760.json`](docs/research/tennis_training_manifest_60838cc5c760.json) (1,969; 1,575 development / 394 закрытый holdout), provenance `tennis_training_examples_536efecf17c3.jsonl`. P/G/S — 28 матчей с меткой всего, общая timeline 21, initial train 8, validation 4/0/3/1, holdout 5; observed examples 0. Match — 2,261 размеченный матч и численно достаточные окна, но temporal precedence провалилась. Feature PIT 0/3,737; 485 результатов с неизвестным временем, 420 размеченных событий исключены. Gate `blocked`, holdout quality не открывалось.
- [x] После выкладки проверить runtime: Research, `flashscore-worker`, Backend и Neural healthy; queue API в 12:23 UTC показал 43 подходящих события, 10 подготовленных игроков, 44 документа и 47 подтверждённых связей за последний час, overdue PBP 0, worker healthy. Dispatch выполнялся без повторения исправленного `KeyError` в новых логах.
- [ ] На реальном матче вызвать ветку с несколькими live-кандидатами, проверить ровно одну pending-карточку на каждую пару, waiting_confirmation без lease/слота и продолжение PBP после решения администратора.
- [x] Хранить новые официальные результаты и corrections append-only в `fonbet_official_result_observations`; adjacent duplicate подавляется, `A → B → A` остаётся тремя версиями. Изолированный unit regression прошёл. Legacy events без доказанного timestamp не backfill-ятся.
- [x] Пересобрать/выкатить Backend с результатной историей; health healthy. Интеграционный прогон реального matcher в транзакции с rollback подтвердил `A → B → A` и подавление следующего одинакового ответа (3 версии, ноль сохранённых тестовых строк). Исправлено сравнение: контрактный generation `timestamp` не превращает повтор в correction.
- [ ] Повторить аудит после накопления новых подтверждённых Fonbet result observations; не backfill-ить legacy timestamp и не легализовать неизвестные времена.

**Acceptance:** отчёт допуска считает только размеченные матчи, содержит provenance и объясняет каждый блокирующий гейт; в рабочее обучение можно перейти лишь после прохождения всех четырёх целевых порогов и point-in-time сертификации.

Открытые до обучения пункты включают PIT-происхождение метрик игроков/подачи/покрытия, доказанное время доступности обучающих результатов до validation-прогнозов и достижение порогов P/G/S. Soak подтвердил здоровье сервиса, но не гарантирует отсутствие кратких задержек: в 13/60 sample был overdue PBP, в последнем и итоговом API-срезе — 0. На live Fonbet event `69148307` подтверждённая ссылка `vV6BKNOq` теперь используется даже до импорта строки `ExtTennisMatch`; после выкладки на 10-й попытке архивирован документ, но у страницы нет PBP-разметки (`EXPECTED_SET_1_HISTORY_NOT_AVAILABLE`), API показывает `versions_total=1`, `total=0`, job остаётся на повторе. Завершённый `69155384` пока ждёт игроков/индексации (`HISTORICAL_FLASHSCORE_MATCH_NOT_INDEXED`, 7 попыток). End-to-end приёмка PBP остаётся открытой до фактических очковых событий и непустого API. Lease fencing и восстановление dispatch после пересоздания сервиса во время работающей очереди проверены; отдельное принудительное убийство активного executor child остаётся открытым. Сверка 20 PBP-матчей — техническая source/DOM-проверка; независимая ручная или vendor-сертификация не заявляется.

### Phase 53 — Заморозка спорных связей

- [x] Сохранять неизменную версию pending-заявки при повторном обнаружении той же пары.
- [x] Парковать задания игрока и матча в `waiting_confirmation`, очищать lease и не занимать слоты исполнителя.
- [x] Исключить ожидающие подтверждения сущности из повторной подготовки, live-поиска, PBP dispatch и архивного backfill.
- [x] После решения возобновлять работу только когда для сущности не осталось других pending-кандидатов; отказ исключает конкретную пару из нового поиска.
- [x] Передавать backend detail ответа `409` в админку вместо одинакового сообщения для всех конфликтов.
- [x] При решении по сохранённому профилю восстанавливать отсутствующую строку `tennis_player_profiles` из durable evidence, не отклоняя заявку при наличии source ID и URL.
- [x] Автоматически подтверждать уникальное полное имя и консервативный случай «однословная полная фамилия + совпавший инициал»; составные и дефисные имена оставлять в ручной очереди.
- [ ] Проверить повторяющиеся scheduler циклы, одновременные решения и перезапуск executor на реальной pending-заявке; собрать и развернуть backend, research, `flashscore-worker` и frontend.

**Acceptance:** карточка сохраняет ID и версию до решения; ожидание не удерживает worker/network slot; после «Да» или последнего «Нет» зависимое задание продолжается, а остальные матчи обрабатываются.

### Свежая проверка допуска после live PBP — 2026-10-21 13:20 UTC

- [x] Пересобрать admission audit по текущей рабочей БД: [`tennis_label_audit_2026-10-21_after_live_pbp_check.json`](docs/research/tennis_label_audit_2026-10-21_after_live_pbp_check.json), manifest [`tennis_training_manifest_89fd0bc624ef.json`](docs/research/tennis_training_manifest_89fd0bc624ef.json), provenance [`tennis_training_examples_c94c03672579.jsonl`](docs/research/tennis_training_examples_c94c03672579.jsonl). Цели point/game/set: по 29 размеченных матчей, но только 22 входят в общий chronology manifest; validation = 3/0/3/1 и holdout = 6. Match: 2,286 матчей с меткой, manifest 1,994 (1,595 development / 399 holdout), validation = 160/159/159/160. Temporal precedence провалена во всех четырёх validation-folds.
- [x] Проверить независимость классификации PBP от неправильной ссылки на двух активных live примерах. Страницы `d0zFZ3Fj` и `dI1TK15L` имеют сохранённый исходный HTML и валидные подтверждённые связи, но обе показывают Summary/Odds/H2H/Draw без PBP/set links; текущая повторная попытка `d0zFZ3Fj` закончилась сетевым TimeoutError. Очковых документов/меток эти события не создали.
- [x] Сверить ожидающие подтверждения с заданиями: 17 pending player-profile предложений для 8 участников; все 8 работ находятся в `waiting_confirmation`, orphaned заявки не обнаружены.
- [x] Расшифровать накопленные profile-job статусы в контрольном срезе 13:26 UTC: 548 `failed` = 222 `not_found`, 190 `REFRESH_ABORTED`, 120 `source_unavailable`, 12 timeout и 2 pagination; 968 `retry`, из них 925 `SEARCH_COMPLETED_NO_MATCH` с плановым шестичасовым повтором; 654 queued. 18 pending заявок относятся к 9 игрокам, у всех 9 есть `waiting_confirmation` job и нет осиротевших заявок. Массовый сброс `failed`/`retry` не выполнялся: сначала надо проверить возраст и причину каждой категории.
- [x] Различать отсутствие PBP-вкладки и ошибку загрузки сета: если Flashscore-страница не публикует set links, архивировать фактически полученный summary HTML с `fetch_status=point_history_tab_missing`, не выдумывать маршрут set-1 и сохранять диагностическую причину для повтора. Есть regression-тест на немодифицированную страницу.
- [x] Различать происхождение PBP по сохранённому Fonbet snapshot: документ считается live-observed только если ближайший snapshot, уже доступный к `retrieved_at`, имеет статус `live` и не старше 90 секунд; завершённое событие исключается. `available_at` остаётся временем получения документа, история не backdate-ится. Добавлены unit-проверки свежести, статуса и временного порядка.
- [x] Выкатить Research/`flashscore-worker`, проверить health и выполнить повторный аудит 13:33 UTC: [`tennis_label_audit_2026-10-21_after_live_provenance_fix.json`](docs/research/tennis_label_audit_2026-10-21_after_live_provenance_fix.json), manifest [`tennis_training_manifest_015d7f19e00a.json`](docs/research/tennis_training_manifest_015d7f19e00a.json). P/G/S — 29 матчей, initial train 9, validation 3/0/3/1, holdout 6; match — 2,289, train 958, validation 160/160/159/160, holdout 400. Temporal precedence 0/4 fold на каждой цели, feature PIT 0/3,848, observed PBP 0; gate остаётся `blocked`.
- [ ] Получить реальные наблюдавшиеся PBP и PIT-доступность спортивных признаков. В актуальном аудите observed PBP = 0, а odds-free feature PIT = 0/3,848; статус обучения остаётся `blocked`.
- [ ] Отдельно разобрать накопленные `failed`/`retry` player jobs по кодам причин и проверить восстановление активного Chromium child после принудительной остановки; текущие интеграционные проверки подтверждают lease fencing, но это не закрывает реальный kill/restart acceptance.

Подробные counts, exclusions и очередной preflight: [`tennis_training_preflight_2026-10-21_after_live_pbp_check.md`](docs/research/tennis_training_preflight_2026-10-21_after_live_pbp_check.md). Holdout quality остаётся закрытым. Тестовые реализации LR/GB/GRU на fixture данных не равны обучению кандидатов.

### Уточнение PIT-контракта — 2026-10-21 14:05 UTC

- [x] Закрыть незафиксированную поверхность: live inference использует court surface только с отдельным `surface_available_at <= cutoff`; без timestamp/future timestamp кодирует `unknown`. Изменения вектора входят в forecast fingerprint; свежий `neural` healthy.
- [x] Обновить validator обеих сторон контракта: пропущенный timestamp остаётся ошибкой, если не указан точный contract-default; тесты проверяют отсутствие выдуманного source timestamp.
- [x] Сверить audit с inference: только point-in-time metric snapshots, действующая на срезе подтверждённая идентичность, реальные serve-знаменатели из snapshot или exact-name serve log за 365 дней, явные priors и отдельные resolution counters. Для priors `source_available_at` остаётся null, а причинность указывается отдельным `contract_defaulted_sources`.
- [x] Сохранить свежий отчёт [`tennis_label_audit_2026-10-21_feature_provenance.json`](docs/research/tennis_label_audit_2026-10-21_feature_provenance.json), manifest [`tennis_training_manifest_95779924731d.json`](docs/research/tennis_training_manifest_95779924731d.json) и preflight [`tennis_training_preflight_2026-10-21_feature_provenance.md`](docs/research/tennis_training_preflight_2026-10-21_feature_provenance.md). PIT-правила проверены на 3,977 примерах; все прошли только потому, что defaults причинны и явно зафиксированы. Player-metric inputs полностью наблюдались лишь в 63 строках; serve и surface имеют ноль наблюдённых значений.
- [x] Пересчитать допускаемые метки: 31 P/G/S матч, 1,009/596/67 игровых примеров, observed PBP 0; общий manifest содержит 2,013 матчей, из них 6 игровых целей в holdout. Match имеет 2,305 меток и проходит размер окон, но временной порядок train/validation нарушен во всех folds; 484 события не имеют доказанного времени результата, 420 валидных исходов исключены.
- [x] Проверить изменения: odds-free forecast — 15 tests passed; PIT/manifest audit — 13 passed. Neural image rebuilt/restarted; healthcheck healthy и SHA-256 `/app/app/tennis_forecast.py` совпадает с исходником. Рабочее обучение не запускалось.
- [ ] Продолжать сбор и исправить хронологию/историю до покрытия 500 initial-train, 100 на каждом validation, 200 holdout по каждой цели. Пока P/G/S недостаточны, observed PBP отсутствует, match train labels позднее validation forecasts, admission остаётся `blocked`; LR/GB/GRU на рабочей выборке и SHADOW не запускать.

### Профильная очередь: приоритет актуальных матчей — 2026-10-21

- [x] Проверить очередь на актуальных событиях: в срезе 14:08 UTC было 42 допустимых live/+6h матча и 84 участника; задания в начале FIFO относились к событиям вне окна, backlog ожидал более 2,5 часа.
- [x] Изменить порядок выдачи: live-участники → prematch до +6 часов → старый архивный backlog; если все слоты заняты неактуальными матчами, отозвать их token/lease с fencing вместо ожидания полного таймаута.
- [x] Добавить SQL-регрессии на порядок приоритетов и отбор только активных, неактуальных попыток. Узкий набор — 7 тестов passed.
- [x] Пересобрать и выкатить `research`/`flashscore-worker` в 14:15 UTC; оба healthcheck healthy. В 14:16 после перезапуска активная профильная задача была участником текущего live-события, подготовленные участники выросли с 4 до 7; архивные записи остались в очереди.
- [ ] Через 30 минут переснять очередь: сравнить число подготовленных актуальных участников, задержку старейшего live/PBP и продвижение архивного хвоста.
- [x] Пересчитать point-in-time preflight после выкладки: audit [`tennis_label_audit_2026-10-21_queue_priority.json`](docs/research/tennis_label_audit_2026-10-21_queue_priority.json), manifest [`tennis_training_manifest_b28f48ca9825.json`](docs/research/tennis_training_manifest_b28f48ca9825.json), provenance [`tennis_training_examples_f9bb25565cf2.jsonl`](docs/research/tennis_training_examples_f9bb25565cf2.jsonl). 31 матч на P/G/S (22 в общей хронологии; train 9, validation 3/1/2/1, holdout 6), match 2,307 меток, но все temporal folds провалены; gate `blocked`.
- [x] Проследить источник времени Fonbet-результата: контракт допускает отсутствие `available_at`, matcher при его отсутствии записывает `utc_now()`. Исторические результаты, впервые принятые сегодня, не backdate-ить. Актуальный audit исключил 420 таких меток из manifest; ещё 483 результата остаются без доказанного времени.
- [x] Уточнить версию признаков: inference и audit используют `tennis_odds_free_v1`; отдельного текущего `v2` writer не найден. Версия и ordered feature list должны войти в manifest кандидата перед обучением.
- [x] Runtime smoke после приоритетной выкладки, 14:23 UTC: Research и `flashscore-worker` healthy; 38 подходящих матчей, подготовлено 9 игроков против 4 до выкладки; оба активных player-refresh задания относятся к текущему окну, очередь и PBP продолжают двигаться, сетевой лимит 1 из 2. Исходник в обоих контейнерах совпадает с рабочим SHA-256.
- [x] После проверки маршрута dispatch исправить привязку FastAPI-декоратора; добавить regression, что endpoint зарегистрирован ровно один раз. Повторная финальная выкладка 14:24 UTC; 8 тестов passed; 14:25 — оба контейнера healthy, route в runtime указывает на `dispatch_tennis_player_refresh_jobs`, SHA-256 Research/worker совпадает с исходником.
- [x] При вытеснении архивного refresh ради актуального события синхронизировать статус профиля: `stale` при сохранённых метриках, иначе `pending`, с явной причиной `REFRESH_PREEMPTED_FOR_CURRENT_EVENT`; это не меняет метрики и не снимает fencing. Изолированный Research-image regression-набор прошёл: 4 tests.
- [x] Добавить сквозное ограничение браузеров: default executor до двух потоков плюс два взаимно-исключающих OS file-lock слота на общем Docker volume, который виден Research и worker и покрывает дочерние scraper процессы. Unit-тесты блокировки и автоматического освобождения слота после kill прошли вместе с browser pool regressions (13 passed).
- [x] После rollout проверить cross-container lock: worker занял первый слот, Research получил второй, третий Chromium в Research получил `FLASHSCORE_BROWSER_SLOT_TIMEOUT`; после закрытия контекста второй слот освободился. Оба сервиса смонтировали один volume; контейнеры healthy.
- [x] Исправить обход общего browser slot: профильная пагинация в `TennisFlashscoreClient._fetch_rendered_profile` запускала отдельный async Playwright, поэтому browser cap ограничивал только sync PBP pool. Добавлен общий async slot context manager, использующий те же kernel file locks. Unit suite `test_browser_pool.py` + `test_tennis_flashscore.py`: 58 passed.
- [x] Пересобрать Research image с `lxml` и общим async/sync slot lock; назначить одинаковый image тегам Research и flashscore-worker, пересоздать оба сервиса. После выкладки оба heartbeat свежие; queue API отвечает, `overdue_pbp=0`, worker healthy. При занятых двух Chromium roots попытка async slot из Research истекла с `FLASHSCORE_BROWSER_SLOT_TIMEOUT`, как ожидается; память worker в первом runtime-срезе ~930 MiB.
- [x] Сократить Docker build context Research: игнорировать локальные `OnlineData`, `models`, `backups`, `docs/research` и frontend build artifacts; сам `services/frontend` остаётся в общем context, потому что frontend Dockerfile копирует исходники.
- [x] Повторить 30-минутный read-only soak после async-slot rollout. Итог в `docs/research/flashscore_soak_2026-10-21_async-profile-slot.jsonl` и `docs/research/flashscore_resources_2026-10-21_async-profile-slot.jsonl`: 60/60 API ответов HTTP 200; latency p50 0.591s, p95 1.461s, max 5.243s; worker healthy 60/60, cooldown 0; Chromium roots не выше 2; max RSS-счётчик worker 929.8 MiB из 2 GiB, Research 192.7 MiB из 1.5 GiB. Один из 55 Docker stats опросов timeout, без сбоя API/worker. `overdue_pbp` достигал 2 в 16/60 опросах при занятых PBP слотах; задания оставались видимыми в durable queue и далее переоткладывались/обрабатывались. Очередь продолжала меняться. Предыдущий `global-browser-slots` soak не принимается: в нём обнаружен профильный async launcher вне общего lock.
- [x] Повторно построить полный point-in-time label audit и единый manifest потоковым чтением PBP-версий после неудачной memory-heavy попытки 16:02 UTC. Завершённый срез 16:17 UTC: `tennis_label_audit_2026-10-21_streaming.json`, manifest `tennis_training_manifest_2026-10-21_streaming_retry.json`; подробности в `tennis_training_preflight_2026-10-21_streaming.md`. Это закрывает повторный аудит, но не training admission: P/G/S ниже порогов, temporal precedence матчевой цели провален; holdout quality закрыт.
- [P1] [ ] Продолжать live-сбор и причинные snapshots; обучение закрыто до прохождения фиксированных coverage, chronology и PIT порогов.
- [x] Подтвердить fixture-only primitives независимых кандидатов в изолированном neural image: `tests/neural/test_independent_tennis_models.py` — 7 passed. Результаты не являются оценкой качества и не открывают training gate.
- [x] Исправить queue metric `overdue_pbp`: активное `running` задание со старым `next_run_at` ошибочно считалось просроченным. Метрика теперь считает только runnable `queued/retry` для live-события с подтверждённым источником. Regression — 1 passed; Backend пересобран, healthy после rollout; API smoke 2 раза вернул overdue=0 с latency 0.39–0.49s.
- [x] Проверить остаточные player queue состояния на 14:57 UTC: 52 pending Flashscore-заявки/34 субъекта и 32 waiting-confirmation job; orphan jobs = 0. DB содержит 1,000 retry (963 `SEARCH_COMPLETED_NO_MATCH`, повторы запланированы), 538 старых failed (218 `not_found`, 187 `REFRESH_ABORTED`, 119 `source_unavailable`), 572 queued; массового сброса нет. В БД 630 PBP-документов для 59 source matches. Этот backlog остаётся видимым и не трактуется как PBP/метки.
- [x] Финальный 30-минутный read-only soak после async profile-slot rollout выполнен в `docs/research/flashscore_soak_2026-10-21_async-profile-slot.jsonl` и `docs/research/flashscore_resources_2026-10-21_async-profile-slot.jsonl`; принятие основано на новом прогоне, не на более раннем `global-browser-slots`.

### Исправление решений админских подтверждений — 2026-10-21

- [x] Разрешить изменить последнее решение `approved ↔ rejected` для игрока или матча с текущей версией заявки; старые/замещённые решения и сущности с новым pending-предложением защищены `409`.
- [x] Сохранять исправление append-only в журнале решений и новой temporal версии identity/match link; исправленный player approval на reject снимает текущий source ID и ставит безопасный повторный поиск.
- [x] Добавить в админку кнопки «Отменить подтверждение» и «Отменить отказ» в истории решений. Backend/Frontend пересобраны и healthy; тесты `test_admin_confirmation_correction_policy.py`, auth и profile recovery — 11 passed.
- [x] Исправить последний ошибочный Gala approval: «Иванович И» → Flashscore `Ivanovic Gala` отклонён и поставлен на повторный поиск; более ранний approval `Ivanovic Gala` для «Иванович Г» оставлен без изменений. Журнал решений сохранён, версия заявки стала 3.

### Дополнительная проверка допуска и Flashscore soak — 2026-10-21

- [x] Повторить 30-минутный runtime/resource soak после 120-секундного deadline инкрементального обновления профилей: 60/60 ответов очереди HTTP 200, worker healthy 60/60, cooldown 0; API p50/p95/max 0.558/0.977/2.554 с; браузерных процессов максимум 2; worker memory максимум 1,090.6 MiB / 2 GiB. Артефакты в [`flashscore_performance_2026-10-21.md`](docs/research/flashscore_performance_2026-10-21.md).
- [P1] [ ] Снизить live PBP backlog. Повторный 30-минутный PBP-filter soak: [`flashscore_soak_2026-10-21_pbp-priority.jsonl`](docs/research/flashscore_soak_2026-10-21_pbp-priority.jsonl), 61/61 HTTP 200, worker healthy 61/61, API p50/p95/max 0.127/0.375/1.44 с; `overdue_pbp` ненулевой в 19/61 срезах (максимум 1). В PBP-очереди 10 waiting_players в начале и 8 в конце; максимальный wait 8,292 с (~2 ч 18 мин), причина `PLAYER_SOURCE_IDS_MISSING`; eligible-событий 11→9, подготовленных игроков 6→5. Это подтверждает здоровье API/исполнителя, но критерий backlog не пройден: главная задержка — неразрешённые профили, а не занятые сетевые слоты.
- [x] Подтвердить на контрольном наборе, что реальный Fonbet snapshot с полным точным финальным счётом может дать более раннее время известности, чем сегодняшняя дозапись официальных метаданных: 19/20 матчей имели точное совпадение; совпавшие снимки предшествовали metadata time. Review — [`tennis_result_observation_review_2026-10-21_sample20.json`](docs/research/tennis_result_observation_review_2026-10-21_sample20.json); индексированная проверка новых regex-фильтров на тех же 19 матчах нашла 19/19 по 62 подходящим строкам. Unit suite label audit/source-time — 17 passed.
- [P1] [x] Оптимизировать доказательство времени результата для существующего admission-аудита: per-event точный score lookup с ограничением `first_seen_at`, чтением по `(event_id, observed_at)` и консервативным временем последнего совпадения. `test_tennis_label_audit.py` — 17 passed; control EXPLAIN — 20 индексных lookup за 5.3 ms. Это не заменяет обязательный полный отчёт.
- [P0] [ ] Завершить свежий полный admission report с точными snapshot times. Per-event exact-score lookup сначала читает последний snapshot по индивидуальному `first_seen_at`, а исторический поиск выполняется только при несовпадении; `test_tennis_label_audit.py` — 18 passed, `py_compile` и `git diff --check` прошли. Контрольный full report ещё не получен после оптимизации: ограниченный запуск на актуальных данных не завершился за 300 секунд из-за холодного чтения исторических snapshot-страниц; собственный backend-запрос был отменён. Последний полный аудит 16:17 UTC остаётся единственным завершённым основанием, gate закрыт. Не менять legacy timestamps и не запускать обучение до нового полного отчёта.

### Точность Flashscore identity matching — 2026-10-21

- [x] Расширить безопасное автосопоставление односложного инициала и полной фамилии распространёнными вариантами русско-латинской транслитерации (`ч/ch/c`, `ж/zh/j`, `ш/sh/s`, `ц/ts/c`, `х/kh/h` и др.); сложные имена остаются ручными. `tests/research/test_tennis_flashscore.py` — 53 passed.
- [x] Схлопывать повторные карточные ссылки одного live-матча по стабильному Flashscore `mid`, чтобы дубликат URL не создавал лишнюю заявку подтверждения; отдельные матчи той же пары по-прежнему ждут выбора.
- [x] Сопоставлять ID игроков только по окончанию двух отдельных Flashscore participant-slug сегментов; это исключает ложное неоднозначное предложение из-за вхождения ID в другой slug/query. Проверка URL `Adkar Vaishnavi — Shkutova Anna` и регрессия случайного подстрочного совпадения: `test_tennis_flashscore.py` — 54 passed; вместе с point-history retrieval и sport-engine regressions — 67 passed. UI подтверждений показывает имена участников из URL вместо непрозрачного `Матч Flashscore <id>`.
- [ ] Принять новый 30-минутный runtime soak после точного slug-сопоставления и healthcheck rollout. Первый прогон нашёл ложный Docker `healthy`: `/tmp/healthy` проверялся только на существование, хотя его возраст превысил 3 минуты и DB heartbeat остановился; в тот же период host I/O wait достигал 57–78%. Healthcheck заменён на проверку возраста heartbeat ≤45 секунд; требуется чистый повторный прогон и подтверждение, что executor восстанавливается после transient I/O stall.
  - Проверочный прогон завершён 18:00–18:31 UTC, но gate оставлен открытым: 57/59 запросов успешны, два 10-секундных timeout; у всех успешных срезов worker healthy и `overdue_pbp=0`. p50/p95/max latency 0.606/2.071/9.562 s. За отдельные 11 минут ресурсов ошибок не было, максимум два Chromium root-процесса, worker 510.7 MiB. Артефакты: `docs/research/flashscore_soak_2026-10-21_post-stale-health.jsonl`, `docs/research/flashscore_resources_2026-10-21_post-stale-health-labels.jsonl`. Не считать это подтверждением полного восстановления при I/O stall.
- [x] Ускорить очередь Flashscore после двух редких API timeout: убрать чтение всех исторических player/PBP/match jobs, ограничить выборки актуальными событиями и последними 24 часами для завершённых карточек; выбирать последнюю связь на внешний матч. Пересобранный backend прошёл 10 последовательных запросов `limit=50`: 10/10 HTTP 200, p50 0.057 s, максимум 0.349 s; новая API regression-проверка bounded projection прошла (1 passed); runtime worker healthy, `overdue_pbp=0`. Это smoke; повторный длительный soak после оптимизации требуется для закрытия предыдущего пункта.
- [x] Убрать из админских подтверждений однозначные одиночные связи матча: reconciliation принимает только единственного pending кандидата с корректным `mid` и двумя отдельными slug-ID уже подтверждённых Flashscore-профилей. Сложные/неоднозначные кандидаты остаются ручными. Два реальных старых предложения (Адкар—Шкутова, Ла Серна—Торрес) прошли проверку, записаны как системные approvals, PBP задания возобновлены; API обоих событий отвечает 200, документов пока 0. `test_confirmation_recovery.py` — 5 passed. Reconciliation включён в выкаченный Research/worker.
- [x] Исправить транслитерацию `й`: защищать букву при NFKD-нормализации и сравнивать только соответствующие позиции как `i/y/j`. Проверить автоматические соответствия `Чайковская В` ↔ `Caykovskaya/Chaykovskaya Violetta`, `Нишимото Ч` ↔ `Nishimoto Catherine`, а также `ж/ш/ц` и близкий отрицательный пример; подтверждать только полную фамилию и совпавший инициал. Unit/regression tests должны пройти до rollout Research/Flashscore worker.
