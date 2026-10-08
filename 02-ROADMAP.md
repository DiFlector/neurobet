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

- [ ] Запустить PostgreSQL + TimescaleDB.
- [ ] Настроить volume для persistent storage.
- [ ] Создать Alembic migrations.
- [ ] Создать таблицу `sports`.
- [ ] Создать `leagues`.
- [ ] Создать `participants`.
- [ ] Создать `participant_aliases`.
- [ ] Создать `events`.
- [ ] Создать `event_state_snapshots`.
- [ ] Создать `raw_snapshots`.
- [ ] Создать `markets`.
- [ ] Создать `market_selections`.
- [ ] Создать `odds_snapshots`.
- [ ] Создать `odds_change_events`.
- [ ] Создать `feature_snapshots`.
- [ ] Создать `ml_predictions`.
- [ ] Создать `llm_decisions`.
- [ ] Создать `web_research_runs`.
- [ ] Создать `web_documents`.
- [ ] Создать `web_evidence`.
- [ ] Создать `bet_proposals`.
- [ ] Создать `bet_validation_results`.
- [ ] Создать `virtual_accounts`.
- [ ] Создать `ledger_entries`.
- [ ] Создать `bets`.
- [ ] Создать `bet_settlements`.
- [ ] Создать `model_versions`.
- [ ] Создать `training_runs`.
- [ ] Создать `training_datasets`.
- [ ] Создать `experiment_results`.
- [ ] Создать `audit_log`.
- [ ] Настроить индексы по `(event_id, observed_at)`.
- [ ] Настроить индексы по `(sport_code, observed_at)`.
- [ ] Настроить индексы для event lookup по source ID.
- [ ] Настроить Timescale hypertables.
- [ ] Настроить compression policy.
- [ ] Настроить retention policy через конфигурацию.

### Acceptance

- [ ] Migration с нуля создает всю схему.
- [ ] Restart PostgreSQL сохраняет данные.
- [ ] Запрос timeline одного event остается быстрым на тестовом датасете.

---

# PHASE 3 — Redis Streams

- [ ] Запустить Redis.
- [ ] Создать streams `fonbet.raw`, `fonbet.events`, `fonbet.state`, `fonbet.odds`.
- [ ] Создать streams `features.ready`, `ml.predictions`.
- [ ] Создать streams `research.requests`, `research.results`.
- [ ] Создать streams `llm.requests`, `llm.results`.
- [ ] Создать streams `bet.proposals`, `bet.validated`, `bet.executed`, `bet.settled`.
- [ ] Создать stream `training.jobs`.
- [ ] Реализовать message envelope.
- [ ] Реализовать idempotency key.
- [ ] Реализовать consumer groups.
- [ ] Реализовать retry policy.
- [ ] Реализовать dead-letter handling.

### Acceptance

- [ ] Один message не создает два одинаковых database action при повторной доставке.
- [ ] Ошибка worker не теряет message.

---

# PHASE 4 — FON.BET collector foundation

- [ ] Создать collector service.
- [ ] Подключить Playwright/Chromium.
- [ ] Создать browser lifecycle manager.
- [ ] Создать configurable page URLs.
- [ ] Реализовать graceful shutdown.
- [ ] Реализовать `random.uniform(5, 10)` polling.
- [ ] Реализовать concurrency limit.
- [ ] Реализовать timeout.
- [ ] Реализовать retry/backoff.
- [ ] Реализовать circuit breaker.
- [ ] Реализовать structured logging.
- [ ] Реализовать snapshot hashing.
- [ ] Создать raw snapshot writer.
- [ ] Создать MinIO bucket `raw-snapshots`.
- [ ] Записывать `collector_version`.
- [ ] Записывать `collected_at`.
- [ ] Сохранять URL/page type.
- [ ] Реализовать parser tests на сохраненных fixtures.

### Acceptance

