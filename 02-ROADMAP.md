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
- [ ] Проверить полную пагинацию и сопоставление на профилях с успешной внешней синхронизацией; подтвердить идемпотентность повторного импорта.
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
- [ ] Повторно проверить полный обход доступной истории на десяти профилях после ужесточения терминального доказательства: старая проверка v4 нашла непрерывные страницы и пустой конец, но не перепроверяла HTTP 200 + `0` вторым запросом; повтор Sinner v4 остаётся доказательством идемпотентности только для предыдущего протокола.
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
- [ ] Отдельно проверить, что позднее исправление назначения, документа и статуса задания не меняет чтение с уже сохранённым `resolved_as_of`.
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
- [ ] Сверить парсер минимум на 20 архивных PBP матчах с обычными геймами, deuce, advantage, tie-break, corrections и gaps; на текущий момент вручную подтверждён один архивный матч.
- [ ] Провести конкурентный lease-fencing/worker-restart тест и проверить polling/финализацию без bankroll операций. Один завершённый матч прошёл runtime-сбор до `finalized`, но это не заменяет проверку потери lease и рестарта очереди.
- [x] Реализовать единый хешированный manifest целых матчей: последние 20% holdout; в development первые 60% начальное обучение и четыре expanding validation окна в оставшихся 40%; целевые метки маскируются внутри общего разбиения.
- [x] Пересчитать аудит на рабочей БД, сохранить единый manifest и покрытие holdout без метрик качества; качество holdout остаётся закрытым до выбора кандидата.
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
- [ ] Доказать тестом/manifestом, что изменение или удаление odds не меняет feature vector и прогноз.

### Phase 43 — Правила счёта и сценарный движок

- [ ] Проверить и покрыть тестами обычный гейм, deuce/advantage, смену подачи между геймами/сетами, best-of-3/5 и известные правила тай-брейка.
- [ ] Не выводить точную очковую последовательность из score delta между опросами; неоднозначный переход исключать из point targets.
- [ ] Считать допустимые ветви на 3 очка и 2 гейма, объединять конечные состояния, сохранять массу и поглощающее состояние матча.
- [ ] Неизвестный формат/правила/счёт дают явный unavailable reason, а не нулевые вероятности.

### Phase 44 — Кандидаты и инференс

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
- [ ] Проверить реальное покрытие более 500 матчей, границы шестичасового окна, два слота, 30-минутный runtime и отсутствие влияния на общий worker/API.
- [ ] Сравнить длительность, число запросов и запусков Chromium с зафиксированным baseline; сохранить отчёт и проверить возврат к последовательной сети.
- [x] Подключить серверную фильтрацию до пагинации, отображение приоритета/следующей попытки, сводные статусы и просроченный PBP; пересобрать backend/frontend, проверить API-фильтр и health контейнеров.
- [ ] Проверить сохранение последнего списка при ошибке API и UI-сценарии всех статусов/перехода по карточке.

**Acceptance:** миграция 026 применена; durable очередь автоматически охватывает все подходящие события; две сетевые операции работают параллельно с общей частотой не чаще одной новой операции каждые две секунды; Flashscore очередь и состояние executor видны во вкладке scheduler; основной betting/settlement pipeline не задерживается.

### Phase 50 — Исправление Flashscore после часового прогона

- [x] Ограничить поиск профиля тремя вариациями латинской записи; timeout, пропущенный ответ, HTTP-блокировку и ошибку разбора не считать подтверждённым `not_found`.
- [x] Сохранять `next_run_at` для профилей; сетевые ошибки повторять с backoff, подтверждённый пустой поиск — через шесть часов, неоднозначность оставлять на ручное подтверждение.
- [x] Добавить durable очередь поиска внешнего live-матча и кэш общей live-страницы на 30 секунд; совпадение подтверждать только по двум подтверждённым ID игроков.
- [x] Перевести PBP без профилей/связи в `waiting_players`/`waiting_match`; будить PBP сразу после подтверждения связи и не учитывать ожидание как просрочку.
- [x] Вывести поиск матча, причины ожидания и повтора, а также счётчики документов и связей за последний час во вкладке scheduler.
- [x] Исправить фактический browser-search путь: ждать гидратацию Flashscore, нажимать видимую кнопку и выбирать видимое поле `Type your search here`; live smoke query `Arantxa Rus` вернул ровно профиль `Rus Arantxa` (`84GyAaXB`).
- [x] Применить миграцию 028, развернуть Research/`flashscore-worker` и актуальные backend/frontend images; проверить health, очередь и event API. Backend API suite проходит.
- [ ] Отдельно проверить lease fencing и неизменность исторического `as_of` на реальной версионированной PBP-истории.
- [ ] На живом матче получить новый PBP-документ, подтверждённую связь и непустой point-history; провести часовой замер доли подготовленных участников и задержки PBP. На текущем срезе: 11 подходящих матчей, 3 подготовленных игрока, 0 документов и связей за час; PBP ждёт неразрешённые профили соперников.

