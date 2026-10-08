# FONBET AI BETTING RESEARCH PLATFORM

## 0. Назначение документа

Этот документ является техническим контрактом и архитектурным заданием для coding-agent / нейросети, которая будет реализовывать проект.

Цель проекта — построить локально разворачиваемую исследовательскую платформу, которая собирает временной ряд линии и состояния матчей с FON.BET, обучает отдельные модели по видам спорта, формирует виртуальные ставки, проверяет их через независимый `bet-manager`, рассчитывает результат и накапливает историю для последующего обучения.

В проекте обязательно существует режим `SIMULATION`, в котором используется только виртуальный баланс. Реальный денежный executor в первой версии не реализуется и не должен включаться по умолчанию.

Официальный сайт FON.BET: https://fon.bet/. На текущей линии присутствуют как prematch, так и live-события и множество видов спорта; структура live-линии содержит состояние матча, счет/игровое время и коэффициенты рынков. Сайт также публикует страницу результатов завершенных событий. См. официальные страницы `/`, `/live/...` и `/results`.

Важно: перед эксплуатацией парсера необходимо отдельно проверить актуальные условия использования сайта, robots.txt, технические ограничения и допустимый способ автоматизированного доступа. Не реализовывать обход CAPTCHA, антибот-защиты, авторизации или иных защитных механизмов.

---

# 1. Главные архитектурные принципы

1. **Источник истины для live-данных — FON.BET collector.** Не использовать бесплатные спортивные API для счета/времени/коэффициентов.
2. **Другие сайты — только дополнительный текстовый research-сигнал для LLM.** Внешний web research никогда не является источником истины для live score.
3. **Каждый вид спорта изолирован.** Общие компоненты работают через абстракции, но признаки, состояние матча, рынки, labels и ML-модели принадлежат конкретному `sport_adapter`.
4. **LLM не является основной predictive model.** Числовая ML-модель рассчитывает вероятности; LLM выступает дополнительным аналитическим агентом.
5. **Никакого свободного текста между сервисами.** Межсервисные решения передаются через versioned JSON/Pydantic schemas.
6. **Каждое решение воспроизводимо.** Сохраняются timestamp, входные данные, версии моделей, feature version, odds snapshot, LLM prompt version и итоговое решение.
7. **Никакого data leakage.** При обучении разрешены только данные, существовавшие на момент `decision_timestamp`.
8. **Сначала backtest/paper trading.** Реальная интеграция с приемом ставок не является частью MVP.
9. **Иммутируемый финансовый журнал.** Баланс не изменяется прямым `UPDATE`; изменение баланса — результат ledger entries.
10. **Fail closed.** При устаревших коэффициентах, неизвестном статусе матча, ошибке валидатора, несовпадении версии данных или недоступности необходимого источника ставка отклоняется.

---

# 2. Общая схема системы

```text
                         ┌─────────────────────┐
                         │       Frontend       │
                         │ dashboard / charts   │
                         └──────────┬──────────┘
                                    │ REST/WebSocket
                                    ▼
                         ┌─────────────────────┐
                         │       Backend       │
                         │ FastAPI / API       │
                         │ orchestration       │
                         └───┬────┬────┬──────┘
                             │    │    │
                ┌────────────┘    │    └──────────────┐
                ▼                 ▼                   ▼
       ┌────────────────┐ ┌───────────────┐  ┌────────────────┐
       │   Collector    │ │ Neural / ML   │  │ Web Research  │
       │ FON.BET        │ │ inference     │  │ + browser      │
       │ Playwright     │ │ training      │  │ + search       │
       └───────┬────────┘ └───────┬───────┘  └───────┬────────┘
               │                  │                  │
               └──────────┬───────┴──────────┬───────┘
                          ▼                   ▼
                    ┌────────────────────────────┐
                    │ Redis Streams / job queue  │
                    └─────────────┬──────────────┘
                                  ▼
                     ┌────────────────────────┐
                     │       Bet Manager      │
                     │ validation / risk      │
                     │ decision / simulation  │
                     └────────────┬───────────┘
                                  ▼
                     ┌────────────────────────┐
                     │ PostgreSQL + Timescale │
                     │ history / time-series  │
                     └────────────┬───────────┘
                                  │
              ┌───────────────────┴───────────────────┐
              ▼                                       ▼
      ┌────────────────┐                     ┌────────────────┐
      │ MinIO / object │                     │ Model Registry │
      │ raw snapshots  │                     │ model artifacts│
      └────────────────┘                     └────────────────┘
```

---

# 3. Docker Compose topology

Минимальный состав контейнеров:

```text
frontend
backend
collector
neural
llm
research
bet-manager
worker
postgres
redis
minio
```

Опционально после стабилизации:

```text
prometheus
grafana
loki
```

Для MVP не нужно делать 20 микросервисов. Некоторые компоненты могут оставаться отдельными Python processes внутри одного image, но логические границы должны сохраняться.

Рекомендуемый стек:

- `frontend`: Next.js + TypeScript.
- `backend`: Python + FastAPI + Pydantic.
- `collector`: Python + Playwright/Chromium.
- `neural`: Python + PyTorch + scikit-learn + LightGBM/XGBoost при необходимости.
- `llm`: llama.cpp server или совместимый локальный inference server.
- `research`: Python + Playwright + HTML extraction + SearXNG или другой конфигурируемый search backend.
- `bet-manager`: Python.
- `worker`: Celery/RQ/ARQ либо Redis Streams consumer. Для MVP предпочтителен простой Redis Streams worker.
- `db`: PostgreSQL с TimescaleDB extension.
- `object storage`: MinIO.

---

# 4. Почему PostgreSQL + TimescaleDB

Система генерирует временные ряды. Polling каждые 5–10 секунд быстро создает большой объем данных, особенно когда у одного события десятки рынков.

TimescaleDB нужен для:

- hypertables;
- time-based partitioning;
- compression;
- retention policies;
- быстрых запросов по временным диапазонам;
- агрегирования odds/state history.

Не создавать отдельную базу на каждый спорт. Использовать одну БД и поле `sport_code`, но каждое sports-specific расширение иметь отдельные таблицы/JSONB-модули при необходимости.

---

# 5. Главный жизненный цикл события

```text
FON.BET page
    ↓
collector poll
    ↓
raw snapshot
    ↓
parser
    ↓
canonical event model
    ↓
normalization
    ↓
DB + event bus
    ↓
feature builder
    ↓
ML prediction
    ↓
optional web research
    ↓
LLM structured verdict
    ↓
bet proposal
    ↓
bet-manager validations
    ↓
virtual execution
    ↓
virtual ledger
    ↓
settlement
    ↓
training dataset / analytics
```

---

# 6. Collector FON.BET

## 6.1 Ответственность

Collector только собирает данные и нормализует их. Он не должен принимать решения о ставках.

Collector обязан уметь:

- получать список событий;
- определять `sport_code`;
- извлекать FON.BET event identifier;
- извлекать названия участников;
- извлекать турнир/лигу;
- извлекать scheduled start;
- определять prematch/live/finished/suspended;
- извлекать live clock;
- извлекать период/set/quarter/etc.;
- извлекать текущий счет;
- извлекать доступные рынки;
- извлекать selections и odds;
- фиксировать, что рынок временно закрыт;
- сохранять время фактического наблюдения;
- сохранять checksum/hash snapshot.

## 6.2 Polling

Для каждого активного контекста polling interval должен быть псевдослучайным:

```python
delay = random.uniform(5.0, 10.0)
```

Не использовать жесткий `sleep(5)` или `sleep(10)`.

Рекомендуется дополнительно иметь:

- exponential backoff при HTTP/browser errors;
- circuit breaker;
- concurrency limit;
- page timeout;
- graceful shutdown;
- health endpoint;
- счетчики ошибок;
- логирование количества событий и времени парсинга.

Для каждой страницы/категории не создавать новый Chromium process на каждом polling cycle. Использовать долгоживущий browser context с контролируемым количеством страниц.

## 6.3 Raw data

На каждый poll создавать raw envelope:

```json
{
  "source": "fonbet",
  "collector_version": "1.0.0",
  "collected_at": "2026-10-08T17:42:12.381Z",
  "source_timestamp": null,
  "sport_code": "football",
  "page_type": "live",
  "url": "...",
  "content_hash": "sha256:...",
  "payload": {}
}
```

Raw snapshot сохраняется в MinIO. Нормализованные сущности — в PostgreSQL/TimescaleDB.

Если данные не изменились, normalized duplicate rows не создавать. Raw polling может сохраняться либо агрегироваться в зависимости от retention policy.

---

# 7. Каноническая модель матча

Match/Event является долгоживущей сущностью, snapshot — отдельным наблюдением.

Рекомендуемые поля `events`:

```text
id UUID
source TEXT = fonbet
source_event_id TEXT
sport_code TEXT
league_id TEXT
league_name TEXT
season TEXT nullable
round TEXT nullable
stage TEXT nullable
home_participant_id TEXT
home_name TEXT
away_participant_id TEXT
away_name TEXT
scheduled_start_at TIMESTAMPTZ
first_seen_at TIMESTAMPTZ
last_seen_at TIMESTAMPTZ
started_at TIMESTAMPTZ nullable
finished_at TIMESTAMPTZ nullable
status TEXT
current_period TEXT nullable
current_score JSONB
metadata JSONB
created_at TIMESTAMPTZ
updated_at TIMESTAMPTZ
```

Для индивидуальных видов спорта вместо `home/away` использовать generic participants. Футбол/хоккей/баскетбол могут иметь team participants, теннис — player participants.

Не полагаться только на названия команд для identity. Основной ключ: `source + source_event_id`. Для резервного dedup использовать нормализованные имена + scheduled start + league.

---

# 8. Timeline/state snapshots

Таблица `event_state_snapshots` должна хранить каждое наблюдаемое состояние.

Минимальный набор:

```text
id UUID
observed_at TIMESTAMPTZ
source_event_id TEXT
event_id UUID
sport_code TEXT
status TEXT
match_clock_ms BIGINT nullable
period TEXT nullable
period_index INT nullable
score_home INT nullable
score_away INT nullable
score_detail JSONB
participants_state JSONB
sport_state JSONB
source_snapshot_id UUID
```

`score_detail` и `sport_state` нужны для спортивно-специфических параметров.

Примеры:

Football:

```json
{
  "red_cards": {"home": 0, "away": 1},
  "yellow_cards": {"home": 2, "away": 3},
  "period": "2H",
  "clock_seconds": 3840,
  "score": {"home": 1, "away": 0}
}
```

Hockey:

```json
{
  "period": 3,
  "clock_seconds": 1120,
  "penalties": {"home": 2, "away": 4},
  "score": {"home": 3, "away": 2}
}
```

Basketball:

```json
{
  "quarter": 4,
  "clock_seconds": 137,
  "score": {"home": 81, "away": 78},
  "team_fouls": {"home": 4, "away": 2}
}
```

Tennis:

```json
{
  "set": 2,
  "game": 5,
  "point": "30-15",
  "sets": {"home": 1, "away": 0}
}
```

Не смешивать смысл полей разных видов спорта в общей плоской схеме.

---

# 9. Odds history

Отдельная hypertable `odds_snapshots`.

Поля:

```text
id UUID
observed_at TIMESTAMPTZ
event_id UUID
source_event_id TEXT
sport_code TEXT
market_id TEXT
market_type TEXT
market_name TEXT
selection_id TEXT
selection_name TEXT
line_value NUMERIC nullable
odds NUMERIC
status TEXT
suspension_reason TEXT nullable
is_live BOOLEAN
raw_market JSONB
source_snapshot_id UUID
```

Очень важно хранить:

- timestamp наблюдения;
- odds;
- линию/handicap/total;
- market type;
- selection;
- live/prematch;
- suspended/open;
- источник snapshot.

Odds не обновлять путем изменения старой строки. Каждое изменение — новый snapshot.

---

# 10. Deduplication и изменение коэффициентов

Для odds вычислять canonical key:

```text
event_id + market_type + line_value + selection_id
```

Если `odds`, `status` и значимые поля не изменились, не обязательно создавать полноценную normalized запись на каждом poll. Однако следует иметь возможность восстановить observation cadence из raw snapshots или lightweight observation table.

Для ML нужны оба типа данных:

1. state observations — что происходило каждые 5–10 секунд;
2. change events — когда реально изменились odds/status.

Рекомендуется хранить и `odds_change_events`, и более компактные state snapshots.

---

# 11. Sports plugin architecture

Каждый спорт должен реализовывать интерфейс вида:

```python
class SportAdapter(Protocol):
    sport_code: str

    def normalize_event(self, raw_event) -> CanonicalEvent: ...
    def normalize_state(self, raw_event) -> EventState: ...
    def normalize_markets(self, raw_event) -> list[MarketSnapshot]: ...
    def build_features(self, history: EventHistory, now: datetime) -> FeatureVector: ...
    def supported_markets(self) -> list[str]: ...
    def outcome_label(self, event_result, market, selection): ...
    def validate_state(self, state: EventState) -> ValidationResult: ...
```

Первый релиз лучше делать только для одного спорта, например football. После стабилизации добавлять hockey/basketball/tennis как отдельные adapters.

Структура:

```text
sports/
  core/
    interfaces.py
    registry.py
    schemas.py
  football/
    adapter.py
    parser.py
    state.py
    markets.py
    features.py
    labels.py
    model.py
  hockey/
    ...
  basketball/
    ...
  tennis/
    ...
```

Добавление нового спорта должно требовать реализации adapter + tests + model pipeline, а не изменения backend/bet-manager schemas.

---

# 12. Feature engineering

## 12.1 Общие признаки

Примеры:

- текущие odds;
- implied probability;
- normalized no-vig probability;
- odds delta за 5/10/30/60/300 секунд;
- скорость изменения odds;
- ускорение изменения odds;
- время до kickoff или время с начала матча;
- status transitions;
- score difference;
- momentum proxies из изменений рынков;
- market suspension frequency;
- spread/total line movement;
- volatility;
- market consistency.

## 12.2 Спорт-specific

Football:

- minute;
- score;
- red/yellow cards, если доступны на FON.BET;
- period;
- corners/other markets, только если достоверно собираются.

Hockey:

- period;
- time;
- score;
- penalties;
- shots/faceoffs, только если есть на источнике.

Basketball:

- quarter;
- game clock;
- score;
- point differential;
- available team/statistic markets.

Tennis:

- set;
- game;
- point score;
- set score;
- odds movement.

Никогда не подставлять отсутствующий статистический показатель как `0`. Использовать `NULL`/missing indicator.

---

# 13. Implied probability и value

Для рынка с взаимоисключающими исходами:

```text
raw_p_i = 1 / odds_i
no_vig_p_i = raw_p_i / sum(raw_p_j)
```