- [ ] Collector может несколько часов работать без uncontrolled browser process growth.
- [ ] Ошибка parser одной карточки не останавливает весь collector.
- [ ] Каждая сохраненная запись имеет точный UTC timestamp.

---

# PHASE 5 — First sport adapter: Tennis

- [ ] Создать `sports/core` interfaces.
- [ ] Создать sport registry.
- [ ] Создать `tennis` adapter.
- [ ] Извлекать event ID.
- [ ] Извлекать tournament / surface (хард, грунт, трава).
- [ ] Извлекать players (Player A, Player B).
- [ ] Извлекать scheduled start.
- [ ] Извлекать live/prematch state.
- [ ] Извлекать tennis score (sets, games, current game points: 0, 15, 30, 40, AD).
- [ ] Извлекать server (кто сейчас подает).
- [ ] Извлекать tennis stats (aces, double faults, break points).
- [ ] Извлекать доступные markets (MVP: `match_winner`).
- [ ] Извлекать selections (`player_a`, `player_b`).
- [ ] Извлекать odds.
- [ ] Извлекать market status/suspension.
- [ ] Нормализовать имена игроков.
- [ ] Сохранить canonical event.
- [ ] Сохранить state snapshot (hierarchical: match -> set -> game -> point).
- [ ] Сохранить odds snapshot.
- [ ] Реализовать result parser.
- [ ] Добавить parser fixture tests.

### Acceptance

- [ ] Один реальный/сохраненный live event корректно проходит `raw → event → state → odds`.
- [ ] Повторный snapshot не создает ложные изменения.
- [ ] Изменение odds создает новую историческую запись.

---

# PHASE 6 — Generic sports layer

- [ ] Перенести event lifecycle в sport-independent core.
- [ ] Оставить sport-specific state в adapter.
- [ ] Убрать football-specific assumptions из backend.
- [ ] Убрать football-specific assumptions из bet-manager.
- [ ] Убрать football-specific assumptions из DB contracts.
- [ ] Создать generic participant model.
- [ ] Создать market/selection generic model.
- [ ] Создать sport-specific label provider.

### Acceptance

- [ ] Backend умеет работать с неизвестным sport_code.
- [ ] Неизвестный спорт корректно попадает в `UNSUPPORTED` и не ставит.

---

# PHASE 7 — Historical data quality

- [ ] Создать data-quality jobs.
- [ ] Проверять duplicate events.
- [ ] Проверять time order.
- [ ] Проверять отрицательные/невозможные odds.
- [ ] Проверять невозможные изменения score.
- [ ] Проверять clock regressions.
- [ ] Проверять missing observations.
- [ ] Проверять orphan odds.
- [ ] Проверять события без result.
- [ ] Добавить data-quality dashboard.
- [ ] Добавить quality score на event.

### Acceptance

- [ ] Можно получить отчет о качестве накопленной истории.
- [ ] Bad rows не удаляются молча; они помечаются и остаются аудируемыми.

---

# PHASE 8 — Feature engineering

- [ ] Реализовать common feature base.
- [ ] Реализовать football feature builder.
- [ ] Реализовать rolling windows 5/10/30/60/300 sec.
- [ ] Реализовать odds delta.
- [ ] Реализовать odds velocity.
- [ ] Реализовать volatility.
- [ ] Реализовать line movement.
- [ ] Реализовать no-vig probability.
- [ ] Реализовать score differential.
- [ ] Реализовать match clock/time-to-start.
- [ ] Реализовать suspension frequency.
- [ ] Добавить missing indicators.
- [ ] Версионировать feature set.
- [ ] Сохранять `feature_cutoff_timestamp`.

### Critical tests

- [ ] Feature builder никогда не запрашивает snapshot после `feature_cutoff_timestamp`.
- [ ] Feature values воспроизводимы при повторном build.
- [ ] Один и тот же event не смешивается с другим event.

---

# PHASE 9 — Baseline ML

