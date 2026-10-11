---
trigger: always_on
---

---
name: neurobet
description: "Master engineering guide for AI Agents (Cursor & Antigravity) working with the Neurobet platform: architecture, MCP tools, ML lifecycle, risk gates, and runbooks."
---

# Регламент разработчика и AI-агента Neurobet (Cursor & Antigravity)

Этот документ является **главным операционным регламентом** для AI-агентов (Cursor, Antigravity, Claude Code), работающих с кодовой базой **Neurobet**. Перед внесением изменений агент обязан ознакомиться с принципами ниже.

---

## 1. Фундаментальные принципы и запреты

### 1.1 Архитектурные табу
- ❌ **Никаких реальных ставок**: параметр `BET_MODE=SIMULATION` жестко зафиксирован в ядре. Любые попытки отправки реальных денежных транзакций блокируются на уровне старта контейнеров.
- ❌ **Никаких прямых записей в баланс**: LLM, ML-модели, Collector и Frontend **не имеют права** изменять баланс или регистрировать ставки. Единственным компонентом, создающим проводки, является `bet-manager`.
- ❌ **Запрет Data Leakage**: при вычислении признаков (feature engineering) и обучении моделей запрещено использовать информацию, зафиксированную позже момента принятия решения (`observed_at <= decision_timestamp`).
- ❌ **Спорт-независимое ядро**: логика видов спорта (теннис, футбол, хоккей и др.) должна находиться строго в `sports/<sport_code>/` и реализовывать контракт `BaseSportAdapter`. Ядро `packages/sports-core` и `services/bet-manager` не должны содержать хардкода под конкретный вид спорта.
- ❌ **Никакого неструктурированного текста**: все межсервисные сообщения обязаны валидироваться схемами Pydantic из `packages/contracts` с полем `schema_version`.

### 1.2 Главный конвейер принятия решений (Decision Pipeline)
```text
Fonbet Line/Live
       ↓
   collector (джиттер 5-10с, Playwright/API)
       ↓
 raw snapshots (MinIO S3) + Redis Streams
       ↓
 event-processor (нормализация, сопоставление команд)
       ↓
  ┌────────────────────────────────────┐
  │                                    │
  ↓                                    ↓
neural (ML вероятности & edge)     research (Web поиск, контекст)
  │                                    │
  │                                local LLM (анализ новостей & вердикт)
  │                                    │
  └─────────────────┬──────────────────┘
                    ↓
             combined verdict
                    ↓
   bet-manager (валидация 20 защитных гейтов)
                    ↓
   virtual bankroll (иммутируемый ledger в PostgreSQL)
                    ↓
  settlement worker (расчет исхода матча)
                    ↓
  feedback loop (дообучение ML & бэктесты)
```

---

## 2. Карта микросервисов и топология портов

Кластер состоит из **13 Docker-контейнеров**, развернутых через `docker-compose.yml`:

| Сервис | Контейнер | Внутренний порт | Хост-порт | Назначение |
|---|---|---|---|---|
| **PostgreSQL + TimescaleDB** | `neurobet_postgres` | `5432` | `127.0.0.1:5435` | Хранение сущностей, гипертаблицы временных рядов (snapshots), финансовый ledger |
| **Redis Streams** | `neurobet_redis` | `6379` | `127.0.0.1:6385` | Очереди событий, стримы `fonbet.*`, `ml.*`, `bet.*`, `research.*` |
| **MinIO S3** | `neurobet_minio` | `8000` | `127.0.0.1:9005` | Хранение сырых HTML/JSON снимков Fonbet и документов |
| **Backend API** | `neurobet_backend` | `8000` | `127.0.0.1:8005` | FastAPI шлюз для фронтенда, шедулера и внешних запросов |
| **Collector** | `neurobet_collector` | - | - | Сбор линии и лайва Fonbet с рандомизированным интервалом 5–10 сек |
| **Bet Manager** | `neurobet_bet_manager` | `8000` | - | Гейткипер ставок, проверка 20 гейтов риска, управление бумажным счетом |
| **Neural** | `neurobet_neural` | `8000` | - | Инференс GBDT/LogReg, бэктестер, экспорт метрик моделей |
| **Local LLM** | `neurobet_llm` | `8000` | - | Локальный инференс GGUF (NVIDIA GPU / CPU fallback), строгий JSON вердикт |
| **Research** | `neurobet_research` | `8000` | - | Поиск и парсинг новостей/травм в интернете с таймаутами и allowlist |
| **Worker** | `neurobet_worker` | - | - | Обработчик очередей Redis Streams, расчет settlement |
| **Frontend** | `neurobet_frontend` | `3000` | `0.0.0.0:8085` | Next.js дашборд (доступен по адресу `necrolich.ru/diflector/neurobet`) |
| **Prometheus** | `neurobet_prometheus` | `9090` | `127.0.0.1:9095` | Сбор метрик со всех сервисов (Section 41) и правила алертов |
| **Grafana** | `neurobet_grafana` | `3000` | `127.0.0.1:8086` | Аналитические панели мониторинга |