Основная модель должна возвращать вероятности, а не только class label.

Для выбранного исхода:

```text
model_probability = p_model
market_probability = p_no_vig
edge = p_model - p_no_vig
fair_odds = 1 / p_model
```

Полезно дополнительно считать:

```text
expected_return = p_model * odds - 1
```

При этом решение о ставке не должно зависеть от одного `edge` — необходимы calibration, uncertainty и risk limits.

---

# 14. ML architecture

Начать не с deep learning, а с robust baseline.

Рекомендуемый baseline:

1. Logistic Regression / calibrated linear model.
2. LightGBM/XGBoost.
3. Calibration layer.
4. Только после baseline — temporal neural network / transformer / GRU при наличии достаточного объема данных.

Причина: история изменения линии часто уже содержит значительную информацию, а сложная модель без правильной временной валидации легко переобучается.

## ML output

```json
{
  "prediction_id": "uuid",
  "event_id": "uuid",
  "sport_code": "football",
  "market_type": "1X2",
  "selection_id": "home",
  "probability": 0.583,
  "fair_odds": 1.715,
  "market_probability": 0.545,
  "edge": 0.038,
  "confidence": 0.71,
  "model_version": "football_lgbm_v17",
  "feature_version": "football_features_v5",
  "input_snapshot_at": "...",
  "created_at": "..."
}
```

---

# 15. Temporal validation

Запрещен случайный `train_test_split` для основного backtest.

Использовать:

```text
train:  T1 ---------------- T2
valid:                         T2 ---- T3
train:  T1 ----------------------- T3
valid:                                  T3 ---- T4
...
```

То есть walk-forward validation.

Дополнительно сделать purge/embargo вокруг границ, если rolling features используют данные близко к следующей части выборки.

Каждая training sample должна иметь:

```text
event_id
decision_timestamp
feature_cutoff_timestamp
market_snapshot_id
label_timestamp
model_version
dataset_version
```

---

# 16. Dataset generation

Training sample строится не из финального результата напрямую, а как point-in-time snapshot:

```text
decision_timestamp
        ↓
взять только snapshots <= decision_timestamp
        ↓
build features
        ↓
получить model target из финального результата
```

Нельзя допускать:

- использование финального счета в feature;
- использование odds после decision timestamp;
- использование информации, которая появилась позднее;
- случайный train/test split по строкам одного матча.

Split всегда минимум по времени, а желательно дополнительно группировать по `event_id`.

---

# 17. LLM service

На NVIDIA GTX 1050 Ti не строить архитектуру вокруг большой GPU-модели. LLM должна быть маленькой quantized instruct-моделью, запускаемой через llama.cpp или аналогичный локальный сервер.

Ожидаемая конфигурация:

```text
1B–3B parameters
4-bit GGUF
CPU-first / optional GPU offload
context 4k–8k
structured JSON output
```

Конкретную модель держать в конфиге:

```env
LLM_MODEL_PATH=/models/model.gguf
LLM_CONTEXT_SIZE=4096
LLM_GPU_LAYERS=auto
```

Сервис `llm` не должен сам ходить в интернет. У него простой contract:

```text
POST /v1/chat/completions
```

Исследование интернета выполняется `research` service.

---

# 18. Web Research agent

Web Research — отдельная подсистема.

Поток:

```text
Bet candidate
  ↓
research query generator
  ↓
search engine
  ↓
collect top N pages
  ↓
domain allowlist
  ↓
fetch/extract text
  ↓
deduplicate
  ↓
publication timestamp detection
  ↓
freshness filter
  ↓
research packet
  ↓
LLM
```

Research может искать:

- новости о командах;
- новости о травмах/дисквалификациях, если сведения доступны;
- изменения состава;
- новости игроков;
- превью матчей;
- текстовые прогнозы аналитиков;
- контекст турнира.

Research не должен использовать сторонний сайт для подмены FON.BET live score.

## Research quality controls

Каждое evidence:

```json
{
  "title": "...",
  "url": "...",
  "domain": "...",
  "published_at": "...",
  "retrieved_at": "...",
  "age_seconds": 813,
  "snippet": "...",
  "content_hash": "sha256:..."
}
```

Использовать allowlist/denylist, timeout, максимум страниц и максимальный размер текста.

Не давать LLM без ограничений выполнять arbitrary HTTP requests.

---

# 19. LLM input contract

LLM получает не весь HTML и не весь database dump, а compact JSON.

```json
{
  "event": {
    "sport": "football",
    "league": "...",
    "home": "...",
    "away": "...",
    "scheduled_start": "...",
    "status": "live",
    "clock": "67:21",
    "score": {"home": 1, "away": 0}
  },
  "market": {
    "type": "total",
    "line": 2.5,
    "selection": "under",
    "odds": 1.92,
    "observed_at": "..."
  },
  "ml": {
    "probability": 0.61,
    "market_probability": 0.52,
    "edge": 0.09,
    "confidence": 0.74,
    "model_version": "..."
  },
  "research": [
    {
      "title": "...",
      "domain": "example.com",
      "published_at": "...",
      "snippet": "..."
    }
  ],
  "constraints": {
    "max_stake_fraction": 0.01,
    "max_bet_age_seconds": 8,
    "simulation_only": true
  }
}
```