- [ ] Создать dataset builder.
- [ ] Реализовать point-in-time feature join.
- [ ] Создать labels для первых поддерживаемых рынков.
- [ ] Сделать Logistic Regression baseline.
- [ ] Сделать LightGBM/XGBoost baseline.
- [ ] Добавить probability calibration.
- [ ] Добавить model artifact serialization.
- [ ] Создать `model_versions`.
- [ ] Создать `training_runs`.
- [ ] Сохранять dataset version.
- [ ] Сохранять git commit.
- [ ] Сохранять feature version.
- [ ] Сохранять hyperparameters.
- [ ] Считать log loss.
- [ ] Считать Brier.
- [ ] Считать calibration metrics.

### Acceptance

- [ ] Можно одной командой воспроизвести training run.
- [ ] Prediction содержит model version и feature version.

---

# PHASE 10 — Walk-forward backtester

- [ ] Реализовать chronological split.
- [ ] Реализовать rolling/walk-forward windows.
- [ ] Добавить purge/embargo при необходимости.
- [ ] Запретить random split в production training.
- [ ] Реализовать simulated decision timestamps.
- [ ] Реализовать historical odds lookup as-of timestamp.
- [ ] Реализовать stale odds behavior.
- [ ] Реализовать execution latency simulation.
- [ ] Реализовать suspension handling.
- [ ] Реализовать virtual stake deduction.
- [ ] Реализовать settlement.
- [ ] Считать ROI.
- [ ] Считать P&L.
- [ ] Считать max drawdown.
- [ ] Считать turnover.
- [ ] Считать exposure.
- [ ] Считать результаты по sport/market/odds/edge buckets.

### Critical acceptance

- [ ] Backtest не может использовать данные после decision timestamp.
- [ ] Backtest воспроизводим по seed/config version.
- [ ] Повторный запуск на одном dataset дает одинаковые результаты.

---

# PHASE 11 — Virtual bankroll + immutable ledger

- [ ] Создать virtual account.
- [ ] Реализовать initial balance.
- [ ] Реализовать ledger.
- [ ] Запретить прямой balance UPDATE.
- [ ] Реализовать available balance.
- [ ] Реализовать exposed balance.
- [ ] Реализовать virtual BET_PLACED.
- [ ] Реализовать WIN.
- [ ] Реализовать LOSS.
- [ ] Реализовать VOID.
- [ ] Реализовать reconciliation job.
- [ ] Добавить ledger audit tests.

### Acceptance

- [ ] Баланс можно пересчитать только из ledger.
- [ ] Нельзя создать ставку при недостаточном balance.

---

# PHASE 12 — Bet Manager

- [ ] Создать отдельный service.
- [ ] Реализовать `BetProposal` schema.
- [ ] Реализовать event validation.
- [ ] Реализовать market validation.
- [ ] Реализовать selection validation.
- [ ] Реализовать odds freshness validation.
- [ ] Реализовать state freshness validation.
- [ ] Реализовать odds match/slippage validation.
- [ ] Реализовать duplicate validation.
- [ ] Реализовать balance validation.
- [ ] Реализовать exposure limit.
- [ ] Реализовать stake limit.
- [ ] Реализовать daily/session loss limit.
- [ ] Реализовать model confidence threshold.
- [ ] Реализовать minimum edge threshold.
- [ ] Реализовать sport-specific validation.
- [ ] Создать explicit reject reasons.
- [ ] Реализовать simulation executor.

### Acceptance

- [ ] ML/LLM не могут напрямую создать bet.
- [ ] Every accepted proposal имеет полный validation trail.
- [ ] stale odds всегда отвергаются.

---

# PHASE 13 — Settlement

- [ ] Реализовать завершение event.
- [ ] Реализовать canonical result.
- [ ] Реализовать market settlement.
- [ ] Реализовать win/loss/void.
- [ ] Реализовать settlement transaction.
- [ ] Сделать settlement idempotent.
- [ ] Сделать reconciliation.
- [ ] Реализовать `SETTLEMENT_REVIEW_REQUIRED`.

