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
- [x] Проверить конкурентные prepare-запросы, recovery worker и причины ожидания через API. 4 независимых процесса создают по одной durable job без дубликатов; worker после restart восстановил реальный archive-import checkpoint (16 страниц сохранены, попытка 36→37), профильные lease regressions — 2 passed. API показывает старые probe jobs как `queued` с attempts/progress/error; на снимке 05:39 оба слота заняты действующими `new_event`/`event_refresh` lease. Сквозные доказательства: [`tennis_player_dispatch_recovery_api_2026-10-22_0539.json`](docs/research/tennis_player_dispatch_recovery_api_2026-10-22_0539.json), [`flashscore_worker_restart_2026-10-22_active-import-checkpoints.json`](docs/research/flashscore_worker_restart_2026-10-22_active-import-checkpoints.json). Очередь остаётся большой; её полнота и throughput отдельно открыты.

### Phase 39.1 — Надёжная история и point-by-point

- [x] Добавить fencing token и heartbeat для lease refresh-профилей; устаревшая попытка не может изменить профиль или закрыть актуальное задание.
- [x] Перейти к подтверждению прогресса страниц по новым ID матчей и сохранять время получения каждой страницы; не считать отсутствующий feed/пагинацию успешным окончанием.
- [x] Ввести source watermark: после полной первой загрузки обновлять с перекрытием 30 дней, полный обход запускать отдельно раз в 30 дней.
- [x] Добавить versioned контракты и таблицы исходных PBP документов, распознанных версий, ссылок на Fonbet событие и durable live refresh jobs.
- [x] Добавить offline HTML парсер для обычных point states, deuce/advantage, serving marker, terminal GAME и числового tie-break; неразрешённые переходы маркировать как ambiguous/gap.
- [x] Добавить Research-сбор матчевых вкладок сетов и архивирование HTML до записи версии; повтор неизменного документа подавляется по хешу.
- [x] Добавить ручное подтверждение неоднозначного Flashscore↔Fonbet матча, `as_of` API и краткий статус PBP в карточке матча.
- [x] Добавить append-only связи participant→Flashscore ID со статусами `candidate`/`confirmed`/`revoked`; автоматический поиск имени оставляет профиль кандидатом и не выдаёт его метрики прогнозу без подтверждения.
- [x] Проверить ручное разрешение реальных профилей и временное чтение после перепривязки. Два реальных профиля (Yidi Yang и Chengyiyi Yuan) подтверждены через protected resolve API с WTA/ITF/H2H source evidence и `available_at`; runtime read показывает 0 PBP до identity и после импорта — 9 версий/242 событий. PostgreSQL integration test [`test_tennis_player_identity_as_of_integration.py`](tests/api/test_tennis_player_identity_as_of_integration.py) проходит A→B rebind: до A-link данных нет, в окне A читаются только A facts, после revoke до B snapshot данных нет, после B-link читаются только B facts (1 passed). Migration 032 deployed; Neural healthy, четыре production source hashes совпадают с checkout: [`tennis_neural_asof_rollout_2026-10-22_1314.json`](docs/research/tennis_neural_asof_rollout_2026-10-22_1314.json), SHA-256 `50b73d8b5c764868bdc934cdbabb86ad086629f805b009a7c8e15bfa2f15c2c9`. Профильные refresh jobs в указанном live-сценарии ещё были queued; это не свидетельство наличия metrics или результата матча.
- [x] Перевести полный Flashscore import route на сохраняемую таблицу `flashscore_import_jobs`; запрос возвращает `job_id`, прогресс сохраняется по профилям, а незавершённое задание подбирается после перезапуска Research.
- [x] Добавить общий сериализующий advisory lock для профиля и PBP сбора с минимальной паузой источника из конфигурации.
- [x] Разделить retrieval, parse и availability timestamps PBP; legacy строки без подтверждённого времени остаются `unknown_legacy` и исключаются из исторического `as_of`.
- [x] При чтении `as_of` сначала выбирать актуальную версию назначения Flashscore-матча, затем применять статус; выборка версий не должна возвращать не связанные с текущей связью источники.
- [x] Сохранять номер сета в версии документа, в том числе при пустом исправленном разборе, чтобы оно замещало прежние события этого сета.
- [x] На непустой истории проверить стабильный `resolved_as_of` и непересекающуюся пагинацию: обе страницы с `as_of=2026-10-20T15:07:44.258741+00:00` вернули `total=224` и одинаковую временную границу.
- [x] Интеграционно проверить фиксированный `as_of` на PostgreSQL: после поздней версии, отмены связи и перепривязки одного source match A→B старые ответы остались побайтно равны; прежнее событие после reassign вернуло 0 строк, новое — историческую версию, затем после отмены связь стала недоступной. Изолированный runtime integration вызов прошёл; тестовые записи удаляются в `finally` (`tests/api/test_tennis_point_history_as_of_integration.py`).
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
- [x] Выполнить read-only source-level сверку сохранённого HTML для 50 последних уникальных подтверждённых Fonbet-связей: [`tennis_pbp_source_review_2026-10-22_latest.json`](docs/research/tennis_pbp_source_review_2026-10-22_latest.json) и полный разбор [`tennis_pbp_source_review_2026-10-22_latest_details.json`](docs/research/tennis_pbp_source_review_2026-10-22_latest_details.json). В 23 архивах распознаны 180 геймов и 940 состояний счёта; учтены 154 deuce/advantage и 14 tie-break очков; расхождений состояния, известных маркеров подачи и tie-break winner/server — 0. В 27/50 нет распознаваемых PBP-строк, 28 архивов имеют пробелы, один PBP документ неполон, а у 6/23 PBP матчей не подтверждена ориентация сторон. Эти пробелы сохраняются как unknown; полная ручная сверка исправлений и live E2E не выполнены.
- [x] Исправить отсутствие результата завершённого тай-брейка: parser v3 сохраняет терминальный исход сета отдельно от очка; label audit включает его в game/set targets. Append-only replay создал 87 версий по сохранённым архивам, без отсутствующих документов и ошибок; старые версии не перезаписаны, `available_at` новых версий — время replay.
- [x] Проверить PostgreSQL lease fencing: истёкшая попытка не завершает задание, после переназначения токена старый исполнитель не меняет состояние, текущий владелец может завершить задание (`tests/research/test_tennis_refresh_lease_integration.py`). Тест изолирован UUID участником и удаляет свои записи.
- [x] Отдельно провести остановку/перезапуск реального worker во время активного archive import и доказать восстановление с непустым checkpoint. После фикса сброса `progress_json.page_checkpoint` при enqueue/claim перезапуск `flashscore-worker` сохранил 16/16 страниц в пере-захваченной job (attempt 36→37), другая активная job сохранила 219 страниц; worker healthy. PostgreSQL и unit regression tests прошли. Артефакты `docs/research/flashscore_worker_restart_2026-10-22_active-import-checkpoints.json` и `docs/research/flashscore_worker_restart_2026-10-22_nonempty-checkpoint.json`. Это подтверждает restart recovery; transient I/O stall проверяется отдельным runtime gate ниже.
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
- [ ] Довести аудит до покрытия snapshots и категорий ITF/juniors, проверить точность категорий. Последний полный audit [`tennis_label_audit_20261022T102658053244+0000.json`](docs/research/tennis_label_audit_20261022T102658053244+0000.json): 2,707 допустимых событий, P/G/S имеют 44/44/43 матча с метками; observed examples 407/71/4, retrospective 5,516/992/108. Категории и полнота по ITF/juniors остаются неполными, поэтому этот пункт открыт. Read-only census 16:32 UTC в [`tennis_live_pbp_ingestion_2026-10-22_1632.json`](docs/research/tennis_live_pbp_ingestion_2026-10-22_1632.json), SHA-256 `78126b4368e8fac50a0da1346acb0b3cbfc64f98a9a8cf09c860567d89a8ebb5`: в 99,160 Flashscore match rows есть 48,275 ITF и 23 junior записи; 1,582 имеют unknown level/gender. В последнем audit источники категории: 1,932 unknown, 771 Fonbet competition name, 79 Flashscore archive. Распределение сохранено, но проверка точности категорий по исходным документам остаётся открытой.
- [ ] Измерить полноту покрытий, форматов, серверов, статистики подачи с знаменателями, timestamps, дублей, конфликтов и задержек. В свежем audit проверено 4,826 примеров: PIT-certified 0; `event_format` source-time отсутствует у 4,826/4,826, хотя source availability event state есть у всех. Player metric источники observed у 421 среза, defaults у 4,405; serve A/B полностью defaulted (4,826 каждый), surface оставлен explicit unknown (4,826). Result-time: 2,211 official metadata, 277 exact previously observed snapshots, 143 результата без времени. См. свежий audit и execution report в последнем пункте Phase 52; измерение общего покрытия и причин задержек остаётся открытым. Дополнительная source-side проверка 50 последних raw Fonbet S3 payloads (2026-10-22 15:30–15:38 UTC, 3,079 event rows) не нашла явных format-related полей; в DB metadata `best_of`/`best_of_available_at` есть у 0/3,847 теннисных событий. Артефакт [`tennis_format_raw_live_source_audit_2026-10-22_1540.json`](docs/research/tennis_format_raw_live_source_audit_2026-10-22_1540.json), SHA-256 `d2c6313309b53041e2e42b360be2a2b0123b2a0cf7bd54ffece397249ec9462c.
- [x] Зафиксировать список разрешённых признаков, цели, отбор событий и manifest эксперимента; проверить дефекты score parser, тай-брейков, смены подачи и дедупликации. Одобряемый feature allowlist — `ODDS_FREE_FEATURE_NAMES`/`tennis_odds_free_v1`; audit содержит группы источников, четыре цели и exclusions, а manifest — хешированные целые матчи, development/holdout разбиение и четыре chronological folds. Отчёт [`tennis_label_audit_20261022T1540Z.json`](docs/research/tennis_label_audit_20261022T1540Z.json), SHA-256 `658b31c99e013cea001c09ea8ce67bf3c46ee18ba72218e7f50a0db341231660`; manifest [`tennis_training_manifest_af309e711ba0.json`](docs/research/tennis_training_manifest_af309e711ba0.json), SHA-256 `1691988dfb2c174d18520421dc0fa8db7c01cefdc3e00f738ab07590ccaa0ac8`; provenance SHA-256 `a91a4277f424dc9923b1c3fe3106ec895b80463325e4b618ae56be0ded4d6d43`. Regression suites `test_tennis_flashscore.py`, `test_tennis_label_audit.py`, `test_point_history.py` — 97 passed; `test_odds_free_tennis_forecast.py` в целевом odds-free наборе — 54 passed. Обучение и holdout-оценка не запускались.
- [x] Уточнить происхождение каждой метки. `_official_winner` принимает только окончательный результат с совпадающими winner, match score, set scores и declared set winners; walkover/retirement, незавершённые и противоречивые результаты исключаются. Свежий audit дал 2,631 разобранных официальных результатов; 143 без source-time исключены из manifest, а 2,488 датированных исходов включены с `result_time_source`. Regression-покрытие правил добавлено в `tests/research/test_tennis_label_audit.py`; полный audit unit file — 27 passed. Отчёт: [`tennis_label_audit_20261022T102658053244+0000.json`](docs/research/tennis_label_audit_20261022T102658053244+0000.json), execution/provenance hashes: [`tennis_admission_execution_2026-10-22_1059.json`](docs/research/tennis_admission_execution_2026-10-22_1059.json).
- [x] Оптимизировать полный PIT label audit: агрегаты документов считаются отдельно, большие наборы PBP-версий читаются потоком вместо одновременной загрузки ORM-строк в память; 13 audit tests проходят. Срез `docs/research/tennis_label_audit_2026-10-21_streaming.json` построил воспроизводимый manifest на 2,060 матчах. Gate остался `blocked`; holdout quality закрыт. Подробности и причины — `docs/research/tennis_training_preflight_2026-10-21_streaming.md`.
- [x] Сохранять отдельный content-addressed архив выбранных исходных Fonbet live tennis event/child rows и sport ancestors, не помещая source payload в Redis или БД. Полный набор collector-тестов прошёл на собранном образе; после rollout collector healthy. Runtime сверка Redis→S3 и повторного SHA-256 совпала: [`tennis_fonbet_source_archive_runtime_2026-10-22_0500.json`](docs/research/tennis_fonbet_source_archive_runtime_2026-10-22_0500.json), artifact SHA-256 `f44c39e792954c0a61b2dc2afc2a56c72ad3a7d311a2dd342fe48a204d087b01`. Захвачено 43 event rows и 11 sport rows; явного `best_of` в них нет, значит формат и PIT доступность не доказаны и аудит/допуск остаются открытыми.
- [x] Проследить источник `event_format` до runtime и обоих fail-closed потребителей: [`tennis_format_provenance_audit_2026-10-22_0505.json`](docs/research/tennis_format_provenance_audit_2026-10-22_0505.json), SHA-256 `414a7d468b7b8555571a262943a9ceb185c40cdb00c96caafceb220c1beae2ad`. В архивных live rows нет ключей формата даже во вложенности; neural и audit требуют явные `best_of` плюс `best_of_available_at`. Flashscore history importer выводит `best_of` из пола/текста турнира и хранит в общей строке матча без отдельной provenance, поэтому это значение не годится как доказательство формата live-события. В полном audit event_format был доступен на момент среза у 0/4,379 примеров.

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
- [ ] Ручно подтвердить или отклонить legacy-сопоставления; не менять append-only факты до проверки каждого источника. Подтверждены две свежие live identity связи (Ян Иди/Yidi Yang и Юань Чэнии/Chengyiyi Yuan) с сохранённым source evidence; это не проверка legacy-сопоставлений.
- [x] Устранить блокировку и голодание очереди Flashscore: миграция 025 добавляет heartbeat заявки; строки без heartbeat 15 секунд удаляются; архив повышается до ранга 5 после 10 секунд ожидания, live остаётся первым. Под ограниченной непрерывной нагрузкой архив получил слот за 14.27 секунды. Davidovich-Fokina: 552/552 одиночных ID, два пустых terminal probe, 17 архивов; replay 519 результатов добавил 0 строк.
- [x] Независимая durable PBP-задача по `source_match_id` до Fonbet-link: таблица и миграция `033_source_point_jobs`; source-only архивирование сохраняет `fonbet_event_id=NULL` и `retrospective=true`. PostgreSQL integration проверяет истёкший lease/reclaim новым fencing token, а также неоднозначный live-кандидат → две source-keyed задачи и manual-confirmation requests без Fonbet-link; Flashscore discovery в smoke замокан, внешние запросы не выполнялись. Всего 64 Flashscore/lease теста прошли; Research и Flashscore worker выкатаны и healthy. Артефакт и hash: [`tennis_source_point_jobs_2026-10-22.json`](docs/research/tennis_source_point_jobs_2026-10-22.json), SHA-256 `b9f388a85513f1708729aa3dbbe210d59908ad4721c520c9418c095e2297f339`. Ручная source-DOM сверка 20 реальных PBP-матчей: [`tennis_pbp_manual_review_2026-10-22_0816.md`](docs/research/tennis_pbp_manual_review_2026-10-22_0816.md) и [`tennis_pbp_manual_review_2026-10-22_0816.json`](docs/research/tennis_pbp_manual_review_2026-10-22_0816.json).
- [x] Перепроверить полный доступный импорт Antonius: 53 распознанных завершённых матча на трёх архивных feed-документах.
- [x] Проверить архивирование реальных отдельных ответов страниц и повтор Sinner без роста числа внешних матчей; завершённые результаты хранятся в durable job history.
- [x] Проверить несколько профилей разных туров/категорий и многостраничную пагинацию по контрольному набору из десяти профилей; coverage и ограничения `unknown` сохранены в аудите.
- [x] Сохранять неизменяемые версии распознанного результата по содержательному хешу с собственным `available_at`; проверить это на мигрированной БД и исправленном результате.

### Phase 42 — Независимые признаки и point-in-time dataset

- [x] Развести у serve observations дату матча и доступность источника: миграции 030/031 сохраняют legacy timestamp, задают `source_event_at`, `available_at`, provenance, source confidence и retrospective; importer пишет ingestion time, а forecast/audit ограничивают serving history окном 365 дней и cutoff. 23,576 старых строк исправлены без удаления; свежий полный аудит от 2026-10-21 22:45 UTC: `docs/research/tennis_label_audit_2026-10-22_pitcorrected.json`. Это закрывает timestamp leakage по serving logs, но не означает полного наблюдаемого feature coverage.
- [x] Проверить runtime cutoff на рабочей БД: до миграционного `available_at` serving weights отсутствуют, после него появляются только для соответствующих источников; audit даёт те же исторические defaults. Smoke сохранён в `docs/research/tennis_serve_pit_runtime_smoke_2026-10-22_corrected.json`; 33 целевых теста прошли.
- [x] Проверить serving cutoff внутри обновлённого neural контейнера: package включает миграционные поля, веса отсутствуют до migration-time и доступны после него только при 365-day `source_event_at` фильтре; provenance сохраняет volume/freshness/source/confidence/retrospective. Артефакт [`tennis_neural_live_provenance_smoke_2026-10-22.json`](docs/research/tennis_neural_live_provenance_smoke_2026-10-22.json). Container healthy после rollout.
- [x] Проверить новую odds-free логику в live neural process: `tennis_forecast` и версия `db.models.external_tennis` импортированы из runtime overlay image; Healthcheck healthy, модели/registry не менялись. Тег отката `neurobet-neural:pre-oddsfree-20261021` сохранён; полный Dockerfile export отложен из-за зависания на упаковке image layers.
- [x] Построить единый актуальный manifest и byte-hashed example provenance после timestamp correction. Авторитетный audit `docs/research/tennis_label_audit_2026-10-22_pitcorrected.json` (SHA-256 `6b03cfe366de63e44a07436ceecd9fe17a5362261787dd60afe7dab82d1d2640`); manifest `docs/research/tennis_training_manifest_684f34db4eb9.json` (dataset digest `684f34db4eb99f0665224aa0040b86c72ef61748199aab374e30cffd1e9989f1`, 2,392 целых матча); examples `docs/research/tennis_training_examples_225e97848bdf.jsonl` (байтовый SHA-256 `b05e9f58e37a64be83f19698c8bc3088975406c72949bdd8738b125e57c03867`, логический digest отдельно). Holdout quality не открывался. PIT временная причинность подтверждена для 4,219 примеров только с явными defaults/unknown; contract default есть во всех 4,219, полностью source-observed примеров 0. Это подтверждает timestamp gate, но не полноту наблюдаемых признаков. Для целей point/game/set: 34 labelled matches на цель, общая timeline train 19/23/26/27, validation 4/3/1/0, holdout 7; у match численные окна достаточны, но temporal precedence провалена в 4/4 folds. Gate остаётся `blocked`. Актуальная интерпретация и разделение causal PIT/defaults от observed coverage — в `docs/research/tennis_training_preflight_2026-10-22.md`.

- [x] Сохранить версионируемый агрегат Elo/Glicko-2, win-rate по окнам/покрытию/уровню, serving denominator и нагрузку в participant metric snapshots.
- [ ] Добавить официальный рейтинг тура, полноценный парный Glicko по рейтингу оппонента, H2H и скорректированные по силе соперника serving/return features. Свежий source audit [`tennis_rank_h2h_source_audit_2026-10-22.json`](docs/research/tennis_rank_h2h_source_audit_2026-10-22.json): 2,203,912 rank rows относятся к одному tour, но таблица не содержит `available_at`/источника получения; только 81/1,668 текущих player profiles имеют mutable archive canonical ID, а confirmed identity links относятся к Flashscore IDs. Без as-of archive identity и source timestamp эти значения остаются unknown и в odds-free contract не добавляются.
- [x] Удалить синтетические знаменатели статистики подачи; хранить unknown volume, freshness, source confidence, missingness и retrospective flags. `player_serve_rate_snapshot` суммирует фактический `serve_points`; при отсутствии допустимых строк возвращает `sample_points=0`, `sample_weight=0`, `volume_known=false` и явный `missingness`, а provenance включает `source_event_at`, `available_at`, confidence и retrospective. До/после migration cutoff проверено в [`tennis_serve_pit_runtime_smoke_2026-10-22_corrected.json`](docs/research/tennis_serve_pit_runtime_smoke_2026-10-22_corrected.json), SHA-256 `1766b4b03e0aafe6abbb6e9513c484e782a5124d02527114ea53c5b6cf070ab4`; deployed Neural healthy, source hash совпадает с checkout (`5d55462e51b80bd05b37ba1d95d5bee8be3405f7d1d4888dce607b66ba5108ce`). Legacy serve rows остаются retrospective и не считаются исторически наблюдавшимися.
- [x] Для любого train/live/replay среза проверять доступность каждого поля на timestamp самого источника. Audit валидирует source times по player metrics, serve A/B, event state, format и surface для каждого примера; runtime validator отклоняет отсутствующий/будущий `best_of_available_at`. В последнем полном audit проверено 5,519 примеров, `event_format` отсутствует к as-of у всех 5,519, поэтому PIT-certified `0/5,519` и допуск blocked; live collector-normalized metadata также дал 0/36 полей формата. Источники и fail-closed результат: [`tennis_label_audit_20261022T1326Z.json`](docs/research/tennis_label_audit_20261022T1326Z.json), SHA-256 `44bd02cb73ba43673dfd413e9363c54a843913184f31c65560c492f0f1919c9d`; [`tennis_format_live_metadata_2026-10-22_1437.json`](docs/research/tennis_format_live_metadata_2026-10-22_1437.json), SHA-256 `fcbfcff4f4e18c34b26de491c59fb1f26df033d876680c526cf4aa2586818a1c`. Default/unknown не считается источниковым наблюдением и не открывает прогноз без формата.
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
- [x] Фиксировать seeds, feature/checkpoint/calibration versions, CPU fallback и параметры инференса; пройти save/load parity. Контракт экспериментальных checkpoint — schema 2: feature/inference contract, явный `uncalibrated_v1`, seed, training device/fallback, параметры LR/GB/GRU и sequence masks. CPU fixture roundtrip — `tests/neural/test_independent_tennis_models.py`, 7 passed. Реальные калибратор и checkpoint обученной модели не создавались.
- [x] Пока данных на точные очковые цели недостаточно, сообщать покрытие без синтетических меток. Актуальный admission audit публикует происхождение, объёмы и недостающие source-time группы: у point/game/set 41/41/40 матчей с меткой, 252/43/2 source-observed aligned examples; точные состояния берутся только из authoritative confirmed PBP, а примеры привязываются к реально сохранённым Fonbet snapshots. Счётные дельты не превращаются в точечные метки; audit не создаёт синтетические labels. См. [`tennis_label_audit_2026-10-22_0808.json`](docs/research/tennis_label_audit_2026-10-22_0808.json), [`tennis_training_examples_6859aa39a3ab.jsonl`](docs/research/tennis_training_examples_6859aa39a3ab.jsonl) и Phase 43 parser rule.

### Phase 45 — API, UI и strategy replay

- [x] Проверить стабильную пагинацию/сортировку forecasts API и фильтр модели; сохранить прогнозную историю без odds. На deployed backend (`healthy`) один и тот же `offset=0, limit=50` вернул идентичный результат; соседняя страница offset=50 не пересеклась, строки отсортированы newest-first, `model_name=tennis_score_baseline` вернул только эту модель. Все 106 forecast rows не содержали odds/price/market keys. Реальный smoke и хеши ответов: [`tennis_forecast_api_pagination_smoke_2026-10-22.json`](docs/research/tennis_forecast_api_pagination_smoke_2026-10-22.json).
- [x] Показать обе вероятностные траектории, изменения счёта/качества, сценарии и причины пересчёта в карточке матча. На deployed карточке добавлены линии обоих игроков (вторая вероятность — дополнение бинарного исхода), текущий tennis score, timestamp, delta счёта/качества, причины качества и сценарии. Production build + TypeScript прошли, frontend после rollout healthy; dashboard HTML и его JS asset проверены на runtime. Артефакт [`tennis_forecast_card_ui_rollout_2026-10-22.json`](docs/research/tennis_forecast_card_ui_rollout_2026-10-22.json). Автоматический screenshot не снимался.
- [x] Отделить forecast от оценки edge/EV и решения bet-manager; реализовать read-only strategy replay по текущему `tennis.cfg` с вариантом положительного EV-подтверждения. Реализация `tennis_adapter.odds_free_replay`, endpoint `/api/tennis/odds-free/strategy-replay`; fixture/regression набор — 6 passed. Изолированный Backend smoke на текущей БД вернул HTTP 200 для 1 свежего live события, но текущих forecast snapshots за шесть часов нет; непустой сигнал на live данных и rollout в существующий контейнер не проверялись. Артефакт: [`tennis_odds_free_strategy_replay_smoke_2026-10-22.json`](docs/research/tennis_odds_free_strategy_replay_smoke_2026-10-22.json).
- [x] Выкатить backend route и повторно проверить рабочий контур после перезапуска: `/health`, forecast history и strategy replay — HTTP 200; forecast history не содержит odds/price; `Bet`, `LedgerEntry`, `ModelBetJournal` до/после не изменились. Артефакт [`tennis_backend_live_contour_smoke_2026-10-22.json`](docs/research/tennis_backend_live_contour_smoke_2026-10-22.json). После обновления backend свежий deployed replay вернул 4 forecast rows для 10 live событий, но все 4 unavailable и сигналов 0; счётчики bet/ledger/journal до и после совпали. Fixture покрывает генерацию сигнала, непустой live сигнал и полный card/API scope остаются непроверенными: [`tennis_odds_free_strategy_replay_postrollout_2026-10-22.json`](docs/research/tennis_odds_free_strategy_replay_postrollout_2026-10-22.json).
- [x] Ограничить replay свежими live и prematch `[now, now + 6h]`; исключать stale live-флаги старше 90 секунд, завершённые события и doubles. На текущей БД 132 из 139 флагнутых live записей оказались stale и не входят в новый forecast/replay scope. Два режима replay не изменили счётчики `Bet`, `ModelBetJournal`, `LedgerEntry` (delta 0); read-only smoke — тот же артефакт. Nonempty live replay остаётся непроверенным из-за отсутствия текущих forecast snapshots.
- [x] Вывести sync external-prediction collection из ASGI event loop через `asyncio.to_thread`; blocking-coordinator regression прошёл, Research rebuilt/healthy, после rollout `/health` ответил за 19 ms и два player-status API — за 69/135 ms. Первый реальный periodic cycle на новом образе завершился за 6 s (114 scraped, 31 saved, 6 expired); последующий `/health` ответил за 17 ms. Runtime-артефакт [`tennis_research_api_nonblocking_runtime_2026-10-22_0528.json`](docs/research/tennis_research_api_nonblocking_runtime_2026-10-22_0528.json), hash `f895f1c4ed3ac582b82c14e32ada6eaff55e293e1654508895e3e44f3fd09730`. Два ранее созданных durable refresh задания доступны через API и остаются queued; восстановление их выполнения и корневая причина предшествующего HTTP timeout этим не закрыты.

### Phase 46 — Обучение и development оценка

- [x] Сохранить в manifest v2 общие whole-match folds и target-specific `target_train_event_ids`, очищенные по cutoff `target label available_at <= earliest validation forecast`; числа late/unknown labels и effective train membership фиксируются в отчёте. На текущем замороженном provenance match сохраняет 1,115/1,309/1,474/1,698 причинных train матчей и проходит temporal precedence во всех 4 folds; P/G/S имеют 0 причинных train матчей. Пороги и 20% holdout не менялись. Manifest и derived report: `tennis_training_manifest_77fcc1b3a797.json`, `tennis_label_audit_2026-10-22_purged_validation.json`.
- [ ] Зафиксировать manifest: данные, признаки/метки, 4 expanding walk-forward окна, development tuning/calibration blocks, seeds, гиперпараметры и задержка исполнения.
- [x] Оставить последние 20% целых Fonbet матчей заранее закрытым holdout; manifest v2 содержит 2,400 whole-match IDs (1,920 development / 480 sealed holdout), а target-specific train IDs проверены как подмножество train split без пересечения с holdout. Holdout quality metrics не открывались: `tennis_training_manifest_77fcc1b3a797.json`.
- [x] Проверить до обучения минимум по каждой цели: train ≥500 матчей, каждое walk-forward окно ≥100, holdout ≥200, оба исхода и отсутствие нарушений point-in-time; при провале продолжать сбор и baseline без снижения порога. Результат актуального аудита — `blocked`: причинный train 0 во всех folds; point/game/set не проходят пороги 500/100/200 и temporal precedence; match проходит численные validation/holdout размеры, но causal train 0. См. актуальный audit и execution артефакты ниже; сбор данных продолжается, пороги не снижались.
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
- [x] Снять актуальный read-only census PBP durable queue и разделить исторический backlog от текущих live events. На 02:17:49 UTC: 2,824 jobs, из них 2,517 waiting for players/source IDs, 181 retry, 84 queued; причины, возраст и due-now сохранены в [`tennis_point_queue_census_2026-10-22_0218.json`](docs/research/tennis_point_queue_census_2026-10-22_0218.json). Этот срез не доказывает throughput или полноту импорта.
- [x] Применить миграции 026–027; проверить двум потокам доступ к двум слотам и интервал старта около двух секунд (smoke test: два активных запроса, разрывы 2,12 и 2,10 с). Проверка fencing/recovery при остановке и видимости API без executor остаётся отдельным испытанием.
- [x] Завершить 30-минутный runtime soak в пределах текущей рабочей очереди: 60/60 API 200, worker healthy 60/60, source slots ≤2, cooldown отсутствовал; 10/60 snapshots кратко отмечали overdue PBP, итоговый overdue — 0. Это подтверждает работу subset runtime, но не покрытие 500+ матчей или отсутствие latency impact при полном объёме.
- [x] Разделить deadline известного 30-дневного refresh (настраиваемые 120 секунд) и первичного/полного импорта (300 секунд), чтобы зависшее инкрементальное обновление быстрее освобождало Chromium; тесты `test_tennis_flashscore.py`, worker и lease — 54 passed; Research и worker пересобраны и healthy. Первый runtime-срез через 14 секунд: overdue PBP 0, 4 документа и 8 подтверждённых связей за час; этот короткий срез не доказывает ускорение.
- [ ] Сравнить timeout rate, profile throughput и live PBP latency до/после на фиксированной eligible-когорте; подтвердить полноту полного импорта. 30-минутный soak на 2 браузерных процессах дал 61/61 API 200, worker healthy 61/61, latency p50/p95/max 0.130/0.290/1.422 с, overdue PBP максимум 1, сеть использовала максимум 1 из 2 слотов; ресурсы: CPU worker median/p95/max 86.8/151.16/160.29% квоты 1.5 vCPU, память peak 1.403/2 GiB. Rolling-hour counters выросли документы 107→126 и подтверждённые связи 109→127, но eligible-когорта менялась 40→41, поэтому это не фиксированный throughput comparison. Повторное испытание бюджета 3 (`1 live PBP + 2 background`) остановлено досрочно: worker доходил до 1.997/2 GiB, 3 browser roots и 329 PID; network gate использовал 0–1 из 2 слотов, а очередь в основном ждала индексации матчей/player IDs. Бюджет возвращён к 2, так как ускорение не доказано, а запас памяти исчерпывался. Артефакт короткого испытания [`flashscore_capacity_trial_2026-10-22_1533.json`](docs/research/flashscore_capacity_trial_2026-10-22_1533.json) и исходные ряды runtime/resource soak. Полная историческая полнота и throughput сравнение на фиксированной когорте открыты. Отчёт предыдущего 2-slot soak [`flashscore_current_2slot_soak_2026-10-22.json`](docs/research/flashscore_current_2slot_soak_2026-10-22.json), SHA-256 `f3c373b6c15c55c40eca7662de9f7e1792df95526c387a186e3ae6950aef15b6`.
- [ ] Final-rollout evidence (2026-10-22): `flashscore-worker` now uses reserved live-PBP, background-browser, and control executors; worker source/container SHA-256 match and focused worker tests pass 4/4. Dedicated 30-minute subset soak: 61/61 HTTP 200, worker healthy 61/61, p50/p95/max API latency 0.104/0.232/1.282 s; overdue PBP was 1 in 6 samples, maximum 1, and 0 in the final sample. Resource sampler: 55/55 without errors, max two Chromium roots, worker peak 1.494 GiB (75% of its 2 GiB limit); a single sample at 01:23:16 also reached 150% CPU. Rolling-hour documents/confirmed links moved 16→40 and 16→42. This proves subset availability, not full-import throughput or resilience under injected I/O stall; keep the checkbox open. Full measurements and hashes: [`flashscore_reserved_lane_soak_2026-10-22.json`](docs/research/flashscore_reserved_lane_soak_2026-10-22.json). Live event `69173294` grew from 53 to 146 PBP events, and side orientation was confirmed; the last source point still lagged Fonbet by one point inside the active game. Raw API evidence and timestamps: [`tennis_live_pbp_api_smoke_2026-10-22_0121.json`](docs/research/tennis_live_pbp_api_smoke_2026-10-22_0121.json). The event later finished 1:2 (6-4, 2-6, 0-6), while its last captured PBP reached only 0-3 in set 3.
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
- [x] Остановить/перезапустить worker во время активной браузерной попытки и доказать recovery/checkpoint; fixture integration tests не заменяют этот runtime acceptance. Runtime-срез 13:06 UTC зафиксировал две running profile jobs и два Chromium root; после restart worker healthy, persisted 13-page checkpoint сохранился и просроченная lease перешла в durable `queued`. В 13:32 тот же job повторно взят на попытке 6 с checkpoint из 13 страниц, завершён со статусом `available`; worker сообщил о добавлении 362 result facts. Детали [`flashscore_worker_browser_restart_2026-10-22.json`](docs/research/flashscore_worker_browser_restart_2026-10-22.json).
- [x] На конкретном live-матче отдельно подтвердить своевременное соответствие PBP текущему счёту и меткам сторон. Smoke [`tennis_live_pbp_api_smoke_2026-10-21_2344.json`](docs/research/tennis_live_pbp_api_smoke_2026-10-21_2344.json), SHA-256 `e5b565a84d7e65ae52312cb6cec3e8b906af035bc13818cb03a71fa8e33a7852`: event `69173294` дал 27 confirmed PBP событий; документ отставал от счёта Fonbet, side orientation оставался null, job был `retry`. После выделения PBP executor тот же live event обновился до 146 событий по состоянию на 01:21:05 UTC: все три сета представлены, ориентация сторон подтверждена через scoreboard DOM/profile IDs. На снимке Fonbet 01:21:01 последний обычный PBP state от 01:20:48 всё ещё отставал внутри текущего гейма (15–40 против 30–40), поэтому последняя точка и полная синхронизация ленты не принимаются. API ответ, снимок Fonbet, источниковые timestamps и SHA-256 сохранены в [`tennis_live_pbp_api_smoke_2026-10-22_0121.json`](docs/research/tennis_live_pbp_api_smoke_2026-10-22_0121.json) и двух raw evidence files рядом. Свежая eligibility-проверка 14:33 UTC [`tennis_live_pbp_alignment_eligibility_2026-10-22_1433.json`](docs/research/tennis_live_pbp_alignment_eligibility_2026-10-22_1433.json), SHA-256 `4a0955dc46462f4cf8797f2a196d5df7dbef1cd7a41ac3113894db4ca75c93c3`: за последние 2 минуты наблюдалось 35 live-матчей, ни один не имел подтверждённой PBP-связи; текущий acceptance пока нельзя повторить на живом матче. Сквозной live API smoke 14:44 UTC [`tennis_live_pbp_alignment_2026-10-22_1444.json`](docs/research/tennis_live_pbp_alignment_2026-10-22_1444.json), SHA-256 `5c348a890b140b5113a74e11777bdcf43b781082b7cc99f8433c96ae402b542f`: event `69161612` / Flashscore `AJWwyZCG`, 55 авторитетных API PBP событий; document available_at отставал от текущего Fonbet snapshot на 34 с, последний PBP terminal state set 2 `1:0` совпал с текущим счётом, ориентация A/B подтверждена двумя linked profile ID и scoreboard DOM evidence.
- [x] Проверить вторую real-source PBP историю и исключить retrospective masking на event `69187254` / `xb0xhomn`: версии set 1/2/3 были не-retrospective и доступны к 13:45:44 UTC, до official result в 15:30:25; однако set 3 заканчивается game score `3:3`, хотя официальный set — `6:4`. API aggregate на `as_of` выбрал более поздние retrospective версии (доступны с 14:09:53), а источник ошибочно пометил неполный set 3 как complete. Этот матч не считается полным live PBP alignment или проверенным комплектом P/G/S labels. Подробности и повторяемый ответ API: [`tennis_live_pbp_alignment_2026-10-22_1530.json`](docs/research/tennis_live_pbp_alignment_2026-10-22_1530.json), SHA-256 `1f82621f861f2213dd494cb2709f7ce03c8e0f43b2351b85aa57ae07df08d488`.

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
- [ ] Завершить runtime-проверку: в штатной БД migration revision `033_source_point_jobs`, ключи `ADMIN_*` настроены, backend/research/frontend healthy; read-only TestClient smoke в backend контейнере подтвердил unauthenticated `401`, login/logout `200`, чтение player-profile `433` и event-match `10` заявок `200`, а числа request/decision rows остались `443/436`. Артефакт [`tennis_admin_readonly_runtime_smoke_2026-10-22_1113.json`](docs/research/tennis_admin_readonly_runtime_smoke_2026-10-22_1113.json), SHA-256 `0c86189066b4fd52187efffd9425eb4f579d4b9f80bca90f31c11619fd8c5866`. End-to-end approve/reject контрольных кандидатов и соответствующая проверка собранных backend/research/frontend ещё не завершены.
- [ ] Проверить два конкурентных решения, повторное предложение rejected пары, восстановление очереди после рестарта, `as_of` до/после решения и отсутствие ставок/проводок. Дополнительно вручную подтверждены две реальные identity связи через защищённый resolve API; матч найден (`A5F0BCMP`), а PBP прошёл `waiting_players` → `waiting_match` → `queued` → `running`. Проверка ветки нескольких кандидатов/карточки подтверждения, временного чтения и PBP документа ещё не завершена; [`tennis_manual_player_identity_review_2026-10-22_0544.json`](docs/research/tennis_manual_player_identity_review_2026-10-22_0544.json), [`tennis_manual_profile_resolution_runtime_2026-10-22_0549.json`](docs/research/tennis_manual_profile_resolution_runtime_2026-10-22_0549.json), [`tennis_live_pbp_after_identity_2026-10-22_0552.json`](docs/research/tennis_live_pbp_after_identity_2026-10-22_0552.json).

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
- [x] Проверить источник `best_of`: прямой Fonbet live payload и read-only DB выборка трёх событий не содержат явный формат; предыдущий forecast snapshot имел 8 строк со статусом `UNSUPPORTED_FORMAT`. В checkout добавить проход только явного `best_of` 3/5 и `best_of_available_at` первого наблюдения через collector/sport adapter; повторный одинаковый формат не сдвигает timestamp, forecast fail-closed при отсутствующем/будущем timestamp. Functional fixtures и compile прошли. Артефакты: [`tennis_live_forecast_availability_2026-10-22_0226.json`](docs/research/tennis_live_forecast_availability_2026-10-22_0226.json), [`tennis_format_source_trace_2026-10-22_0258.json`](docs/research/tennis_format_source_trace_2026-10-22_0258.json).
- [ ] Подтвердить end-to-end `best_of_available_at` и forecast при явном исходном формате. Предыдущий Collector export был остановлен на BuildKit ошибке, но rollout исправленной версии Collector/worker состоялся в 03:57 UTC (см. checkpoint ниже); live источник формата не предоставил, поэтому этот оставшийся критерий не проверен. Source trace: [`tennis_format_source_trace_2026-10-22_0258.json`](docs/research/tennis_format_source_trace_2026-10-22_0258.json). Свежая read-only проверка collector-normalized metadata 14:37 UTC: 36 recently observed live events, `best_of`, `best_of_available_at`, `event_format` — 0/36; отчёт [`tennis_format_live_metadata_2026-10-22_1437.json`](docs/research/tennis_format_live_metadata_2026-10-22_1437.json), SHA-256 `fcbfcff4f4e18c34b26de491c59fb1f26df033d876680c526cf4aa2586818a1c`. Это подтверждает отсутствие полей в collector output. Актуальный raw-source audit проверил 50 content-addressed Fonbet payloads / 3,079 event rows и не нашёл явных format keys; DB metadata не содержит значений у 3,847/3,847 tennis events. Артефакт [`tennis_format_raw_live_source_audit_2026-10-22_1540.json`](docs/research/tennis_format_raw_live_source_audit_2026-10-22_1540.json), SHA-256 `d2c6313309b53041e2e42b360be2a2b0123b2a0cf7bd54ffece397249ec9462c`. Поэтому end-to-end `best_of_available_at` при явном source формате всё ещё не проверен; формат остаётся unknown и inference по турниру/гендеру/счёту не применяется.
- [x] После дополнительного сбора пересчитать аудит и manifest на рабочей БД: [`tennis_label_audit_2026-10-21_0750.json`](docs/research/tennis_label_audit_2026-10-21_0750.json), digest `6d13c80e7fdd…`. P/G/S — 22 размеченных матча всего, 6 в начальном train, 1–2 на validation и 3 holdout; матч — 892/149×4/372, но все temporal folds провалены. Feature PIT 0/3,266, 470 source result timestamps не доказаны; обучение остаётся закрыто.
- [x] Добавить fail-closed PIT-проверку timestamps по группам источников; неполные/будущие времена отклоняются, пока данные не сертифицированы.
- [x] При принятии официального Fonbet-результата сохранять `available_at`; повтор неизменённого результата сохраняет первое время, исправление получает время новой доступности. Старые строки не backfill-ятся по расписанию или `finished_at`.
- [x] Применить обновлённый Research/`flashscore-worker` и Backend; все три контейнера healthy. Backfill обнаружил 2,206 завершённых одиночных событий, 2,205 ждут профилей, восемь заданий игроков поставлены в очередь, один матч уже покрыт PBP.
- [ ] Проверить восстановление очереди после перезапуска и измерить, что архивные запросы не задерживают live при общем сетевом лимите.
- [ ] Собрать и подтвердить PBP для сотен уникальных матчей; ежедневно публиковать воронку профиля → матч → связь → документ → метки четырёх целей. Свежий census 16:32 UTC: 1,768 версий по 117 source matches, из них 401 non-retrospective по 34 matches; 1,605 matched-link rows покрывают 120 Fonbet events. После cutoff последнего admission добавилась 71 версия и один source match, поэтому «сотни» не достигнуты; PBP очередь — 2,688 waiting_players, 173 retry, 92 queued и 1 running; profile очередь — 596 queued, 1,015 retry и 2 running. Артефакт и разбиение по причинам: [`tennis_live_pbp_ingestion_2026-10-22_1632.json`](docs/research/tennis_live_pbp_ingestion_2026-10-22_1632.json), SHA-256 `78126b4368e8fac50a0da1346acb0b3cbfc64f98a9a8cf09c860567d89a8ebb5`.
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
- [x] Обновить полный read-only admission audit после завершения event `69173294`, применив bounded batches для exact-score snapshot lookup; сохранить [`tennis_label_audit_2026-10-22_batched.json`](docs/research/tennis_label_audit_2026-10-22_batched.json), manifest `tennis_training_manifest_77fcc1b3a797.json` (2,400 whole matches; 1,920 development / 480 sealed holdout) и byte-hashed provenance `tennis_training_examples_6af68a476573.jsonl` (4,284 rows). Gate `blocked`: point/game/set имеют по 35 матчей, match-fold temporal precedence провалена во всех четырёх окнах, у игровых целей недостаточно примеров. PIT 4,284/4,284 означает только соответствие контракту с explicit defaults/unknown; source-observed примеров 0. Holdout quality не открывался. Полная интерпретация и hashes: `docs/research/tennis_training_preflight_2026-10-22_batched.md`.
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
- [ ] Подтвердить завершение обновления профилей и повторный поиск `69155384`, затем получить для реального связанного матча PBP, совпадающий с живым счётом и подтверждённой стороной. Для `69148307` ссылка дала только summary; event `69173294` имел 53 подтверждённых PBP sequence events, но последний non-empty документ (23:53 UTC) заканчивается на set-1 5:3. На 00:14 UTC Fonbet показывает set 1 6:4 и set 2 1:2; последняя версия Flashscore не содержит PBP tab, job `retry/point_history_tab_missing`, а effective link `external_home_is_fonbet_a` остаётся null. Archived DOM подтверждает source home=Nishimoto/Fonbet A, но link не содержит durable side confirmation. Метки не допускаются к обучению; свежий снимок [`tennis_live_pbp_api_smoke_2026-10-22_0014.json`](docs/research/tennis_live_pbp_api_smoke_2026-10-22_0014.json), историческое доказательство — [`tennis_live_pbp_api_smoke_2026-10-21_2344.json`](docs/research/tennis_live_pbp_api_smoke_2026-10-21_2344.json).
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
- [x] Повторить аудит после накопления новых подтверждённых Fonbet result observations; не backfill-ить legacy timestamp и не легализовать неизвестные времена. Полный read-only аудит с cutoff `2026-10-22T12:36:04.989988Z` сохранён в [`tennis_label_audit_20261022T1302Z.json`](docs/research/tennis_label_audit_20261022T1302Z.json), execution и хеши — [`tennis_admission_execution_2026-10-22_1236.json`](docs/research/tennis_admission_execution_2026-10-22_1236.json). После новых результатов P/G/S имеют по 51 размеченному матчу; их causal train всё ещё 0 в каждом fold, validation 4/2/1/1 и holdout 17 на цель. Match: 2,554 метки, causal train 0, validation 203/203/204/203, holdout 508. PIT `0/5,372` из-за отсутствующего source-time `event_format`; result provenance — 2,262 official metadata, 277 exact snapshots, 143 без времени и исключены. Admission остаётся `blocked`; holdout quality не открывали, обучение и SHADOW не запускали.

- [x] Повторить полный admission audit на рабочей БД после накопления PBP, не обучая модели и не открывая качество holdout. Аудит `docs/research/tennis_label_audit_2026-10-21_2342.json` (SHA-256 `12185433e567e7bc2ba5009b1bc44a2e7a27fc1fd4a9b74babd31c9e945d1345`), manifest `docs/research/tennis_training_manifest_854f241351dc.json` (dataset digest `854f241351dc08fbf33a1f1921c4ad71601420f9828f159f3922502b060790f4`, 2,392 матчей: 1,913 development / 479 sealed holdout), provenance `docs/research/tennis_training_examples_a9164a19d7bc.jsonl` (4,219 строк, byte SHA-256 `cedf5722e4dbb5d49f7cf0056102cd4bb1121b4e19b878da01441dc9d2d4a1b7`). Observed PBP улучшился до point 26 и game 6 примеров; set 0. Timeline P/G/S осталась 19/23/26/27 train, 4/3/1/0 validation, 7 holdout; match окна численно достаточны, но temporal precedence провалена в 4/4 folds. PIT 4,219/4,219 только с contract defaults/unknown, source-observed 0. Gate `blocked`; причины и counts в JSON. Holdout quality не открывался.

**Acceptance:** отчёт допуска считает только размеченные матчи, содержит provenance и объясняет каждый блокирующий гейт; в рабочее обучение можно перейти лишь после прохождения всех четырёх целевых порогов и point-in-time сертификации.

Открытые до обучения пункты включают PIT-происхождение метрик игроков/подачи/покрытия, доказанное время доступности обучающих результатов до validation-прогнозов и достижение порогов P/G/S. Soak подтвердил здоровье сервиса, но не гарантирует отсутствие кратких задержек: в 13/60 sample был overdue PBP, в последнем и итоговом API-срезе — 0. На live Fonbet event `69148307` подтверждённая ссылка `vV6BKNOq` теперь используется даже до импорта строки `ExtTennisMatch`; после выкладки на 10-й попытке архивирован документ, но у страницы нет PBP-разметки (`EXPECTED_SET_1_HISTORY_NOT_AVAILABLE`), API показывает `versions_total=1`, `total=0`, job остаётся на повторе. Завершённый `69155384` пока ждёт игроков/индексации (`HISTORICAL_FLASHSCORE_MATCH_NOT_INDEXED`, 7 попыток). End-to-end приёмка PBP остаётся открытой до фактических очковых событий и непустого API. Lease fencing и восстановление dispatch после пересоздания сервиса во время работающей очереди проверены; отдельное принудительное убийство активного executor child остаётся открытым. Сверка 20 PBP-матчей — техническая source/DOM-проверка; независимая ручная или vendor-сертификация не заявляется.

- [x] Зафиксировать сторону Flashscore только по ID обоих игроков в source scoreboard DOM, сверенным с подтверждёнными профилями, либо по явному manual confirmation; перестать выводить сторону из порядка URL slug. 58 тестов `test_tennis_flashscore.py` прошли на собранном образе. `research` и `flashscore-worker` пересобраны и развернуты из текущего checkout; после rollout оба helper импортируют и worker healthy. Образы: research `sha256:40e6a13daeda6e0d8088cf3335c5d81ec2a8076caf73d2abdb61c5a0ce3c472d`, worker `sha256:e903abec438315d0ed05bc362740ae47f2b4abc71b8d5dd77b76de59a5da05ec`. Очередь после rollout: HTTP 200, heartbeat healthy, overdue PBP 0; это не закрывает фактическое PBP lag/retry у event `69173294`; recovery non-empty archive-import checkpoint проверен отдельно в Phase 39.1.

- [x] Исправить потерю `progress_json.page_checkpoint` при claim просроченного profile lease: при `enqueue` и повторном `claim` обновляются stage/attempt без потери сохранённых страниц. Unit regression и dispatch suite — 5 passed; PostgreSQL integration `test_tennis_refresh_lease_integration.py` — 2 passed: истёкший retry сохраняет checkpoint и после claim. Worker/Research пересобраны/развернуты (Research `sha256:1a8b8e4981ae21d029f003eae4e5dfda387b5a830667ab334d894e10126d1802`, worker `sha256:aaec33c0a516ad57aad8e75f10a50dd39a27ccc5502a14e2ae8a6901d441041d`). Реальный restart подтвердил recovery активного archive-import job: attempt 36→37, lease обновлён, все 16 из 16 checkpoint pages сохранены; одновременно другой job удерживал 219 страниц. Артефакты: [`flashscore_worker_restart_2026-10-22_nonempty-checkpoint.json`](docs/research/flashscore_worker_restart_2026-10-22_nonempty-checkpoint.json), [`flashscore_worker_restart_2026-10-22_active-import-checkpoints.json`](docs/research/flashscore_worker_restart_2026-10-22_active-import-checkpoints.json). Артефакт [`flashscore_worker_restart_2026-10-22_nonempty-checkpoint.json`](docs/research/flashscore_worker_restart_2026-10-22_nonempty-checkpoint.json).

- [x] Повторить полный read-only admission audit на актуальных данных до `2026-10-22T15:39:52.074048+00:00`; проверка gate завершена со статусом `blocked`, поэтому обучение не запускается. Отчёт [`docs/research/tennis_label_audit_20261022T1540Z.json`](docs/research/tennis_label_audit_20261022T1540Z.json), SHA-256 `658b31c99e013cea001c09ea8ce67bf3c46ee18ba72218e7f50a0db341231660`; execution [`docs/research/tennis_admission_execution_2026-10-22_1539.json`](docs/research/tennis_admission_execution_2026-10-22_1539.json), SHA-256 `0ca0475dc246e03901d9c834fb6f24ac96d6a96dc3fcd46e2c14a75a00dd9262`. Manifest [`docs/research/tennis_training_manifest_af309e711ba0.json`](docs/research/tennis_training_manifest_af309e711ba0.json) — 2,588 матчей (2,070 development / 518 sealed holdout), dataset digest `af309e711ba07b18d021c6c6e48a22fbcef3ed6c6f06f4743d293b139df654a7`, file SHA-256 `1691988dfb2c174d18520421dc0fa8db7c01cefdc3e00f738ab07590ccaa0ac8`; provenance [`docs/research/tennis_training_examples_3fdc5e29899b.jsonl`](docs/research/tennis_training_examples_3fdc5e29899b.jsonl) — 6,083 примеров, SHA-256 `a91a4277f424dc9923b1c3fe3106ec895b80463325e4b618ae56be0ded4d6d43`. Labels point/game/set/match: 64/64/63/2,603. Причинный train после временного purge — 0 для всех четырёх целей во всех folds; validation P/G/S — 3/2/2/4, holdout по 24, match validation 207 в каждом fold и holdout 518. Feature PIT `0/6083`: `event_format` отсутствует у 6,083/6,083 примеров; 143 недатированных результата исключены. Пороги 500/100/200 и требование обоих классов не снижались; holdout quality не открывалось, обучение/кандидаты/SHADOW не запускались.
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
- [ ] Получить достаточные реальные наблюдавшиеся PBP и PIT-доступность спортивных признаков. Последний полный audit 2026-10-22 01:57:42: observed examples point/game/set = 138/24/2; PIT-check 4,284/4,284 проходит только с явными defaults/unknown, fully source-observed = 0. Статус обучения остаётся `blocked`; подробности в `docs/research/tennis_training_preflight_2026-10-22_batched.md`.
- [x] Переснять read-only census durable player jobs 2026-10-22 02:10 UTC: 2,531 jobs — completed 320, failed 530, queued 534, retry 1,057, running 2, waiting_confirmation 4, skipped doubles 84. Группировка по причине, стадии, возрасту и due-now сохранена в [`flashscore_profile_job_census_2026-10-22_0209.json`](docs/research/flashscore_profile_job_census_2026-10-22_0209.json). У `REFRESH_ABORTED` известна стадия `resolving_profile`, но исходная ошибка отсутствует в сохранённой записи и не найдена в логах последних попыток; массовых retry/reset не делал.
- [ ] Завершить per-job root-cause attribution для `REFRESH_ABORTED` и проверить восстановление активного Chromium child после принудительной остановки. Новый код сохраняет стадии `starting` и `resolving_profile` под fencing lease; integration regression — 2 passed, но изменение ещё не выкачено и существующие generic sentinel jobs этим не объяснены. Non-empty checkpoint recovery на archive import проверен отдельно, однако это не закрывает profile-child kill/restart acceptance.

Подробные counts, exclusions и очередной preflight: [`tennis_training_preflight_2026-10-21_after_live_pbp_check.md`](docs/research/tennis_training_preflight_2026-10-21_after_live_pbp_check.md). Holdout quality остаётся закрытым. Тестовые реализации LR/GB/GRU на fixture данных не равны обучению кандидатов.

### Уточнение PIT-контракта — 2026-10-21 14:05 UTC

- [x] Закрыть незафиксированную поверхность: live inference использует court surface только с отдельным `surface_available_at <= cutoff`; без timestamp/future timestamp кодирует `unknown`. Изменения вектора входят в forecast fingerprint; свежий `neural` healthy.
- [x] Обновить validator обеих сторон контракта: пропущенный timestamp остаётся ошибкой, если не указан точный contract-default; тесты проверяют отсутствие выдуманного source timestamp.
- [x] Сверить audit с inference: только point-in-time metric snapshots, действующая на срезе подтверждённая идентичность, реальные serve-знаменатели из snapshot или exact-name serve log за 365 дней, явные priors и отдельные resolution counters. Для priors `source_available_at` остаётся null, а причинность указывается отдельным `contract_defaulted_sources`.
- [x] Сохранить свежий отчёт [`tennis_label_audit_2026-10-21_feature_provenance.json`](docs/research/tennis_label_audit_2026-10-21_feature_provenance.json), manifest [`tennis_training_manifest_95779924731d.json`](docs/research/tennis_training_manifest_95779924731d.json) и preflight [`tennis_training_preflight_2026-10-21_feature_provenance.md`](docs/research/tennis_training_preflight_2026-10-21_feature_provenance.md). PIT-правила проверены на 3,977 примерах; все прошли только потому, что defaults причинны и явно зафиксированы. Player-metric inputs полностью наблюдались лишь в 63 строках; serve и surface имеют ноль наблюдённых значений.
- [x] Пересчитать допускаемые метки: 31 P/G/S матч, 1,009/596/67 игровых примеров, observed PBP 0; общий manifest содержит 2,013 матчей, из них 6 игровых целей в holdout. Match имеет 2,305 меток и проходит размер окон, но временной порядок train/validation нарушен во всех folds; 484 события не имеют доказанного времени результата, 420 валидных исходов исключены.
- [x] Проверить изменения: odds-free forecast — 15 tests passed; PIT/manifest audit — 13 passed. Neural image rebuilt/restarted; healthcheck healthy и SHA-256 `/app/app/tennis_forecast.py` совпадает с исходником. Рабочее обучение не запускалось.
- [ ] Продолжать сбор и исправить хронологию/историю до покрытия 500 initial-train, 100 на каждом validation, 200 holdout по каждой цели. P/G/S недостаточны (latest observed point/game/set examples 26/6/0), match train labels позднее validation forecasts, admission остаётся `blocked`; LR/GB/GRU на рабочей выборке и SHADOW не запускать.

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
- [x] Подтвердить fixture-only primitives независимых кандидатов и checkpoint schema 2 в изолированном neural image: `tests/neural/test_independent_tennis_models.py` — 7 passed. Проверены seed, feature/checkpoint/calibration/inference versions, training device, параметры и save/load parity. Результаты не являются оценкой качества и не открывают training gate.
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
- [P0] [x] Завершить свежий полный admission report с точными snapshot times. Полный текущий audit завершился после потокового чтения PBP; отчёт, manifest, provenance и численные причины блокировки зафиксированы в checkpoint 2026-10-22 04:21 UTC ниже. Gate остаётся закрыт; legacy timestamps не менялись, обучение не запускалось.

### Точность Flashscore identity matching — 2026-10-21

- [x] Расширить безопасное автосопоставление односложного инициала и полной фамилии распространёнными вариантами русско-латинской транслитерации (`ч/ch/c`, `ж/zh/j`, `ш/sh/s`, `ц/ts/c`, `х/kh/h` и др.); сложные имена остаются ручными. `tests/research/test_tennis_flashscore.py` — 53 passed.
- [x] Схлопывать повторные карточные ссылки одного live-матча по стабильному Flashscore `mid`, чтобы дубликат URL не создавал лишнюю заявку подтверждения; отдельные матчи той же пары по-прежнему ждут выбора.
- [x] Сопоставлять ID игроков только по окончанию двух отдельных Flashscore participant-slug сегментов; это исключает ложное неоднозначное предложение из-за вхождения ID в другой slug/query. Проверка URL `Adkar Vaishnavi — Shkutova Anna` и регрессия случайного подстрочного совпадения: `test_tennis_flashscore.py` — 54 passed; вместе с point-history retrieval и sport-engine regressions — 67 passed. UI подтверждений показывает имена участников из URL вместо непрозрачного `Матч Flashscore <id>`.
- [ ] Принять новый 30-минутный runtime soak после точного slug-сопоставления и healthcheck rollout. Первый прогон нашёл ложный Docker `healthy`: `/tmp/healthy` проверялся только на существование, хотя его возраст превысил 3 минуты и DB heartbeat остановился; в тот же период host I/O wait достигал 57–78%. Healthcheck заменён на проверку возраста heartbeat ≤45 секунд. Soak 06:05:58–06:35:58 UTC завершён: 60 queue/event samples и 15 PBP/as_of samples, HTTP errors 0, worker healthy 60/60; queue p50/p95 85.7/765.8 ms, event 9.1/114.9 ms, point-history 133.1/1028.5 ms. Target PBP job всё ещё был queued после `SET_3_NAVIGATION_TimeoutError`, retry не восстановился и source progress отсутствовал, поэтому acceptance recovery/lag остаётся открытым. Raw samples и отчёт: [`tennis_live_runtime_soak_2026-10-22_0604.jsonl`](docs/research/tennis_live_runtime_soak_2026-10-22_0604.jsonl), [`tennis_live_runtime_soak_2026-10-22_0604_report.json`](docs/research/tennis_live_runtime_soak_2026-10-22_0604_report.json). После изменения browser capacity нужен отдельный повторный soak.
  - Проверочный прогон завершён 18:00–18:31 UTC, но gate оставлен открытым: 57/59 запросов успешны, два 10-секундных timeout; у всех успешных срезов worker healthy и `overdue_pbp=0`. p50/p95/max latency 0.606/2.071/9.562 s. За отдельные 11 минут ресурсов ошибок не было, максимум два Chromium root-процесса, worker 510.7 MiB. Артефакты: `docs/research/flashscore_soak_2026-10-21_post-stale-health.jsonl`, `docs/research/flashscore_resources_2026-10-21_post-stale-health-labels.jsonl`. Не считать это подтверждением полного восстановления при I/O stall.
  - Дополнительный subset API soak после recovery: [`flashscore_soak_2026-10-22_post-recovery.jsonl`](docs/research/flashscore_soak_2026-10-22_post-recovery.jsonl), 60/60 HTTP 200 и worker healthy; p50/p95/max 0.113/0.268/8.708 с. `overdue_pbp` был 1 в 6/60 срезах (максимум 1, к последнему срезу 0); объём — 1–2 eligible events, documents/confirmed links 6→13. Один Flashscore navigation timeout 25 с перешёл на retry. Повтор не воспроизводил I/O stall и не доказывает полноту импорта, поэтому основной acceptance остаётся открытым.
  - Полный post-restart прогон после исправления checkpoint: [`flashscore_soak_2026-10-22_post-final-restart.jsonl`](docs/research/flashscore_soak_2026-10-22_post-final-restart.jsonl), 61/61 HTTP 200, p50/p95/max 0.083/0.227/0.333 с; worker healthy в 58/61 срезах, три последовательных среза 00:36:28–00:37:28 показывали устаревший heartbeat, затем восстановился. `overdue_pbp=1` в 16/61 срезах (максимум 1, последний 0). Ресурсы: [`flashscore_resources_2026-10-22_post-final-restart.jsonl`](docs/research/flashscore_resources_2026-10-22_post-final-restart.jsonl), 55 срезов без ошибок, максимум два Chromium root, worker 1,008 MiB, Research 112 MiB. Свежий queue snapshot [`tennis_flashscore_queue_2026-10-22_0057.json`](docs/research/tennis_flashscore_queue_2026-10-22_0057.json), SHA-256 `7e0f335982ec0173511d94797e3c24d0d38f2a83585acc052715e1e817c93bdf`: HTTP 200, 10 eligible events, 14 prepared players, overdue 0, worker healthy. Soak acceptance оставлен открытым из-за трёх health failures и ненулевого backlog; transient I/O stall не инжектировался и не проверен.

### Полный admission audit после продолжения сбора — 2026-10-22

- [x] Пересчитать аудит после дальнейшего сбора на текущей рабочей БД и сохранить [`tennis_label_audit_2026-10-22_postsoak.json`](docs/research/tennis_label_audit_2026-10-22_postsoak.json), manifest [`tennis_training_manifest_postsoak.json`](docs/research/tennis_training_manifest_postsoak.json), provenance [`tennis_training_examples_e134829734dc.jsonl`](docs/research/tennis_training_examples_e134829734dc.jsonl). Dataset digest `a9666c9694c507ea53d5885afe9e00e384b8b813680cba1605c130fa8405552d`; 2,393 матча, 1,914 development / 479 закрытый holdout. SHA-256 report `257149e5dd3a7480e53f9104e13f236bf7cd569546e2d1a1353dcbbb67b130d4`, manifest `a6349c07655a3cafbf5b66a9cbf0ed990b8921074592a51f8d4774ba636b1e4b`, provenance `8da96ca7a8adaf87ea3ede946dbf2b0429ba6c6a1b40e207c5d10bb8c1a01a9e`.
- [x] Зафиксировать контрактную PIT-семантику и допуск: 4,220/4,220 проверок причинны только с документированными defaults/unknown; полностью source-observed примеров 0. Наблюдавшиеся target examples point/game/set выросли до 49/9/0, размеченных матчей по 34; общие folds P/G/S остаются train 19/23/26/27, validation 4/3/1/0, holdout 7, temporal precedence 0/4. Match имеет 2,393 метки, но его temporal precedence также провалена во всех folds. Gate `blocked`, holdout quality не открывался.
- [x] Обновить единый audit/manifest после финального live PBP rollout. Первый широкий exact-score lookup был безопасно остановлен под лимитом памяти PostgreSQL; после пакетирования 100 event IDs на lookup и пакетирования исторического fallback аудит завершился полностью. Авторитетные артефакты и свежие counts зафиксированы в `docs/research/tennis_label_audit_2026-10-22_batched.json`, `tennis_training_manifest_77fcc1b3a797.json`, `tennis_training_examples_6af68a476573.jsonl` и `docs/research/tennis_training_preflight_2026-10-22_batched.md`. Holdout quality не открывалось; статус обучения остаётся `blocked`.
- [ ] Добавить отсутствующие официальные рейтинги тура, H2H и opponent-adjusted serve/return с надёжным source-time provenance; текущий odds-free v1 их не содержит, архивные поля сами по себе не доказывают PIT-доступность.
- [x] Сверить сохранённый источник PBP для 20 последних архивов и сохранить [`tennis_pbp_source_review_2026-10-22.json`](docs/research/tennis_pbp_source_review_2026-10-22.json): 6 страниц содержали PBP-разметку (43 гейма/215 состояний, 0 расхождений очков и подачи); 14 не имели распознаваемых PBP-строк, в выборке нет tie-break. Оригиналы для ручного принятия и tie-break остаются открытыми.
- [x] Выполнить read-only smoke forecasts API: проверить повторяемость страницы, непересечение offset-страниц, сортировку и отсутствие odds/price в payload; один матч, 106 строк. Runtime карточка отображает trajectories/scenarios и unavailable reason. Полная проверка охвата live/prematch остаётся открытой.
- [x] Реализовать odds-free strategy replay с текущим `tennis.cfg` и режимом положительного EV-подтверждения. В endpoint и pure replay нет записи bets, model journal или bankroll; шесть replay-тестов проходят. Изолированный Backend smoke: HTTP 200, подходящий scope 1 live / 0 prematch, DB counts Bet/LedgerEntry/ModelBetJournal без изменений. Отчёт: [`tennis_odds_free_strategy_replay_smoke_2026-10-22.json`](docs/research/tennis_odds_free_strategy_replay_smoke_2026-10-22.json). В свежем MCP live contour 02:17 UTC видны 9 forecast rows, но все `UNSUPPORTED_FORMAT`, поэтому вероятности и nonempty strategy signal не созданы; prematch событий в окне нет. Свежий сводный снимок: [`tennis_live_forecast_availability_2026-10-22_0217.json`](docs/research/tennis_live_forecast_availability_2026-10-22_0217.json). Контракт fail-closed проходит, успешный forecast и непустой live signal остаются непроверенными.
- [ ] Накопить наблюдавшиеся PBP, причинные метрики игроков/подачи/покрытия и result observations; повторить admission до достижения всех неизменных порогов 500/100/200 по каждой цели и прохождения временных проверок. Обучение кандидатов остаётся закрытым.
- [x] Выполнить ручную source-DOM сверку оригиналов 20 завершённых PBP матчей: прочитаны 162 игры и 863 точечных состояния, включая 154 deuce/advantage перехода и 14 TB строк; state/server/TB mismatches — 0. Пропуски `E9vF4SAD` остались явными и не достроены; TB `WAlDxZFD` прошёл от 1:0 до 6:8, соответствуя set 6:7; отдельно просмотрена коррекция server-side для `UXYqpB76`. Отчёт с document IDs, URL, archived paths, content hashes и ограничениями: [`tennis_pbp_manual_review_2026-10-22_0816.md`](docs/research/tennis_pbp_manual_review_2026-10-22_0816.md), [`tennis_pbp_manual_review_2026-10-22_0816.json`](docs/research/tennis_pbp_manual_review_2026-10-22_0816.json). Это ретроспективная проверка исходного DOM; она не сертифицирует live полноту или PIT признаков. Отдельный live матч ранее сверялся с Fonbet timeline/source identity: [`tennis_live_pbp_source_review_2026-10-22_0557.json`](docs/research/tennis_live_pbp_source_review_2026-10-22_0557.json).
- [x] Переснять source-level review последних 20 confirmed links на архивированных HTML: [`tennis_pbp_source_review_2026-10-22_latest20_details.json`](docs/research/tennis_pbp_source_review_2026-10-22_latest20_details.json), SHA-256 `58d8dd5aa7626632366c36020df16b7be4e1e80aad4b098a88aeb8ae0d7c7851`. В свежей выборке 4/20 имели PBP: 26 геймов, 137 состояний, 0 score/server mismatch; 17/20 имели gaps, 2 PBP матча без подтверждённого home/Fonbet side orientation. Это read-only техническая сверка, она не закрывает ручную сверку оригиналов.
- [x] Ускорить очередь Flashscore после двух редких API timeout: убрать чтение всех исторических player/PBP/match jobs, ограничить выборки актуальными событиями и последними 24 часами для завершённых карточек; выбирать последнюю связь на внешний матч. Пересобранный backend прошёл 10 последовательных запросов `limit=50`: 10/10 HTTP 200, p50 0.057 s, максимум 0.349 s; новая API regression-проверка bounded projection прошла (1 passed); runtime worker healthy, `overdue_pbp=0`. Это smoke; повторный длительный soak после оптимизации требуется для закрытия предыдущего пункта.
- [x] Убрать из админских подтверждений однозначные одиночные связи матча: reconciliation принимает только единственного pending кандидата с корректным `mid` и двумя отдельными slug-ID уже подтверждённых Flashscore-профилей. Сложные/неоднозначные кандидаты остаются ручными. Два реальных старых предложения (Адкар—Шкутова, Ла Серна—Торрес) прошли проверку, записаны как системные approvals, PBP задания возобновлены; API обоих событий отвечает 200, документов пока 0. `test_confirmation_recovery.py` — 5 passed. Reconciliation включён в выкаченный Research/worker.
- [x] Исправить транслитерацию `й`: защищать букву при NFKD-нормализации и сравнивать только соответствующие позиции как `i/y/j`. Проверить автоматические соответствия `Чайковская В` ↔ `Caykovskaya/Chaykovskaya Violetta`, `Нишимото Ч` ↔ `Nishimoto Catherine`, а также `ж/ш/ц` и близкий отрицательный пример; подтверждать только полную фамилию и совпавший инициал. Unit/regression tests должны пройти до rollout Research/Flashscore worker.

### Полный admission audit с обязательной доступностью формата — 2026-10-22 03:15 UTC

- [x] Повторить полный read-only audit после обнаружения, что `sets_to_win` зависит от `best_of`, но прежняя PIT-группировка не требовала доказательства доступности формата на момент прогноза. Отчёт [`tennis_label_audit_2026-10-22_event-format.json`](docs/research/tennis_label_audit_2026-10-22_event-format.json), SHA-256 `06ac6f6e4ae9e03071f8e5bb1ca39f7047d3b3102ba7e04073476de83b3b1f53`; единый manifest [`tennis_training_manifest_ce0b2f6775ed.json`](docs/research/tennis_training_manifest_ce0b2f6775ed.json), dataset digest `ce0b2f6775ed596f5bbb10f3a935345431dfa899542ad242c89b652c999fa969`, SHA-256 `edf5558a145f1ddafa9d8340d3f8645f7b0884f87f7ae51b8aaf2f9e280ae24e`; provenance [`tennis_training_examples_c5d453a7ec5a.jsonl`](docs/research/tennis_training_examples_c5d453a7ec5a.jsonl), SHA-256 `8bccb109aa6bcb48dd1526d69ed4975cc80285e833ad158884b5dcf6c492e264`. В manifest 2,407 целых матчей (1,925 development / 482 sealed holdout); holdout quality не открывалось.
- [x] Зафиксировать результат строгой PIT-проверки: 4,319 примеров проверено, сертифицировано 0; `event_format` отсутствует либо не доказан к `as_of` у всех 4,319. Предыдущая отметка PIT `4,284/4,284` не доказывала доступность `best_of` и производного `sets_to_win`; её нельзя считать закрытием этого условия. Формат не подменяется contract default.
- [x] Пересчитать неизменные окна и причинный порядок меток: point/game/set — 36/36/35 матчей с метками на manifest timeline; causal train после purge — 0 в каждом из четырёх окон для этих целей, validation — 3/3/1/0, holdout — 9/9/8. Match — 2,407 меток на timeline и 482 sealed holdout, но общий допуск не пройден. Gate остаётся `blocked` по покрытиям, walk-forward/holdout минимумам, временной доступности меток и PIT-доступности признаков. Пороги 500/100/200 не менялись.
- [x] Добавить fail-closed передачу только явного поддерживаемого Fonbet `best_of` и `best_of_available_at` при создании/обновлении события; live forecast и audit требуют `best_of_available_at <= as_of`. Добавлены проверки parser, adapter, PIT forecast и audit; обновлён архитектурный контракт (`01-ARCHITECTURE.md`). Источник не отдавал явный `best_of` на трёх проверенных live событиях; collector raw snapshot сейчас не сохраняет полный upstream payload.
- [x] Собрать collector image с передачей `best_of`: `sha256:48376118b0c3690016c6d17599531076d5d0e2800f03042a09aa4bb4bfaaf572` (577 MB). Предыдущая ошибка BuildKit не повторилась.
- [x] Проверить parser в собранном collector image `sha256:48376118b0c3690016c6d17599531076d5d0e2800f03042a09aa4bb4bfaaf572`: явные 3/5 проходят; отсутствующее, unsupported, boolean и fractional значения остаются неизвестными.
- [x] Снять текущий MCP live snapshot 2026-10-22 03:36:52 UTC: 20 live событий, PBP queue status — 15 `waiting_players`, 3 `not_queued`, 2 `retry`; 10 forecast rows с `UNSUPPORTED_FORMAT`. Артефакт [`tennis_live_forecast_availability_2026-10-22_0335_latest.json`](docs/research/tennis_live_forecast_availability_2026-10-22_0335_latest.json).
- [x] Развернуть собранные collector и worker; проверить новые snapshot: parser/metadata изолированные image smoke прошли, live-контур здоров. Snapshot `1792641434412-0` от 03:57:13 UTC содержал 21 live event; все 21 дошли до DB. У snapshot и DB нет явного `best_of`, поэтому timestamp формата не создан. Артефакт [`tennis_format_runtime_after_rollout_2026-10-22_0358.json`](docs/research/tennis_format_runtime_after_rollout_2026-10-22_0358.json).
- [ ] Восстановить очередь без потери/переупорядочивания истории: read-only аудит 04:35 UTC ([`tennis_fonbet_raw_pending_audit_2026-10-22_0435.json`](docs/research/tennis_fonbet_raw_pending_audit_2026-10-22_0435.json)) — `fonbet.raw` удерживает 10,000 из 93,987 доставленных записей; adjacent snapshots видели 81–82 pending, 15 ID уже без payload после trim, lag 0. Consumer читает только новые `>` сообщения. Pending оставлены без изменений; перед повторным проигрыванием нужен age-aware idempotent replay и/или восстановление из архивов с исходным `collected_at`.
- [x] Выкатить neural PIT event-format код: image `sha256:bf10f5d44183c315f879e2070eb2f12353285a622baeeaf3413a7a72f6a586c1`, health healthy, `/app/app/tennis_forecast.py` SHA-256 совпадает с checkout. Изолированные проверки missing/future/causal `best_of_available_at` прошли. Очередь `training.jobs`: pending=0, lag=0; restart не запускал обучение.
- [x] Проверить live inference после neural rollout: после старта образа `sha256:bf10f5d44183c315f879e2070eb2f12353285a622baeeaf3413a7a72f6a586c1` в истории сохранены два новых odds-free forecast (05:06 и 05:27 UTC), оба с `UNSUPPORTED_FORMAT`, `forecast_available=false`, без вероятностей и без `best_of`/`best_of_available_at`. Это подтверждает работающий fail-closed контур и запись истории; получение причинного прогноза остаётся невозможным до источника формата. Артефакт [`tennis_live_forecast_post_neural_rollout_2026-10-22_0536.json`](docs/research/tennis_live_forecast_post_neural_rollout_2026-10-22_0536.json), SHA-256 `a7a9a3740f4da5a6e18626a6f8c6a98c94d63bd662b9a7451294305de68dc58a`. Общее покрытие live-событий с обоими профилями по прежнему snapshot было 0/18; ручной аудит источников отдельно открыт.
- [x] Сверить три реальные версии live PBP с сохранённым Fonbet snapshot на момент получения и с подтверждённой ориентацией сторон: [`tennis_live_pbp_asof_review_2026-10-22_0412.json`](docs/research/tennis_live_pbp_asof_review_2026-10-22_0412.json), review time 04:12:40 UTC. Все три связи confidence 1.0 и non-retrospective; одна запись содержит полные два сета (137 очков), две совпадают с частичным live-счётом. Проверка техническая, не заменяет ручную сверку оригиналов 20 PBP матчей; этот пункт остаётся открытым.
- [x] Сверить ещё один реальный live PBP по source versions, стороне и времени: [`tennis_live_pbp_source_review_2026-10-22_0555.json`](docs/research/tennis_live_pbp_source_review_2026-10-22_0555.json), SHA-256 `5ec5441ae9d54d6b7b86d6c7de8c785e7005a283d7a2d3277ce9660353560f73`. Для матча Yidi Yang—Chengyiyi Yuan сохранены три сета/шесть версий; выбранные версии дают 240 событий, у источников нет gaps/ambiguous, `retrospective=false`. `as_of` до identity не возвращает события; после импорта API возвращает только версии, доступные к запрошенному времени. Счёт 7:6, 1:6 и состояние третьего сета совпали с Fonbet snapshots на временах получения. Это одна ручная проверка; требование проверить 20 матчей остаётся открытым.
- [x] Повторить сверку live PBP на следующем refresh: [`tennis_live_pbp_source_review_2026-10-22_0557.json`](docs/research/tennis_live_pbp_source_review_2026-10-22_0557.json), SHA-256 `e88789a9e2543ae226dbd64a78502dcb2771f875547c0abe8897006d16664101`. На том же live матче latest `as_of` вернул три сета/девять source versions и 242 события; set 3 дошёл до тай-брейка 3:0 и совпал с ближайшими Fonbet snapshots до/после `available_at`. Матч ещё live, итоговой метки нет; счётчик ручной проверки — один уникальный матч, порог 20 остаётся открытым.
- [x] Сверить прежний PIT-pass с последним admission: ранний checkpoint разрешал contract defaults, а аудит 04:21 применяет текущий контракт с обязательным source-timed `event_format`; поле отсутствует/не подтверждено у 4,379/4,379 примеров, поэтому сертификация 0/4,379 корректна и допуск остаётся blocked. Сопоставление: [`tennis_pit_contract_default_reconciliation_2026-10-22_0540.json`](docs/research/tennis_pit_contract_default_reconciliation_2026-10-22_0540.json), SHA-256 `e8593a0d6c38a9be374443065bda7c6eb4e0bf7cc4058ae708482d4d03cd6419`.
- [x] Пересчитать полный admission после новых PBP: отчёт [`tennis_label_audit_2026-10-22_0421.json`](docs/research/tennis_label_audit_2026-10-22_0421.json), generated `2026-10-22T04:21:23.299892Z`, SHA-256 `1cd0314097c8df1e6ee8961e766d757e696ec0eb29c73462f99d50ba35ec0e73`. Код, образ и output hashes зафиксированы в [`tennis_admission_execution_2026-10-22_0421.json`](docs/research/tennis_admission_execution_2026-10-22_0421.json). Свежий manifest [`tennis_training_manifest_97bd6ddae120.json`](docs/research/tennis_training_manifest_97bd6ddae120.json), dataset digest `97bd6ddae120b0b7bc08aa1fda073d405586abebbdd49ab95414eb37d1720b2c`, SHA-256 `63af0ff098c411965aea04dbfdf875c19b7b5b8f0a960d906931f55e0b6633cd`; provenance [`tennis_training_examples_8cc2c1b16a7f.jsonl`](docs/research/tennis_training_examples_8cc2c1b16a7f.jsonl), 4,379 examples, SHA-256 `27a6f1bc2a76ca241e384fba33cb4c859820caefd1ff26a735011e381703362c`. Manifest содержит 2,421 целый матч (1,936 development / 485 sealed holdout); train/holdout непересекаются и упорядочены, `holdout_quality_metrics_opened=false`. Gate `blocked`: по P/G/S causal train = 0 во всех четырёх folds, validation = 2/3/1/0, holdout = 10/10/9; у match train = 1,137/1,323/1,474/1,698, validation = 194/194/193/194, holdout = 485. PIT-сертификация 0/4,379: `event_format` отсутствует к `as_of` у всех примеров. Порог 500/100/200 не снижался.
- [x] Снять live-contour snapshot после neural rollout: [`tennis_live_forecast_availability_2026-10-22_0419.json`](docs/research/tennis_live_forecast_availability_2026-10-22_0419.json), 04:19:33 UTC. 21 live event, ни у одного нет двух доступных профилей; point-history: 14 `waiting_players`, 4 `not_queued`, 3 `retry`. Девять сохранённых прогнозов имеют только прежние timestamps, после рестарта neural новый forecast не создан; поэтому проверка deployed inference остаётся открытой.
- [x] Устранить источник долгой DB-транзакции при повторе admission: обнаружен orphan `neurobet_dataset_refresh` с несовместимым образом (импорт `features.selection` завершался ошибкой; контейнер перезапускался), его PID оставил `idle in transaction` более 1 ч 50 мин. Контейнер остановлен и оставлен выключенным; активная транзакция закрыта. Текущий `docker-compose.yml` не определяет такой сервис, поэтому старый runtime не возобновлялся. Полный audit завершился; модельное обучение не запускалось.
- [x] В live parser передавать только явно присутствующий Fonbet `best_of` из допустимых значений 3/5; не выводить формат из турнира, пола участников или текущего счёта. Collector image `sha256:c7f524583750977d198e8bb80f21fd7040758a43b9c51e6c743d2031abee8e57`, полный `tests/collector/test_collector.py` прошёл и сервис healthy после rollout. Runtime sample не содержал ни одного такого значения, поэтому это изменение сохраняет неизвестность, а не закрывает PIT gate.
- [x] Проверить сквозное восстановление одной live PBP заявки после ручного подтверждения профилей: две identity связи сохранены append-only вместе с источниками; Flashscore match `A5F0BCMP` сопоставлен, PBP refresh завершён, API вернул 242 события/9 версий по трём сетам. Worker healthy. На queue snapshot 05:57 — 57 строк, 22 `waiting_players`, 3 `completed`, `overdue_pbp=1`; две profile refresh jobs этого события оставались queued. Доказательства и score/as_of сверка: [`tennis_live_pbp_after_identity_2026-10-22_0552.json`](docs/research/tennis_live_pbp_after_identity_2026-10-22_0552.json), [`tennis_live_pbp_queue_2026-10-22_0555.json`](docs/research/tennis_live_pbp_queue_2026-10-22_0555.json), [`tennis_live_pbp_source_review_2026-10-22_0557.json`](docs/research/tennis_live_pbp_source_review_2026-10-22_0557.json), SHA-256 `e88789a9e2543ae226dbd64a78502dcb2771f875547c0abe8897006d16664101`. Это закрывает один runtime-сценарий, но не общий сбор, очередь/throughput, проверки 20 матчей или admission.
- [x] Сопоставить свежесть очереди и источник во время live refresh: на 06:00 Fonbet был на TB 5:1, последняя доступная PBP версия (05:56:35) заканчивалась на TB 3:0, а job ещё держал lease. `overdue_pbp=0` при активном задании не означает, что PBP догнал live score. Снимок и hash: [`tennis_live_pbp_refresh_lag_2026-10-22_0600.json`](docs/research/tennis_live_pbp_refresh_lag_2026-10-22_0600.json), SHA-256 `dd6796a028ba30f8db0dc88ce3ca4683570f34e4046c8419ba528c747c7b5bcb`.
- [x] Проверить восстановление PBP после отставания: последняя версия, доступная в 06:00:39, дошла до TB 5:1; Fonbet показывал 5:1 перед её доступностью, а в 06:01:21 уже перешёл к 5:2. Отчёт [`tennis_live_pbp_refresh_recovery_2026-10-22_0601.json`](docs/research/tennis_live_pbp_refresh_recovery_2026-10-22_0601.json), SHA-256 `512f2a40644e80ee7d8f66d35eac9eb022630becca1167f172677e98ea18b6b7`. Свежесть восстановилась до короткого lag, но terminal result и долгосрочная задержка ещё не проверены.
- [x] Зафиксировать следующий runtime timeout: в 06:03 Fonbet показывал TB 6:3, PBP не обновился после 06:01:45 и перешёл в retry из-за `SET_3_NAVIGATION_TimeoutError`; новый запуск отложен до 06:18:24. Worker healthy, но job не считается overdue во время retry и линия отстаёт. Отчёт [`tennis_live_pbp_refresh_retry_2026-10-22_0603.json`](docs/research/tennis_live_pbp_refresh_retry_2026-10-22_0603.json), SHA-256 `38fa6df939a22909c686b712e9905e362b9f8b68f0d5ac7e38fd048deae64487`.
- [x] Проверить первые 13 интервалов Phase 53 soak: до 06:12 queue/event API ответили 13/13 раз, все <2 секунд, worker здоров. Queue 57 jobs, 24 `waiting_players`, 5 retry, 3 completed, 3 running. Рассматриваемый event после пропуска свежих Fonbet snapshots сменил API mode на `prematch`; PBP по-прежнему в retry до 06:18:24, две profile jobs queued. Завершённый интервал и итоговые latency/health приведены в отчёте Phase 53 выше.
- [x] Снизить PostgreSQL memory settings под 2-GiB cgroup и проверить рост Flashscore browser capacity. Postgres healthy; эффективные настройки `shared_buffers=768MB`, `work_mem=8MB`, `maintenance_work_mem=256MB`, `effective_cache_size=1536MB`; RSS оставался ниже 1 GiB. Проба browser processes 2→3 дала worker peak 1.72 GiB / 2 GiB, поэтому третий slot откатан; на финальной конфигурации 2 processes три контрольных среза healthy, peak RSS 1006 MiB. Остаются один background lane плюс один зарезервированный live-PBP lane. Артефакты: [`flashscore_capacity_postchange_2026-10-22.json`](docs/research/flashscore_capacity_postchange_2026-10-22.json), [`flashscore_capacity_observation_2026-10-22.jsonl`](docs/research/flashscore_capacity_observation_2026-10-22.jsonl). 30-минутный soak после финального rollback остаётся открытым.
- [x] Зафиксировать текущую причину задержек очереди: на snapshot 07:19:56 worker healthy, без cooldown; `waiting_players=50` (25 PBP и 25 match lookup с отсутствующими source IDs), `retry=15` (13 `SEARCH_COMPLETED_NO_MATCH`), `queued=32`; самый старый queued archive — 104,064 с. Поэтому дополнительные browser lanes помогают только исполняемым заданиям; они не устраняют ожидание ручной/надёжной identity link и повторные source misses. Диагностика [`flashscore_queue_diagnostic_2026-10-22.json`](docs/research/flashscore_queue_diagnostic_2026-10-22.json), SHA-256 `eb6e74be5693d6866fa7be0e66175da57502062be026aa78429eacbf0d1e60e9`.
- [x] Переснять причины ожидания после пробного увеличения worker до 3 browser processes: snapshot 15:20:59 UTC — 2,675 PBP `waiting_players` (2,507 `HISTORICAL_FLASHSCORE_MATCH_NOT_INDEXED`, 169 без player source ID); 674 player jobs queued и 973 retry, из retry 867 `SEARCH_COMPLETED_NO_MATCH`. Оба глобальных source slots в этот момент свободны. Это подтверждает, что большинство текущего backlog ожидает identity/match indexing, а не свободные браузерные процессы; throughput-сравнение ждёт 30-минутного soak. Артефакт [`flashscore_queue_diagnostic_2026-10-22_1521.json`](docs/research/flashscore_queue_diagnostic_2026-10-22_1521.json).
- [ ] Повторная capacity-проба по просьбе пользователя остановлена до 30 минут: 4 browser processes (2 PBP + 2 background), discovery 5 с, подготовка игроков раз в 60 с. На кратком срезе Research и worker были healthy, PBP достиг 2 активных заданий, dispatch интервалы 7–10 с; worker достиг 1.553 GiB / 2 GiB и 149.92% CPU при лимите 1.5 vCPU, Redis — 511.8 MiB / 512 MiB. Постоянный рост завершений не доказан, очередь менялась вместе с eligible матчами (68 queued, 80 waiting_players, 14 retry в одном срезе). Вернул `FLASHSCORE_BROWSER_PROCESSES=2`; ускоренный discovery и интервал подготовки 60 с оставлены. После отката Research/worker healthy, API 200; в срезе 10:01 eligible matches 0, поэтому размер очереди 8 не сравнивается с предыдущим snapshot. PostgreSQL: 875 MiB / 2.5 GiB, 12/100 соединений, 0 lock waiters; увеличивать лимит БД не требуется. Артефакт [`flashscore_capacity_trial_2026-10-22_0956.json`](docs/research/flashscore_capacity_trial_2026-10-22_0956.json). Повторный capacity soak остаётся открытым до появления запаса ресурсов и доказанного прироста throughput.
- [x] Зафиксировать актуальный capacity snapshot после запроса о дополнительных слотах: Flashscore worker `1.092/2 GiB`, CPU `148.50%` при квоте `1.5 vCPU`; PostgreSQL `1.313/2.5 GiB`, CPU `54.88%`, 24/100 соединения, cache hit `97.94%`, размер БД `6278 MB`; follow-up не нашёл блокируемых backend-процессов. Redis `505.6/512 MiB`, collector `485.8/512 MiB`. Сетевой Flashscore gate (2 слота) был свободен, а в census 11:03 `2,628/2,952` PBP задач ждали индексацию матча или source player ID; дополнительная browser capacity эти состояния не снимает. Ещё browser slots не выкатывались: Flashscore CPU уже достигает квоты, а предыдущая 4-slot проба достигла `1.553/2 GiB` и `149.92%` CPU без доказанного устойчивого throughput. Snapshot [`runtime_capacity_snapshot_2026-10-22_1113.json`](docs/research/runtime_capacity_snapshot_2026-10-22_1113.json), SHA-256 `024674ddfe0a96a923ca3c147828f7fdd9322378f1cb19c2ffff158b028d8b53`.
- [x] Повторно проверить capacity после запроса о Flashscore slots: worker с двумя профилями использовал `975.6 MiB/2 GiB` и `145.6%` CPU при квоте `1.5 vCPU`; оба job слота были заняты. В `waiting_players`: 2,458 задач ждали индексации матча, 205 — source player ID. Слоты не увеличивать до появления CPU запаса и доказанного throughput. В PostgreSQL `995.4 MiB/2.5 GiB`, но Docker `/dev/shm` был только 64 MiB: параллельный VACUUM запросил 130 MB и завершился ошибкой. Добавлен `shm_size: 512m`; после пересоздания контейнер healthy. `VACUUM (ANALYZE, PARALLEL 0)` завершился, удалил 8,621 мёртвую строку и 170,167 мёртвых TID, статистика обновлена. Snapshot [`runtime_capacity_snapshot_2026-10-22_1323.json`](docs/research/runtime_capacity_snapshot_2026-10-22_1323.json), SHA-256 `e8e1cba40c758e8d77b79bed3a7ccff401eb6fec82138700b1ca190af1a9cdbe`.
- [x] Повторить единый read-only admission audit после capacity change и migration `032_metric_identity_provenance`. Свежий отчёт [`tennis_label_audit_2026-10-22_0808.json`](docs/research/tennis_label_audit_2026-10-22_0808.json), generated 08:08:45 UTC, SHA-256 `3a44d7d986f37f88fe274fc9e54503f8f20d679da8466fdf27530fa6dc85f7c3`; execution, source/output hashes [`tennis_admission_execution_2026-10-22_0808.json`](docs/research/tennis_admission_execution_2026-10-22_0808.json). Manifest [`tennis_training_manifest_8ad4cb858bd2.json`](docs/research/tennis_training_manifest_8ad4cb858bd2.json), 2,455 whole matches (1,964 development / 491 sealed holdout); provenance [`tennis_training_examples_6859aa39a3ab.jsonl`](docs/research/tennis_training_examples_6859aa39a3ab.jsonl), 4,664 examples. Admission `blocked`: point/game/set labelled 41/41/40; causal train 0 in all four folds, validation 2/2/1/0, holdout 13 each; match 2,470 labelled, train 1,157/1,329/1,476/1,711, validation 196/197/197/196, holdout 491. PIT 0/4,664 because source-timed `event_format` is unavailable. Fixed thresholds unchanged; `holdout_quality_metrics_opened=false`.
- [ ] Продолжить сбор доказуемых источников PBP/формата и спортивных признаков, закрыть оставшиеся runtime/ручные source-review гейты из Phase 39–53 и пересчитать допуск. Свежий read-only census [`tennis_live_pbp_ingestion_2026-10-22_1102.json`](docs/research/tennis_live_pbp_ingestion_2026-10-22_1102.json), SHA-256 `41b9036ef3f048e1ffaec9f3af8441828da6357336577ef47555ddc266c86b45`: 23 non-retrospective source matches / 211 versions / 9,024 point states; +1 match, +44 versions и +2,707 states к 10:04 UTC. Всего 1,263 versions и 27,570 point states по 88 distinct source matches. У 3,758 Fonbet tennis events нет явного `best_of` и `best_of_available_at`; очередь содержит около 2,951 job, из них 2,628 `waiting_players`; детализация по причинам: 2,322 `HISTORICAL_FLASHSCORE_MATCH_NOT_INDEXED`, 306 `PLAYER_SOURCE_IDS_MISSING`, а 128 из `retry` имеют причину `unmatched`. Между двумя read-only SQL-срезами очередь изменилась на одну строку (2,951→2,952), поэтому это точечный census, не throughput soak. Детали [`flashscore_queue_diagnostic_2026-10-22_1103.json`](docs/research/flashscore_queue_diagnostic_2026-10-22_1103.json), SHA-256 `79da1c2bd730cb16ad3ba005e94ba7c7593e488974af81743502503c538a134b`; source-keyed refresh jobs — 0. Новые PBP ещё не дали финальных меток. Последний полный audit [`tennis_label_audit_20261022T102658053244+0000.json`](docs/research/tennis_label_audit_20261022T102658053244+0000.json), SHA-256 `71d8fa252c73819336498ac97762b0d0ec34c951a93d2041fd69ee730c5ba564`; manifest [`tennis_training_manifest_1f4c3aef329c.json`](docs/research/tennis_training_manifest_1f4c3aef329c.json), 2,488 whole matches (1,990 development/498 sealed holdout), dataset digest `1f4c3aef329ce72332ee5e194890c097ccc49d12251e81fa54d6f1ef2e0851cb`, SHA-256 `df27dd45225d8dc58b415f2ed3725490a378d7fefad5bf9a33e559b2143efedc`; provenance [`tennis_training_examples_c7e3c68857dd.jsonl`](docs/research/tennis_training_examples_c7e3c68857dd.jsonl), 4,826 examples, SHA-256 `867d2915732c2f1ca2334d674b208a1ae747a5ba80b04478643d043e3dfcf524`. Execution [`tennis_admission_execution_2026-10-22_1059.json`](docs/research/tennis_admission_execution_2026-10-22_1059.json), SHA-256 `dcc703d68f5a22cfc7945dc7c7ed2b25623ca54fb8bb70e3c5a8dcb029513362`. Gate remains `blocked`: P/G/S labelled matches 44/44/43; effective causal train 0 in every fold; validation P/G/S 4/1/2/1 per fold and holdout 14 each; match validation 199/fold and holdout 498 but causal train also 0 after temporal purge. Feature PIT 0/4,826 because `event_format` has no source-time. Holdout quality remains unopened; do not train or register SHADOW before unchanged 500/100/200 and PIT gates pass.
- [x] Повторить полный read-only admission audit после новых финальных наблюдений PBP; сохранить отчёт [`tennis_label_audit_20261022T1148Z.json`](docs/research/tennis_label_audit_20261022T1148Z.json), SHA-256 `d7952dd4874b8a13d41cffd3706e09391513e555f5031e7bef7ae22e30f91ae4`, manifest [`tennis_training_manifest_3e34b4c7eb7d.json`](docs/research/tennis_training_manifest_3e34b4c7eb7d.json) (2,518 матчей: 2,014 development / 504 sealed holdout; dataset digest `3e34b4c7eb7d47945ac50269c45099ed4ba8fc0dc02a68fb0896f26e54067dd1`) и provenance [`tennis_training_examples_735be4b2dbdf.jsonl`](docs/research/tennis_training_examples_735be4b2dbdf.jsonl), 5,062 примера, SHA-256 `cd12ad648296a717bb9937e0673a0e6d5cec8e0c71197dee11cf7be650945ffa`. Цели имеют 48/48/47/2,533 размеченных матчей; после temporal purge train — 0 в каждом fold, P/G/S validation — 4/1/2/1, match validation — 202/201/201/202, holdout — 15/15/15/504. Feature PIT `0/5,062`: `event_format` без source-time во всех примерах. Result provenance: 2,241 official metadata, 277 exact prior snapshots, 143 unknown-time исключены. Gate остаётся `blocked`; holdout quality metrics не открыты, обучение и SHADOW не запускались. Execution и хеши [`tennis_admission_execution_2026-10-22_1148.json`](docs/research/tennis_admission_execution_2026-10-22_1148.json), SHA-256 `0ea2190c6e626d07f441db46fa11071cfec88b2eb3c495228bdd1415c1769a1a`.
- [x] Обновить read-only PBP/формат census после audit cutoff: [`tennis_live_pbp_ingestion_2026-10-22_1228.json`](docs/research/tennis_live_pbp_ingestion_2026-10-22_1228.json), SHA-256 `78e49e2f545e450934839bbd592f9e2d7d784ba696b911ab6ad06f230b7db926`. История содержит 100 source matches, 1,431 versions и 33,947 point states; 27 non-retrospective matches / 288 versions / 13,614 states. У 3,791 Fonbet tennis events (3,450 с numeric ID) по-прежнему 0 `best_of` и 0 source-time. PBP queue: 2,642 `waiting_players` (2,317 historical match не индексирован, 325 source player ID отсутствует), 198 retry, 76 queued; source-keyed refresh jobs — 0. После cutoff 11:48 UTC ещё шесть source matches получили новые версии, три связанных матча завершились, у двух подтверждён side order; их outcome evidence не входит в frozen manifest и будет проверяться следующей as-of сборкой.
- [x] Обновить admission после этих новых outcomes: cutoff аудита `12:36:04Z` включает завершённые к нему подтверждённые наблюдения; полный manifest и provenance пересозданы. Результат и актуальные количественные гейты приведены выше; пороги и исключение неизвестного времени не менялись.
- [x] Повторить полный read-only admission audit после накопления данных до `2026-10-22T13:27:17Z`; отчёт [`tennis_label_audit_20261022T1326Z.json`](docs/research/tennis_label_audit_20261022T1326Z.json), SHA-256 `44bd02cb73ba43673dfd413e9363c54a843913184f31c65560c492f0f1919c9d`; execution [`tennis_admission_execution_2026-10-22_1326.json`](docs/research/tennis_admission_execution_2026-10-22_1326.json), SHA-256 `5d0928311a5d1498e4e60095cc486efe670dca1cd91d20668b9c2cea27c69334`. Manifest [`tennis_training_manifest_49c609244205.json`](docs/research/tennis_training_manifest_49c609244205.json): 2,552 whole matches (2,041 development / 511 sealed holdout), dataset digest `49c609244205d9a155c005e452757e1ea67af1b71c4717be808b1fb3294c8e5e`; provenance [`tennis_training_examples_05fe1c44b9f0.jsonl`](docs/research/tennis_training_examples_05fe1c44b9f0.jsonl): 5,519 examples, SHA-256 `62bac10277fc832a14f13d126091e97c3797dc81ee7ac552247301a76015dfec`. Point/game/set/match labels: 54/54/54/2,567. Causal train остаётся 0 в каждом fold для всех целей; P/G/S validation — 4/2/1/1, holdout — 17 каждый; match validation — 204/204/205/204, holdout — 511. `event_format` не подтверждён к `as_of` у 5,519/5,519 примеров, поэтому feature PIT `0/5,519`; serve inputs все contract-defaulted, player metrics source-observed у 665 примеров, surface остаётся явным unknown. Result provenance: 2,275 official metadata, 277 exact prior snapshots, 143 unknown-time исключены. PBP: 1,521 документа / 105 source matches / 8,618 confirmed outcomes. Gate `blocked`; причины и неизменные пороги 500/100/200 зафиксированы в отчёте; holdout quality не открывался, обучение и SHADOW не запускались.
- [x] Ускорить PBP fallback по дате: для lookup в `collect_event_point_history` добавлен online составной индекс `(source, played_at)`, Alembic `034_ext_tennis_source_played`. До изменения план проходил индекс `source` и фильтровал `played_at`; после изменения `EXPLAIN ANALYZE` использует оба условия в `Index Cond`, вернул 151 строку за 1.273 ms (154 shared-buffer hits). Индекс valid/ready, размер 13 MB; worker продолжает dispatch, браузерные слоты остались равны двум. Доказательства [`flashscore_played_at_index_2026-10-22.json`](docs/research/flashscore_played_at_index_2026-10-22.json), SHA-256 `77c70e466b2872ca7746bbf1401427f57873c7ff4877bd79bfbcd087075d5341`.
- [x] Снять read-only PBP census после admission cutoff 13:27:17Z: [`tennis_live_pbp_ingestion_2026-10-22_1429.json`](docs/research/tennis_live_pbp_ingestion_2026-10-22_1429.json), SHA-256 `7ca4ccb5c624f4c61315d74e792d2cf9efdcb8d44c6bb5f833ecec17ca0b023c`. Всего 1,587 версий / 37,702 point states / 107 source matches; 31 non-retrospective source matches / 346 версий / 15,896 состояний. После 13:27 manifest добавились 66 версий и два source matches; они не входят в frozen audit. Очередь `waiting_players=2,675`: 2,484 ждут индексации матча, 191 — source player ID; source-keyed jobs 0. Следующий admission пересчитать после дальнейшего накопления подтверждённых исходов.
- [x] Проверить позднее состояние ранее выбранного события `69155384`: Flashscore match `h8LO17tg` связан с событием и оба профиля доступны, но повтор PBP завершился `PBP_TAB_NOT_PUBLISHED`/`point_history_tab_missing`, 0 событий; версия помечена retrospective, поэтому не является live evidence или PIT-меткой. На 15:18 UTC job оставлен в retry согласно backoff; отсутствие документа не закрывает требование по этому событию.

### Диспетчер очереди Flashscore и отображение попыток — 2026-10-23

- [x] Ограничить фонового worker одним одновременно захваченным заданием для профиля, поиска матча или полного архива; live PBP остаётся на отдельном executor. Worker передаёт профильному dispatcher `limit=1`, не claim-ит архив при занятом фоне и не запускает второй archive task. `tests/research/test_flashscore_worker.py` и `test_tennis_player_dispatch_priority.py` — 12 passed; lease fencing для истёкшей попытки — 1 passed.
- [x] Добавить durable время начала попытки и показывать в очереди время ожидания только для runnable задач, текущую длительность, последнюю активность, попытки и просроченный lease. API использует `attempt_started_at`, причина старой ошибки сохраняется отдельно и не показывается как текущая у `running`.
- [x] Сохранить частые повторы полного архива (backoff не выше часа), контрольные точки и явные причины неполной загрузки; неполный ответ остаётся ошибкой источника и не становится success. Старые задания массово не сбрасывались.
- [x] Проверить regression-набор очереди и после выкладки сверить API, worker heartbeat, продвижение live PBP и корректность времён/причин на активных и архивных заданиях. Backend, Research и Flashscore worker healthy; API показывает running attempt без wait, с попытками и lease, а старый `PROFILE_TIMEOUT_PROCESS_TERMINATED` остаётся только в `last_error`. Worker продолжил выдавать live PBP dispatch с `active_count=1`; после окончания прежних lease запустил один профиль (`players=1`, `imports=0`). Frontend production build прошёл.

### Контрольное испытание Flashscore на трёх слотах — 2026-10-23

- [x] До изменения провести 30-минутный baseline с двумя слотами (103 среза), затем 30-минутный trial с тремя слотами (102 среза). Неизменный сетевой регулятор: 2 параллельных запроса и минимум 2 секунды между стартами; состав очереди различался (56 против 59 начальных элементов), совпали 18/24 profile ID, 9/12 match lookup и 9/12 PBP ID. Новые поступления учитывались отдельно.
- [x] Результаты БД за окна: успешных профилей 11→20 (+82%), новых подтверждённых Flashscore identity links 7→18 (+157%), успешных match lookup 0→1 (в trial связь пришла от нового события), новых PBP-документов 33→33, завершённых полных архивов 0→4. Архивный импорт `9c041ab6-da57-4c82-802a-b23ac39f944e` обработал сохранённый checkpoint на 219 страниц и завершился; ошибки неполной загрузки успехом не считались.
- [x] Проверить ресурсы и live: worker healthy 102/102, restart=0, OOM=0, cooldown отсутствовал; память достигала 1,908 MiB из 3 GiB, до 3 Chromium root процессов. p95 ожидания live PBP вырос с 13 до 63 секунд (+385%) при допустимом пределе +10%; `overdue_pbp` был ненулевым в 14/102 срезах против 8/103 на baseline. Условие принятия трёх слотов не пройдено.
- [x] Откатить worker к 2 browser slots, 1.5 CPU и 2 GiB RAM. Зафиксировать API/health и оставить распределение: отдельный live PBP executor и один общий background lane; queue API продолжает показывать ёмкость и занятость lanes. Полная сводка и сырые данные: [`flashscore_capacity_trial_3slots_2026-10-23.md`](docs/research/flashscore_capacity_trial_3slots_2026-10-23.md).

### Дополнительный слот для полного архива — 2026-10-23

- [x] Оставить общий browser budget на трёх executor: live PBP, подготовка текущих матчей, архив/source PBP/фоновые профили. Worker — 3 GiB RAM и 2 CPU; общий network gate — два запроса с интервалом 2 секунды и прежним cooldown. Проверка API до изменения показала 3 слота, worker healthy, backlog lane active 0–1.
- [x] Перевести профильный полный архив на HTTP-first: серверный HTML/feed и страницы результатов получает HTTPX без Chromium и browser lock; только HTTP 200 без пригодных данных один раз переводит задание на Chromium fallback. На 403/429 fallback не запускается. В архивных checkpoint сохраняются transport, fallback reason, ожидаемое и наблюдаемое число одиночных результатов и их разница.
- [x] Исправить сверку полноты: нулевой `allEventsCount` больше не теряется, отсутствие счётчика не считается совпадением, классификатор парных событий определён один раз. Парсер повышен до v9; checkpoint страниц предыдущих версий остаются пригодны для продолжения и повторного разбора.
- [x] Диагностировать поиск профиля: успешный пустой ответ отличим от кандидатов, отклонённых сопоставлением имени; PBP-причина отсутствующей вкладки остаётся отдельной от сетевого timeout. Неоднозначная личность не подтверждается автоматически.
- [ ] После выкладки провести сопоставимые 30-минутные окна на двух и трёх слотах с фиксированными ID заданий и срезом каждые 15 секунд. Три слота оставить при росте успешной подготовки минимум на 20%, продвижении архива, не менее 10 завершённых попыток подготовки в каждом окне и росте p95 ожидания live PBP не более 10%; иначе вернуть два. Досрочный откат при OOM/restart, worker выше 2.7 GiB два замера подряд, доступной памяти хоста ниже 1.5 GiB минуту либо непрерывно нездоровом worker более минуты. Сохранить отчёт с результатами и принятым числом слотов.

### Повторное использование свежей истории теннисистов — 2026-10-23

- [x] Разрешить прогнозам использовать сохранённые point-in-time метрики игрока в течение семи дней после успешного обновления; одного свежего участника достаточно, а история второго остаётся независимой и не подменяется.
- [x] Убрать постановку обновления профиля на каждый новый/завершившийся матч. Планировать отсутствующие или просроченные профили по семидневному окну, не блокируя подготовку и прогноз.
- [x] Опустить обновление профилей в фоновую категорию Flashscore после поиска матча, live PBP, архивного PBP и архива; сохранить общий rate limit/cooldown. Счёт, статус и результат матча брать из Fonbet; отдельный PBP остаётся дополнительным источником очковых событий.
- [ ] Проверить свежую историю при running/retry refresh, отказ от профиля старше семи дней в `as_of`, отсутствие повторного enqueue на новый матч, фоновый приоритет профиля и текущий источник счёта после rollout. Сравнение слотов до новой политики считать только диагностическим; для решения о ёмкости нужен новый фиксированный 2/3-slot замер.

### HTTP point-by-point Flashscore — 2026-10-23

- [x] Подтвердить HTTP feed `global.flashscore.ninja/2/x/feed/df_mh_2_{source_match_id}`: один регулируемый GET вернул 200 `text/plain` с полной накопленной лентой сетов и очков для сохранённого матча. Прямой GET HTML-вкладки проверен отдельно и не содержит разбираемой ленты.
- [x] Добавить HTTP-first загрузку, разбор сетов/геймов/розыгрышей и тай-брейка, архив raw feed и транспорт в checkpoint; одинаковый ответ не создаёт новую версию. Для HTTP 403/429 действует cooldown без браузерного повтора; обычный сетевой сбой и неразбираемый feed сохраняют Chromium fallback, а пустой HTTP 200 у live-матча ждёт следующего HTTP повтора без запуска браузера.
- [x] Регрессии URL/feed-парсера, счёта, подачи, победителей, тай-брейка, HTTP fallback и cooldown прошли вместе с worker/lease regression: 91 passed.
- [x] Для live-матча пустой HTTP 200 больше не открывает summary-страницу: задача повторяет feed по штатному интервалу; для завершённого матча прежний browser fallback сохранён. Проверки `test_tennis_flashscore.py` прошли (73 passed).
- [ ] На реально только что начавшемся и уже связанном live-матче сверить опубликованные HTTP PBP события с Fonbet и подтвердить отсутствие browser navigation; на момент проверки очередь не содержала eligible live PBP jobs.
- [x] После текущего rollout Research, worker, Backend и Neural отвечали healthy; worker сохранил два browser processes.
- [ ] На одном live и одном завершённом матче сверить число и порядок PBP событий HTTP feed с сохранённой браузерной версией, повторить HTTP запрос без изменений и убедиться, что новый документ не создаётся.