---

## 3. Использование MCP-сервера в Cursor и Antigravity

В проекте развернут **полноценный Model Context Protocol сервер** ([`packages/mcp-server`](file:///srv/neurobet/packages/mcp-server)), позволяющий агентам напрямую инспектировать систему без написания ad-hoc скриптов.

### 3.1 Ключевые инструменты (MCP Tools)
- **Мониторинг кластера**:
  - `neurobet_get_system_health`: статус БД, Redis, очередей и активных моделей.
  - `neurobet_query_prometheus`: выполнение любого PromQL запроса (`rate(collector_polls_total[1m])`, `sum(up)`).
- **Управление ML-моделями**:
  - `neurobet_list_models`: просмотр реестра моделей, метрик ROC-AUC и Brier score.
  - `neurobet_get_shadow_report`: оценка дрейфа в Shadow Mode (PSI по 10 бакетам, Wasserstein distance, контрфактический PnL).
  - `neurobet_register_shadow_model`: постановка кандидата в Shadow Mode.
  - `neurobet_promote_model`: перевод теневой модели в статус ACTIVE.
  - `neurobet_rollback_model`: экстренный откат модели к предыдущей версии при выявлении дрейфа.
  - `neurobet_simulate_inference`: безопасный расчет вероятностей и edge на произвольном векторе признаков (sandbox-тестирование).
- **Беттинг и аудит**:
  - `neurobet_get_bankroll_summary`: проверка баланса, свободных средств и экспозиции.
  - `neurobet_list_bet_proposals`: аудит отклоненных/принятых ставок и причин срабатывания гейтов риска.
  - `neurobet_sync_settlements`: принудительный запуск расчета завершенных ставок.
  - `neurobet_get_data_quality_report`: проверка монотонности таймлайна и отсутствия data leakage.

### 3.2 Динамические ресурсы MCP (`Resources`)
- `neurobet://system/health` — оперативный JSON-снимок состояния системы.
- `neurobet://models/registry` — полный реестр версий моделей.
- `neurobet://bankroll/status` — текущий баланс и экспозиция счета.
- `neurobet://metrics/prometheus` — сводка таргетов сбора метрик.

---

## 4. Жизненный цикл ML-модели (Training, Shadow Mode, Rollback)

Каждая модель проходит строгий регламент жизненного цикла:

```text
TRAINING → CANDIDATE → SHADOW (параллельный инференс) → ACTIVE → RETIRED
```

### 4.1 Теневой режим (Shadow Mode Engine)
1. Кандидат регистрируется через `neurobet_register_shadow_model`.
2. В цикле инференса модель запускается параллельно с активной на идентичных снимках матча.
3. Рассчитывается дрейф распределений:
   - **Population Stability Index (PSI)**:
     - `PSI < 0.10`: стабильное распределение (OK).
     - `0.10 <= PSI < 0.25`: умеренный дрейф (Warning).
     - `PSI >= 0.25`: критический дрейф (Promotion заблокирован).
   - **Расстояние Вассерштейна (Wasserstein Distance)**: мера физического смещения средних вероятностей.
4. Контрфактический PnL: расчет виртуальной доходности ставки, если бы решение принимал кандидат.

### 4.2 Гейты промоушена в статус ACTIVE
Для промоушена модель обязана удовлетворять 5 критериям:
1. `shadow_observation_days >= 7.0` (минимум 7 дней наблюдений).
2. `total_evaluations >= 100` (минимум 100 оцененных событий).
3. `probability_drift_psi < 0.20` (отсутствие критического дрейфа).
4. `counterfactual_roi >= active_roi - 0.02` (ROI не должен уступать активной модели более чем на 2%).
5. `brier_score_delta <= 0.02` (калибровка не должна деградировать).

При нарушении хотя бы одного критерия метод `neurobet_promote_model` вернет отказ, если не передан флаг `force=True`.

---

## 5. Добавление и доработка спортивных адаптеров

Логика конкретного вида спорта изолируется в папке `sports/<sport_code>/`:

```text
sports/
├── tennis/
├── football/
├── basketball/
├── hockey/
└── volleyball/
```

### 5.1 Контракт `BaseSportAdapter`
Каждый адаптер обязан реализовывать:
- `normalize_event_state(raw_data) -> CanonicalMatchState`: парсинг счета, периодов, сетов, четвертей.
- `extract_features(event_history, h2h_data) -> FeatureVector`: генерация признаков (rolling win rate, fatigue, Elo diff).
- `map_market_odds(raw_markets) -> NormalizedOdds`: стандартизация рынков (`match_winner`, `totals`, `handicap`).
- `settle_market(selection, final_score) -> SettlementResult`: определение выигрыша/проигрыша/возврата.

После написания адаптер регистрируется в `sports_core.registry.register_adapter(sport_code, adapter_instance)`.

---

## 6. Основные команды и регламент тестирования

Все операции автоматизированы через [`Makefile`](file:///srv/neurobet/Makefile):

| Команда | Описание |
|---|---|
| `make up` | Запуск всего стека контейнеров с пересборкой |
| `make down` | Корректная остановка всех контейнеров |
| `make ps` | Проверка статуса здоровья всех контейнеров |
| `make logs` | Просмотр структурированных логов всего кластера |
| `make migrate` | Применение миграций Alembic в PostgreSQL |
| `make train` | Запуск фонового обучения модели для тенниса |
| `make backtest` | Запуск walk-forward бэктеста по стратегии KELLY |
| `make test` | Запуск полного сквозного тест-сьюта платформы |
| `make test-mcp` | Запуск верификационного теста MCP-сервера (10/10 проверок) |
| `make simulation-gate` | Запуск финального симуляционного шлюза (Gate 32) |

### 6.1 Правило запуска тестов в Docker
Большинство тестов рассчитаны на запуск внутри контейнера с доступом к внутренней сети `neurobet-internal`:
```bash
docker exec -i neurobet_backend python - < tests/api/test_backend_api.py
docker exec -i neurobet_neural python - < tests/neural/test_baseline_ml.py
```
Тесты MCP-сервера запускаются на хосте напрямую, используя стандартный Python:
```bash
python3 tests/mcp/test_mcp_server.py
```

---

## 7. Runbook при нештатных ситуациях

### 7.1 Сбой сбора данных (Collector Degraded / Stale Data)
- Если Fonbet временно недоступен или блокирует запросы:
  - Collector переходит в режим ожидания с экспоненциальным backoff.
  - `bet-manager` блокирует создание новых ставок из-за несвежей линии (`LINE_STALE_REJECT`).
  - Ранее сделанные виртуальные ставки остаются на учете.
  - Агент **не должен** форсировать ставки в условиях деградировавшего коллектора.

### 7.2 Расхождение баланса (Ledger Reconciliation)
- Если сумма баланса на счете не совпадает с суммой записей проводок:
  ```bash
  curl -X POST http://127.0.0.1:8005/api/bankroll/reconcile
  ```
  Или через скрипт восстановления:
  ```bash
  python3 scripts/backup_manager.py --restore-latest
  ```

### 7.3 Критический дрейф модели (PSI Alert)
- При срабатывании алерта `ModelPredictionDriftHigh` в Prometheus:
  1. Вызовите инструмент `neurobet_get_shadow_report`, чтобы локализовать бакеты дрейфа.
  2. Если активная модель деградировала, выполните откат через `neurobet_rollback_model`.
  3. Запустите переобучение на свежем датасете через `neurobet_trigger_training`.