### Acceptance

- [ ] Один bet нельзя settle дважды.
- [ ] После restart worker settlement не теряется.

---

# PHASE 14 — Local LLM

- [ ] Добавить `llm` container.
- [ ] Поддержать GGUF model path.
- [ ] Проверить CPU inference.
- [ ] Проверить optional GPU offload на GTX 1050 Ti.
- [ ] Добавить fallback на CPU.
- [ ] Настроить context size.
- [ ] Настроить temperature/decoding config.
- [ ] Зафиксировать prompt version.
- [ ] Реализовать JSON-only output.
- [ ] Добавить Pydantic validation.
- [ ] Добавить invalid JSON fallback.
- [ ] Записывать latency.
- [ ] Записывать model identifier.
- [ ] Не давать LLM write access к DB.

### LLM acceptance

- [ ] LLM стабильно возвращает валидный JSON на одинаковый input.
- [ ] Некорректный JSON → `INSUFFICIENT_DATA`.
- [ ] LLM не может создать virtual bet напрямую.

---

# PHASE 15 — Web Research

- [ ] Создать research service.
- [ ] Запустить search backend.
- [ ] Добавить Playwright/browser fetcher.
- [ ] Добавить HTML text extraction.
- [ ] Добавить URL normalization.
- [ ] Добавить duplicate document detection.
- [ ] Добавить content hash.
- [ ] Добавить published timestamp extraction.
- [ ] Добавить retrieved timestamp.
- [ ] Добавить freshness score.
- [ ] Добавить domain allowlist.
- [ ] Добавить timeout.
- [ ] Добавить max page count.
- [ ] Добавить max text size.
- [ ] Добавить research cache.
- [ ] Добавить research run history.
- [ ] Запретить arbitrary POST/PUT requests.
- [ ] Запретить обход CAPTCHA/anti-bot controls.
- [ ] Запретить использование research для live score authority.

### Acceptance

- [ ] Research produces compact structured evidence packet.
- [ ] Повторный запрос с тем же cache key не делает новый network fetch до истечения TTL.

---

# PHASE 16 — ML + LLM decision layer

- [ ] Создать candidate selection.
- [ ] Отбирать кандидатов по ML edge/confidence.
- [ ] Добавить research only for shortlisted events.
- [ ] Сформировать LLM input JSON.
- [ ] Запросить LLM.
- [ ] Провалидировать LLM output.
- [ ] Сохранить `llm_decisions`.
- [ ] Создать `BetProposal`.
- [ ] Передать только через bet-manager.
- [ ] Сравнить ML-only и ML+LLM стратегии.
- [ ] Считать incremental value LLM.

### Acceptance

- [ ] Можно включить/выключить LLM конфигом.
- [ ] Поведение ML-only не меняется при отключенной LLM.
- [ ] Можно отдельно backtest LLM contribution.

---

# PHASE 17 — Candidate scheduling and cost control

- [ ] Не запускать research для каждого события.
- [ ] Ввести candidate score.
- [ ] Кэшировать research.
- [ ] Кэшировать LLM input по версии snapshot.
- [ ] Ограничить LLM requests/sec.
- [ ] Ограничить browser concurrency.
- [ ] Ввести per-event research cooldown.
- [ ] Ввести per-event LLM cooldown.
- [ ] Добавить priority queue для high-edge candidates.

---

# PHASE 18 — Backend API