---

# 20. LLM output contract

LLM обязан возвращать только JSON:

```json
{
  "verdict": "BET",
  "market_type": "total",
  "selection_id": "under",
  "confidence": 0.72,
  "stake_recommendation_fraction": 0.006,
  "reason_codes": [
    "MODEL_EDGE",
    "POSITIVE_MARKET_MOVEMENT",
    "RESEARCH_SUPPORTS"
  ],
  "contradictions": [],
  "research_quality": 0.66,
  "research_freshness_seconds": 920,
  "invalid_or_missing_data": [],
  "summary": "short factual summary",
  "evidence": [
    {
      "url": "...",
      "domain": "..."
    }
  ]
}
```

Allowed verdicts:

```text
BET
NO_BET
INSUFFICIENT_DATA
```

LLM нельзя разрешать возвращать:

- произвольную сумму денег без ограничения;
- другой event_id;
- несуществующий market;
- новую odds;
- ссылку без evidence record;
- числовую вероятность вне `[0,1]`;
- confidence вне `[0,1]`.

Pydantic JSON Schema должен быть source of truth. Некорректный JSON = `INSUFFICIENT_DATA`.

---

# 21. Как объединять ML и LLM

Не делать:

```text
LLM говорит BET → bet immediately
```

Делать:

```text
ML probability
       +
market state
       +
LLM structured opinion
       +
research quality
       +
risk manager
       ↓
Bet Manager
```

На первом этапе LLM можно использовать как отдельный signal:

```text
llm_signal = -1 / 0 / +1
```

После накопления истории обучить calibration/meta-model, которая проверяет, дает ли LLM дополнительную predictive value поверх ML.

То есть нужно экспериментально проверить варианты:

```text
ML only
ML + odds dynamics
ML + LLM
ML + research features
ML + LLM + research
```

LLM не считать полезной автоматически только потому, что ее reasoning выглядит убедительно.

---

# 22. Bet Manager

`bet-manager` — самый важный сервис с точки зрения безопасности и воспроизводимости.

Он получает `BetProposal`, а не выполняет произвольные инструкции модели.

```json
{
  "proposal_id": "uuid",
  "event_id": "uuid",
  "market_id": "...",
  "selection_id": "...",
  "requested_stake_fraction": 0.006,
  "requested_stake": 60.0,
  "created_at": "...",
  "expires_at": "...",
  "source": {
    "ml_prediction_id": "uuid",
    "llm_decision_id": "uuid"
  }
}
```

После этого проходит validation pipeline.

---

# 23. Bet validation pipeline

Порядок:

```text
1. JSON/schema validation
2. event exists
3. event is not finished
4. market exists
5. market is open
6. selection exists
7. odds timestamp is fresh
8. current odds match proposed odds / allowed slippage
9. event state is fresh
10. no duplicate/collision bet
11. bankroll check
12. exposure check
13. stake limit check
14. daily/session loss limit
15. model confidence threshold
16. edge threshold
17. LLM quality/freshness threshold if LLM is required
18. sport-specific validation
19. simulation executor
20. immutable ledger entry
```

Любая ошибка → explicit reject reason.

Примеры `reject_reason`:

```text
STALE_ODDS
STALE_EVENT_STATE
MARKET_SUSPENDED
INSUFFICIENT_BALANCE
EXPOSURE_LIMIT
DUPLICATE_PROPOSAL
EDGE_TOO_LOW
MODEL_CONFIDENCE_TOO_LOW
LLM_DATA_INSUFFICIENT
SPORT_VALIDATION_FAILED
```

---

# 24. Virtual bankroll

Создать `virtual_accounts` и immutable `ledger_entries`.

Баланс рассчитывается как:

```text
balance = initial_balance + sum(ledger_entries.amount)
```

Типы ledger entries:

```text
INITIAL_DEPOSIT
BET_PLACED
BET_WIN
BET_LOSS
BET_VOID
ADJUSTMENT
```

`BET_PLACED` уменьшает available balance и создает exposure.

После settlement:

```text
WIN  → payout returned
LOSS → no payout
VOID → stake returned
```

В отдельном поле сохранять `locked/exposed_balance`.

---

# 25. Stake sizing

LLM может предложить размер только в ограниченной доле банка, но окончательный размер определяет Risk Manager.

Базовая схема:

```text
risk_fraction = min(
    configured_max_fraction,
    model_edge_based_fraction,
    exposure_limit_remaining,
    bankroll_limit
)
```

Можно реализовать fractional Kelly как эксперимент, но не делать Kelly единственным способом sizing.

Например:

```text
kelly = (p * odds - 1) / (odds - 1)
fractional_kelly = kelly * KELLY_FRACTION
```

