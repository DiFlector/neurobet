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