- [ ] Реализовать health/readiness.
- [ ] Реализовать sports endpoint.
- [ ] Реализовать events endpoint.
- [ ] Реализовать event detail.
- [ ] Реализовать timeline endpoint.
- [ ] Реализовать odds history endpoint.
- [ ] Реализовать ML predictions endpoint.
- [ ] Реализовать research endpoint.
- [ ] Реализовать bet proposals endpoint.
- [ ] Реализовать bets endpoint.
- [ ] Реализовать account endpoint.
- [ ] Реализовать ledger endpoint.
- [ ] Реализовать performance endpoint.
- [ ] Реализовать model endpoint.
- [ ] Реализовать training runs endpoint.
- [ ] Добавить pagination.
- [ ] Добавить filters.
- [ ] Добавить WebSocket/SSE live updates.

---

# PHASE 19 — Frontend

- [ ] Создать Next.js app.
- [ ] Создать live dashboard.
- [ ] Создать sports filter.
- [ ] Создать league filter.
- [ ] Создать event status filter.
- [ ] Создать event detail page.
- [ ] Показать live score/clock.
- [ ] Показать odds.
- [ ] Показать odds movement chart.
- [ ] Показать ML probability.
- [ ] Показать market implied probability.
- [ ] Показать edge.
- [ ] Показать LLM verdict.
- [ ] Показать research evidence.
- [ ] Показать validation chain.
- [ ] Показать virtual bankroll.
- [ ] Показать open exposure.
- [ ] Показать P&L.
- [ ] Показать drawdown.
- [ ] Показать model version.
- [ ] Показать event timeline.
- [ ] Добавить dark/light theme.

### Acceptance

- [ ] Dashboard обновляется без ручного refresh.
- [ ] Event detail дает возможность восстановить логику решения по timestamps.

---

# PHASE 20 — Observability

- [ ] Structured logs.
- [ ] Request IDs.
- [ ] Event IDs в каждом релевантном логе.
- [ ] Collector metrics.
- [ ] ML metrics.
- [ ] Research metrics.
- [ ] LLM metrics.
- [ ] Bet-manager metrics.
- [ ] Settlement metrics.
- [ ] Prometheus.
- [ ] Grafana.
- [ ] Error alerts.
- [ ] Collector stale-data alert.
- [ ] DB disk usage alert.
- [ ] Container restart alert.

---

# PHASE 21 — Security hardening

- [ ] Не публиковать PostgreSQL port наружу без необходимости.
- [ ] Не публиковать Redis наружу.
- [ ] Не публиковать MinIO наружу без необходимости.
- [ ] Секреты в env/secrets.
- [ ] Минимальные container capabilities.
- [ ] Non-root containers где возможно.
- [ ] Resource limits.
- [ ] Browser sandbox.
- [ ] Research URL allowlist.
- [ ] Нет arbitrary shell execution от LLM.
- [ ] Нет arbitrary database writes от LLM.
- [ ] Нет реальных ставок в default compose.
- [ ] `BET_MODE=SIMULATION` enforced on application startup.

---

# PHASE 22 — Data leakage test suite

Это обязательная часть проекта.

- [ ] Создать искусственный dataset с очевидным future signal.
- [ ] Убедиться, что feature builder его не видит.
- [ ] Проверить `cutoff_timestamp`.
- [ ] Проверить rolling windows.
- [ ] Проверить result joins.
- [ ] Проверить odds lookup.
- [ ] Проверить train/validation boundary.
- [ ] Проверить event grouping.
- [ ] Проверить backtest execution timestamp.
- [ ] Проверить research published/retrieved timestamps.

### Acceptance

- [ ] Intentional future-leak test должен падать, если код случайно начинает использовать будущее.

---

# PHASE 23 — Scientific baseline experiment

- [ ] Зафиксировать initial bankroll.
- [ ] Зафиксировать period.
- [ ] Зафиксировать sport.
- [ ] Зафиксировать markets.
- [ ] Зафиксировать minimum edge.
- [ ] Зафиксировать stake policy.
- [ ] Запустить ML-only experiment.
- [ ] Получить log loss/Brier.
- [ ] Получить ROI/P&L.
- [ ] Получить max drawdown.
- [ ] Получить calibration curve.
- [ ] Сохранить experiment result.

---