Рекомендуется начинать с малого `KELLY_FRACTION` или фиксированного cap и сравнивать стратегии в backtest.

---

# 26. Execution simulator

`simulation-executor` должен имитировать то, что произошло бы при ставке в момент решения.

Правило по умолчанию:

```text
execution_odds = last valid odds snapshot at decision time
```

Нельзя использовать более поздние odds.

Опционально добавить реалистичную latency model:

```text
decision_at
+ execution_latency
→ find next available snapshot
→ accept/reject depending on odds movement
```

Таким образом backtest не должен получать идеальную ставку по будущему коэффициенту.

---

# 27. Settlement

После завершения события:

```text
finished event
  ↓
result parser
  ↓
canonical result
  ↓
bet settlement worker
  ↓
resolve market
  ↓
ledger entries
  ↓
P&L
```

Не settlement-ить по предположению.

Если результат неоднозначен или market rules невозможно однозначно применить:

```text
SETTLEMENT_REVIEW_REQUIRED
```

В первую очередь поддержать небольшой набор рынков, которые можно однозначно рассчитать.

---

# 28. Database model

Минимальные таблицы:

```text
users / system_settings
sports
leagues
participants
participant_aliases
events
event_state_snapshots
raw_snapshots
markets
market_selections
odds_snapshots
odds_change_events
web_research_runs
web_documents
web_evidence
feature_snapshots
ml_predictions
llm_decisions
bet_proposals
bet_validation_results
virtual_accounts
ledger_entries
bets
bet_settlements
model_versions
training_runs
training_datasets
experiment_results
audit_log
```

---

# 29. Important database relationships

```text
sport
  └── leagues
        └── events
              ├── event_state_snapshots
              ├── markets
              │     └── market_selections
              │           └── odds_snapshots
              ├── feature_snapshots
              ├── ml_predictions
              ├── llm_decisions
              └── bet_proposals
                         └── bets
                              └── bet_settlements
```

Все timestamp fields должны быть `TIMESTAMPTZ` в UTC.

Frontend локализует timestamp в timezone пользователя.

---

# 30. Auditability

Каждое решение должно отвечать на вопросы:

- какой матч;
- какой рынок;
- какой коэффициент был в момент решения;
- какое было состояние матча;
- какие признаки увидела ML;
- какая вероятность была предсказана;
- какая версия ML использовалась;
- какие web evidence были доступны;
- какой JSON вернула LLM;
- какой validation result получен;
- сколько было поставлено виртуально;
- почему ставка была принята/отклонена;
- чем закончилась ставка;
- какой P&L получен.

Это важнее красивого UI.

---

# 31. API backend

Основные endpoints:

```text
GET  /api/health
GET  /api/sports
GET  /api/events
GET  /api/events/{id}
GET  /api/events/{id}/timeline
GET  /api/events/{id}/odds
GET  /api/events/{id}/predictions
GET  /api/events/{id}/research
GET  /api/bets
GET  /api/bets/{id}
GET  /api/account
GET  /api/ledger
GET  /api/performance
GET  /api/models
GET  /api/training/runs
POST /api/research/run
POST /api/models/train
POST /api/simulation/reset
```

Не добавлять endpoint вида `POST /bet` без прохождения `bet-manager`.

Для frontend использовать WebSocket/SSE для live updates.

---

# 32. Redis Streams

Рекомендуемые streams:

```text
fonbet.raw
fonbet.events
fonbet.state
fonbet.odds
features.ready
ml.predictions
research.requests
research.results
llm.requests
llm.results
bet.proposals
bet.validated
bet.executed
bet.settled
training.jobs
```

Messages должны иметь:

```json
{
  "message_id": "uuid",
  "schema_version": "1",
  "created_at": "...",
  "event_id": "uuid",
  "sport_code": "football",
  "payload": {}
}
```

Consumer должен быть idempotent.

---

# 33. Research scheduling

Не отправлять LLM/research запрос по каждому событию каждые 5 секунд.

Это приведет к ненужной нагрузке и шуму.

Сделать candidate filter:

```text
all live events
  ↓
liquid/interesting markets
  ↓
ML edge above threshold
  ↓
fresh odds
  ↓
research only for top candidates
```

Research cache key:

```text
event_id + normalized_query + time_bucket
```

Например, не искать одну и ту же новость повторно каждую секунду.

---

# 34. Frontend

Dashboard должен показывать:

1. Live events.
2. Current score/time.
3. Current odds.
4. Odds movement chart.
5. ML probability vs implied probability.
6. LLM verdict.
7. Research evidence.
8. Bet proposal/validation result.
9. Virtual bankroll.
10. P&L and drawdown.
11. Model version.
12. Event timeline.

Отдельная страница event detail:

```text
MATCH HEADER
score / clock / status

MARKETS
odds table

TIMELINE
state snapshots

ML
probability / edge / calibration

LLM
structured verdict / evidence

BET MANAGER
validation chain

RESULT
settlement / P&L
```

---

# 35. Training pipeline

Offline training не должен блокировать collector.

Pipeline:

```text
DB history
  ↓
Dataset builder
  ↓
point-in-time join
  ↓
feature matrix
  ↓
time split
  ↓
train
  ↓
calibration
  ↓
walk-forward validation
  ↓
metrics
  ↓
model artifact
  ↓
model registry
  ↓
staging
  ↓
shadow inference
  ↓
activate
```

Модель нельзя автоматически активировать только по одному ROI. Обязательно учитывать calibration и стабильность по периодам.

---

# 36. Metrics

ML:

```text
log loss
Brier score
ROC-AUC where applicable
PR-AUC where applicable
calibration error
reliability curve
```

Betting simulation:

```text
ROI
P&L
yield
hit rate
average odds
average edge
max drawdown
profit factor
number of bets
turnover
exposure
P&L by sport
P&L by market
P&L by confidence bucket
P&L by edge bucket
P&L by odds bucket
```

Important: `ROI = profit / turnover`, а не только `profit / initial bankroll`.

---

# 37. Backtest realism

Backtest должен учитывать:

- timestamped odds;
- suspension;
- stale quotes;
- execution latency;
- odds movement;
- insufficient balance;
- duplicate bets;
- exposure;
- limits;
- multiple simultaneous bets;
- exact settlement rules.

Не разрешать модели смотреть на окончательный коэффициент, если ставка произошла раньше.

---

# 38. Model registry

Каждая модель имеет:

```text
model_id
sport_code
model_type
version
training_dataset_id
feature_version
code_commit
created_at
metrics
artifact_uri
status
```

Statuses:

```text
TRAINING
CANDIDATE
STAGING
SHADOW
ACTIVE
RETIRED
FAILED
```

---

# 39. Model inference isolation

`neural` сервис не должен напрямую писать в `bets`.

Он пишет только `ml_predictions` / `bet_proposals`.

`llm` также не имеет доступа на запись в financial ledger.

Только `bet-manager` может создавать virtual bets и ledger entries.

---

# 40. Security

Даже для локальной системы:

- secrets только через `.env`/Docker secrets;
- PostgreSQL не публиковать наружу без необходимости;
- Redis/MinIO не публиковать наружу без необходимости;
- frontend → backend only;
- backend service-to-service network;
- research sandbox;
- никакого arbitrary shell tool от LLM;
- никакого arbitrary URL POST/PUT от LLM;
- allowlist web domains;
- resource limits для Chromium;
- контейнеры non-root, где возможно.

---

# 41. Observability

Каждый сервис должен иметь:

```text
/health
/ready
```

Логировать минимум:

```text
timestamp
service
level
request_id
event_id
sport_code
operation
latency
result
error
```

Добавить metrics:

```text
collector_polls_total
collector_errors_total
collector_events_seen
collector_parse_latency
odds_updates_total
ml_predictions_total
llm_requests_total
research_requests_total
bet_proposals_total
bet_rejected_total
bet_executed_total
settlements_total
```

---

# 42. Failure handling

Если FON.BET недоступен:

```text
collector degraded
→ no new betting proposals
→ existing virtual bets remain tracked
```

Если DB unavailable:

```text
do not make bets
```

Если LLM unavailable:

- если конфигурация `LLM_REQUIRED=true` → no bet;
- иначе допускается ML-only strategy только в отдельном experiment mode.

Если research unavailable:

- никогда не считать отсутствие research отрицательным фактом;
- выставить `research_quality=0`;
- LLM может вернуть `INSUFFICIENT_DATA`.

---

# 43. Конфигурация

Пример `.env.example`:

```env
APP_ENV=development
LOG_LEVEL=INFO

POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=fonbet_ai
POSTGRES_USER=fonbet
POSTGRES_PASSWORD=change_me

REDIS_URL=redis://redis:6379/0
MINIO_ENDPOINT=minio:9000
MINIO_ACCESS_KEY=minio
MINIO_SECRET_KEY=change_me
MINIO_BUCKET_RAW=raw-snapshots

FONBET_POLL_MIN_SECONDS=5
FONBET_POLL_MAX_SECONDS=10
FONBET_MAX_CONCURRENCY=4

ML_MIN_EDGE=0.03
ML_MIN_CONFIDENCE=0.60
MAX_STAKE_FRACTION=0.01
MAX_EVENT_EXPOSURE_FRACTION=0.02
MAX_DAILY_LOSS_FRACTION=0.05
MAX_ODDS_AGE_SECONDS=8

LLM_MODEL_PATH=/models/model.gguf
LLM_CONTEXT_SIZE=4096
LLM_GPU_LAYERS=auto
LLM_REQUIRED=true

RESEARCH_ENABLED=true
RESEARCH_MAX_PAGES=5
RESEARCH_MAX_AGE_SECONDS=86400
RESEARCH_TIMEOUT_SECONDS=10

BET_MODE=SIMULATION
```

Все параметры, влияющие на решение, должны попадать в snapshot/config version для воспроизводимости эксперимента.

---

# 44. Что не надо делать

Не делать:

- микросервис на каждый endpoint;
- прямые SQL-запросы из frontend;
- LLM с прямым доступом к PostgreSQL write access;
- LLM с direct browser control без sandbox;
- random train/test split;
- обучение по текущим результатам до момента decision timestamp;
- одну общую модель для всех видов спорта на старте;
- hardcoded parser selectors без tests;
- бесконечный research loop;
- парсинг каждые 5 секунд без jitter;
- реальные денежные ставки в MVP;
- автоматическое продвижение модели в ACTIVE без backtest + shadow period.

---

# 45. Recommended repository structure

```text
fonbet-ai/
├── docker-compose.yml
├── .env.example
├── README.md
├── Makefile
├── migrations/
├── configs/
│   ├── sports/
│   ├── models/
│   └── risk/
├── services/
│   ├── backend/
│   ├── collector/
│   ├── neural/
│   ├── llm/
│   ├── research/
│   ├── bet-manager/
│   └── worker/
├── frontend/
├── packages/
│   ├── contracts/
│   ├── sports-core/
│   └── db/
├── sports/
│   ├── football/
│   ├── hockey/
│   ├── basketball/
│   └── tennis/
├── infra/
│   ├── postgres/
│   ├── redis/
│   ├── minio/
│   └── monitoring/
├── tests/
│   ├── contract/
│   ├── collector/
│   ├── sports/
│   ├── neural/
│   ├── llm/
│   └── betting/
└── docs/
```

---

# 46. Development order

Правильный порядок реализации:

```text
1. contracts
2. DB schema + migrations
3. one FON.BET parser
4. event/state/odds ingestion
5. historical data viewer
6. feature builder
7. baseline ML
8. backtester
9. virtual bankroll
10. bet-manager
11. LLM JSON interface
12. research service
13. ML + LLM ensemble
14. frontend analytics
15. monitoring
16. second sport
```

Не начинать с LLM. Без качественного timestamped dataset LLM ничего принципиально не исправит.

---

# 47. Minimum viable scientific experiment

До сложного deep learning система должна уметь провести такой эксперимент:

```text
Collect football live data
↓
Store every event state
↓
Store odds history
↓
Create snapshots at decision times
↓
Train baseline model
↓
Walk-forward backtest
↓
Simulate bets through bet-manager
↓
Measure P&L/drawdown/calibration
↓
Repeat with LLM disabled
↓
Repeat with LLM enabled
↓
Compare statistically
```

Главный научный вопрос проекта:

> Дает ли информация, извлекаемая только из временной динамики линии FON.BET и состояния матча, дополнительное предсказательное преимущество после учета букмекерской вероятности и расходов/ограничений исполнения, и дает ли текстовый web research + LLM дополнительную информацию поверх этого baseline?

---

# 48. Definition of Done для первой рабочей версии

MVP считается рабочим, когда одновременно выполняется:

- `docker compose up -d` запускает все обязательные сервисы;
- collector получает FON.BET events;
- collector работает с random 5–10 sec polling;
- raw snapshots сохраняются;
- normalized events/state/odds сохраняются;
- frontend показывает live timeline;
- есть хотя бы один sport adapter;
- есть baseline ML model;
- prediction сохраняется с model/feature versions;
- LLM возвращает JSON по schema;
- research packet может быть передан LLM;
- bet-manager валидирует proposal;
- virtual bankroll изменяется только через ledger;
- virtual bet settlement работает;
- есть walk-forward backtest;
- есть P&L/drawdown/calibration metrics;
- есть test на отсутствие future leakage;
- при stale odds система не создает virtual bet;
- при restart контейнеров история не теряется.

---

# 49. Основные открытые настройки, которые надо вынести в config

Не хардкодить:

```text
sports enabled
markets enabled per sport
polling interval
max collector concurrency
odds freshness limit
minimum edge
minimum confidence
stake cap
exposure cap
daily loss cap
LLM required/optional
LLM model path
research domains
research freshness
research frequency
backtest latency
retention periods
```

---

# 50. Финальная логика решения

Итоговый pipeline должен выглядеть так:

```text
                    ┌──────────────┐
                    │ FON.BET data │
                    └──────┬───────┘
                           ▼
                     Feature Builder
                           │
                           ▼
                     Numerical ML
                           │
                     probabilities
                           │
               ┌───────────┴───────────┐
               ▼                       ▼
          Market signal            Candidate
                                       │
                                       ▼
                                  Web Research
                                       │
                                       ▼
                                      LLM
                                       │
                                structured JSON
                                       │
               ┌───────────────────────┘
               ▼
          Bet Proposal
               │
               ▼
          BET MANAGER
               │
       ┌───────┴────────┐
       │                │
    REJECT            ACCEPT
                        │
                        ▼
               Virtual Executor
                        │
                        ▼
               Immutable Ledger
                        │
                        ▼
                    Settlement
                        │
                        ▼
                   Training data
```

Ключевая архитектурная граница: **ML/LLM предлагают, `bet-manager` решает, executor исполняет только то, что прошло проверки, а база хранит каждую стадию с timestamp.**