**Acceptance:** нельзя закрыть этап по одному успешному build. Требуются развернутые контейнеры, повторяющиеся сетевые ошибки с планом повтора, реальный PBP-документ и подтверждённая связь; research не создаёт ставок или ledger entries.

### Phase 51 — Административная очередь подтверждений

- [x] Добавить универсальные сохранённые заявки и append-only журнал решений для профилей игроков и связей матчей; идемпотентно перенести ожидающие legacy-кандидаты миграцией.
- [x] Добавить проверку версии заявки и атомарные решения «Да»/«Нет»: подтверждение запускает импорт метрик/PBP, отказ сохраняет отвергнутую пару и повторно ставит поиск через durable очередь.
- [x] Исключать отвергнутые кандидаты из следующих поисков; показывать явное состояние отсутствия альтернативных данных и не использовать неподтверждённые метрики/матчи.
- [x] Автоматически подтверждать единственное точное полное совпадение имени после безопасной нормализации; неоднозначные, похожие и конфликтующие варианты оставлять в очереди ручной проверки.
- [x] Добавить серверную сессию админки с HttpOnly cookie, защитить API решений и legacy route ручной связи; удалить парольный hash из frontend.
- [x] Добавить в админку вкладку «Подтверждения» с количеством ожидающих решений, фильтрами типов/статусов, контекстом источника, двумя кнопками, обновлением раз в пять секунд и журналом решений.
- [ ] Выполнить миграцию в штатном контейнере, настроить серверные `ADMIN_*` секреты, собрать backend/research/frontend и проверить оба типа заявок на контрольных кандидатах.
- [ ] Проверить два конкурентных решения, повторное предложение rejected пары, восстановление очереди после рестарта, `as_of` до/после решения и отсутствие ставок/проводок.

**Acceptance:** оператор принимает или отклоняет обе категории связи из админки; каждое решение переживает перезапуск и имеет журнал; отклонённая пара не предлагается снова; отсутствие альтернатив видно в текущей очереди; неподготовленная серверная авторизация закрывает все административные решения.

### Phase 52 — Допуск к обучению независимого прогноза

- [x] Исправить аудит окон: требуемый минимум считать только по уникальным матчам с меткой соответствующей цели; отдельно показывать все матчи-кандидаты.
- [x] Исключить `scheduled_start_at` и `finished_at` как замену времени доступности результата; недатированные исходы не попадают в manifest.
- [x] Сохранять JSONL provenance меток со ссылками на Fonbet-снимок, PBP-документ и версию разбора, позицию и подтверждённую связь сторон.
- [x] Добавить очередь исторического PBP backfill для завершённых одиночных числовых Fonbet-событий вне окна прогнозов; live остаётся выше архива в очереди и в глобальном регуляторе.
- [x] Зафиксировать закрытый `tennis_odds_free_v1` feature allowlist; вход игнорирует odds/market-поля и включает спортивную историю игроков и счёт.
- [x] Добавить fail-closed PIT-проверку timestamps по группам источников; неполные/будущие времена отклоняются, пока данные не сертифицированы.
- [x] При принятии официального Fonbet-результата сохранять `available_at`; повтор неизменённого результата сохраняет первое время, исправление получает время новой доступности. Старые строки не backfill-ятся по расписанию или `finished_at`.
- [x] Применить обновлённый Research/`flashscore-worker` и Backend; все три контейнера healthy. Backfill обнаружил 2,206 завершённых одиночных событий, 2,205 ждут профилей, восемь заданий игроков поставлены в очередь, один матч уже покрыт PBP.
- [ ] Проверить восстановление очереди после перезапуска и измерить, что архивные запросы не задерживают live при общем сетевом лимите.
- [ ] Собрать и подтвердить PBP для сотен уникальных матчей; ежедневно публиковать воронку профиля → матч → связь → документ → метки четырёх целей.
- [x] Зафиксировать закрытый список odds-free входов и добавить fail-closed проверку времён доступности по группам источников; текущие реальные данные пока не сертифицированы. Unit-тесты движка и сценариев на трёх очках/двух геймах проходят.
- [ ] Повторно сформировать manifest и пройти для каждой цели train ≥500, все четыре validation ≥100, holdout ≥200, оба класса и point-in-time проверку. Порог не снижать.
- [ ] Только после допуска обучить LR/GB/GRU на одном manifest, оценить на development, внести документированные исправления и открыть holdout один раз.

**Acceptance:** отчёт допуска считает только размеченные матчи, содержит provenance и объясняет каждый блокирующий гейт; в рабочее обучение можно перейти лишь после прохождения всех четырёх целевых порогов и point-in-time сертификации.

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