# PHASE 24 — LLM experiment

- [ ] На том же time split запустить ML + LLM.
- [ ] Не менять feature data между экспериментами.
- [ ] Не менять bankroll/risk config.
- [ ] Не менять execution latency.
- [ ] Сравнить number of bets.
- [ ] Сравнить ROI.
- [ ] Сравнить P&L.
- [ ] Сравнить drawdown.
- [ ] Сравнить calibration.
- [ ] Разбить результаты по edge buckets.
- [ ] Разбить результаты по research freshness.
- [ ] Проверить, когда LLM ошибается.
- [ ] Проверить, добавляет ли LLM независимую информацию.

### Acceptance

- [ ] LLM не объявляется улучшением проекта без сравнительного эксперимента.

---

# PHASE 25 — Shadow mode

- [ ] Реализовать shadow predictions.
- [ ] Shadow model не создает virtual bets.
- [ ] Собирает predictions параллельно active model.
- [ ] Сравнивать predictions.
- [ ] Считать drift.
- [ ] Считать performance by segment.
- [ ] Установить минимальный shadow period в конфиге.

---

# PHASE 26 — Second sport

Первым кандидатом можно сделать hockey.

- [ ] Создать `sports/hockey`.
- [ ] Реализовать adapter.
- [ ] Реализовать state schema.
- [ ] Реализовать market mappings.
- [ ] Реализовать features.
- [ ] Реализовать labels.
- [ ] Реализовать baseline model.
- [ ] Реализовать settlement tests.
- [ ] Реализовать historical fixtures.
- [ ] Подключить model registry.
- [ ] Подключить frontend.

### Acceptance

- [ ] Hockey включается только config-флагом.
- [ ] Football pipeline не изменяется из-за hockey.

---

# PHASE 27 — Third/fourth sports

- [ ] Добавить basketball.
- [ ] Добавить tennis.
- [ ] Проверить, что generic contracts не стали содержать sport-specific fields.
- [ ] Если generic model начинает ухудшать качество, выделить sport-specific storage/logic.

---

# PHASE 28 — Performance and scale

- [ ] Нагрузочный тест collector.
- [ ] Нагрузочный тест PostgreSQL.
- [ ] Нагрузочный тест Redis.
- [ ] Измерить DB rows/day.
- [ ] Измерить raw storage/day.
- [ ] Измерить LLM latency.
- [ ] Измерить research latency.
- [ ] Измерить inference latency.
- [ ] Оптимизировать indexes.
- [ ] Настроить Timescale compression.
- [ ] Настроить MinIO lifecycle.
- [ ] Добавить archive policy.
- [ ] Не хранить дублирующий raw HTML дольше заданного срока без причины.

---

# PHASE 29 — Disaster recovery

- [ ] Backup PostgreSQL.
- [ ] Backup MinIO metadata/data.
- [ ] Export model artifacts.
- [ ] Export config.
- [ ] Document restore procedure.
- [ ] Test DB restore.
- [ ] Test complete stack restart.
- [ ] Test reconstruction of balance from ledger.
- [ ] Test reconstruction of a bet decision from audit records.

---

# PHASE 30 — Final production-like simulation gate

Все пункты должны быть выполнены до любых мыслей о реальном executor.

- [ ] Collector работает стабильно.
- [ ] History не теряется.
- [ ] Data quality acceptable.
- [ ] No future leakage tests pass.
- [ ] Backtest reproducible.
- [ ] ML model calibrated.
- [ ] Bet-manager fail-closed.
- [ ] Virtual ledger auditable.
- [ ] Settlement idempotent.
- [ ] LLM JSON schema enforced.
- [ ] Research sandboxed.
- [ ] All model versions recorded.
- [ ] Monitoring available.
- [ ] Backups tested.
- [ ] Multiple sports isolated.
- [ ] System survives service restarts.
- [ ] Long-running simulation produces no balance corruption.

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
