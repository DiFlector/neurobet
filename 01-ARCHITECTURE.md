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

Калибратор боевых линий учится на последних 20% матчей обучающего окна (`temporal_holdout`), а не на случайных строках тех же матчей. Смена этой схемы — minor-версия и остаётся `SHADOW` до promote.

Стенд `services/neural/app/compare` сравнивает на одних и тех же матчах вероятность букмекера без маржи, Elo/Glicko по канонической истории, логистическую регрессию, HistGradientBoosting и CatBoost. Отдельной ставкой Markov больше не является: его вероятность входит в остаток как разница логитов с линией, а сам выход ограничен отрезком от 0.02 до 0.98. Остаток — это логистическая регрессия и HistGradientBoosting, которые сдвигают логит линии не больше чем на 0.35 и не видят рыночную вероятность среди признаков. Они учатся на второй половине validation. Срезы ATP/WTA, challenger и ITF считаются отдельно, если в тесте уровня хотя бы 200 матчей. CatBoost считается на CPU. `beats_market` требует, чтобы доверительный интервал разницы log loss был целиком ниже нуля, Brier не был хуже линии больше чем на 0.02, а в тесте было не меньше 100 матчей. Положительный ROI сам по себе модели в реестр не переводит. Результат пишется в `experiment_results`.

Теннис считает матч и сеты одной линией `tennis_v2.0.0`. Вход — лента счёта до среза: каждое очко и скобки сетов, плюс слот рынка. Закрытый сет остаётся фактом для матча, даже если вердикт по нему не успели поставить. При спорной картине вердикт матча ждёт. Метка сета приходит, когда сет закрылся или когда известен итог. Без своих котировок сетов эту версию не обучают. `tennis_v1.0.0` сеты не видит.

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

Стенд сравнения делит историю на три периода: обучение, выбор параметров и калибратора, замороженный тест. Все срезы одного матча лежат в одной части. Список тестовых `event_id` сохраняется. Повторный подбор, который задевает эти матчи, отклоняется. Isotonic-калибровка выбирается только если в калибровочной части не меньше 1000 независимых матчей.

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

Модель учится на срезах с реальной линией, потому что в лайве она считает вероятность на всей ленте матча. Длинный матч сжимается до равномерной ленты из 12 срезов, включая первый и последний, чтобы не перевесить короткие. Пустая котировка остаётся пустой и не проходит порог 2%. Ничья не получает метку победителя стороны B.

Проверка идёт walk-forward по матчам: каждый проверочный матч считает модель, которая видела только более ранние матчи. Метрика решения берётся с одного среза на матч — второго подряд, где та же сторона подтвердила перевес не меньше 2%. Этот срез выбирает та же модель, которая потом сохраняется для последнего окна. Метка остаётся итоговым победителем. Контекстные числа травм, усталости и новостей берутся только если они были записаны не позже среза.

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

Живой и прематчевый матч ищется один раз. Пока поиск не начат, он стоит в очереди. Во время запроса статус — поиск работает. Завершённый запуск без страниц означает, что в интернете нет информации о матче, и повторно он не ставится.

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

## 18.1. Внешние прематч-прогнозы экспертов (External Prediction Aggregator)

Для обогащения контекста матча за несколько часов или дней до старта подсистема `services/research/app/predictions/` осуществляет регулярный парсинг прогнозов с профильных сайтов:
1. `vseprosport.ru` (VseProSport)
2. `stavka.tv` (Stavka.tv)
3. `livesport.ru` (LiveSport)
4. `kushvsporte.ru` (KushVsporte)
5. `betzona.ru` (Betzona)
6. `legalbet.ru` (Legalbet)
7. `nb-bet.com` (NB-Bet)
8. `overbetting.ru` (Overbetting)
9. `metaratings.ru` (MetaRatings)

### Жизненный цикл и правила:
- **Периодический сбор**: раз в 60 минут фоновый воркер опрашивает открытые ленты прогнозов.
- **Хранение в PostgreSQL**: модель `external_predictions` (миграция `010_external_predictions.py`) с уникальностью по `source_url`.
- **Нормализация имен игроков**: утилита `normalize_tennis_player()` с каноническим словарем фамилий (английские и русские варианты) и очисткой инициалов.
- **Сопоставление с линией Fonbet**: при появлении события в прематче или лайве (`events`) происходит привязка прогноза по участникам и окну времени `abs(scheduled_start_at - estimated_start_at) <= 24h`.
- **Автоочистка фантомных матчей (24h TTL)**: если прогноз был опубликован, но матч так и не появился в линии Fonbet, спустя 24 часа после предполагаемого времени начала (`expires_at < now() AND matched_event_id IS NULL`) запись безвозвратно удаляется из БД (`cleanup_expired_unmatched_predictions()`).
- **Инъекция в Research Packet**: при формировании пакета для локальной LLM функция `execute_research()` запрашивает консенсус `get_consensus_for_event()` и добавляет структурированное свидетельство `ResearchEvidence` с доменом `"expert_consensus"` (доля голосов за П1/П2, средний кэф, сниппеты обоснований аналитиков).

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

Теннисная боевая линия считает долю Келли из `sports/tennis/tennis.cfg`, затем ограничивает её долей банка, свободным балансом и остатком экспозиции. `bet-manager` пересчитывает ожидаемую ценность по свежему коэффициенту. В журнал попадает только ставка со статусом `ACTIVE`; тень остаётся в `model_bet_journal`. На карточках Live по умолчанию видны только зарегистрированные ставки с суммой.

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
model_bet_journal
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
ext_tennis_players
ext_tennis_matches
ext_tennis_rankings
ext_tennis_serve_logs
ext_tennis_canonical_players
ext_tennis_canonical_matches
```

Внешняя теннисная история (Jeff Sackmann / Tennis Abstract, CC BY-NC-SA 4.0, только некоммерческое использование; сезонная лента `OnlineData/raw/ten`; линия `OnlineData/raw/odds/atp_tennis.csv`; charting подач) загружается `scripts/import_external_tennis.py`. Сырые строки остаются в `ext_tennis_matches`. `scripts/build_canonical_tennis.py` склеивает повторы в `ext_tennis_canonical_matches` и `ext_tennis_canonical_players`: общий id Sackmann/TML, иначе фамилия с инициалом, пол, покрытие, окно 14 дней и не противоречащий счёт. Коэффициенты берутся из `atp_odds`, затем из `ten`. Статистика подачи — из Sackmann, затем из TML. `available_at` — самый ранний честный срок. Выгрузка лежит в `OnlineData/canonical`. Результат матча виден модели только при `available_at <= cutoff`. `scripts/link_tennis_players.py` связывает имя Fonbet с каноническим игроком, только если фамилия и инициал однозначны в окне дат. Пустой инициал связывается, когда фамилия и пол дают ровно одного игрока. Ручные пары читаются из `sports/tennis/data/fonbet_name_overrides.csv`. Уверенность пишется в `participant_aliases.link_confidence`.

Для Flashscore реализован отдельный research-адаптер `services/research/app/tennis_flashscore.py`. Первая страница и `allEventsCount` читаются из `cjs.initialFeeds["results_s"]`; код страны игрока извлекается из его стороны матча. Видимая кнопка `Show more matches` вызывает устаревший feed с суффиксом `_s`, поэтому адаптер использует Chromium BrowserContext request к текущему формату `pr_2_<country>_<player>_<zero_based_page>_1_en_1` и принимает только `flashscore.ninja`. Общий слот источника освобождается после каждого запроса; между операциями выдерживается не менее двух секунд. Feed включает одиночные и парные результаты, а `allEventsCount` считает только одиночные: полноту можно подтвердить лишь двумя одинаковыми успешными пустыми ответами и точным равенством числа уникальных одиночных ID. Парные и смешанные ID остаются в архиве, но не входят в метрики одиночного игрока. Успешное 30-дневное перекрытие помечается `history_scope=overlap`, полный обход доступной через feed истории — `full_accessible`, остальные случаи — `partial`; это не означает полноту всей карьеры. Каждая feed-страница архивируется до разбора с URL, ID, временем, HTTP-статусом и причиной окончания. Парсер новой версии может повторно обработать проверенные документы предыдущих версий, сверяя профиль, feed URL, непрерывный номер страницы и SHA-256; новая загрузка начинается с текущей страницы профиля. Контрольная точка сохраняет архивный префикс. Повторный импорт идемпотентен по ID/содержимому; A→B→A сохраняет версии и время доступности. Внешние результаты сопоставляются с каноническим архивом только при совпадении пары игроков, дня и места турнира; разные source match ID внутри одного импорта не схлопываются из-за похожего контекста.

Поиск игрока использует read-only `GET https://s.livesport.services/api/v2/search/?q=...` — это тот же JSON endpoint, который вызывает обычный поиск на теннисной странице Flashscore. DomainFilter разрешает только `livesport.services` для этого вызова, каждый отдельный GET получает общий source slot с межзапросным интервалом не менее двух секунд и Referer `https://www.flashscore.com/tennis/`; payload принимается только для сущностей типа Player и спорта Tennis. Ответ 200 должен быть валидным JSON для каждой использованной вариации имени; timeout, parse failure, HTTP 403/429 и прочие сетевые ошибки остаются ошибками источника и никогда не превращаются в `not_found`. Это убирает запуск Chromium на поиске профиля; Chromium остаётся для получения истории профиля и PBP.

Доступ к Flashscore проходит через durable PostgreSQL очередь и глобальный регулятор из миграции 026. Два сетевых слота назначаются короткой транзакцией, между стартами выдерживается минимум две секунды, а сетевой запрос выполняется без открытого соединения БД. Приоритеты: live point-by-point — 0, подготовка участника — 10, архивная страница — 20; архив повышается до ранга 5 после десяти секунд ожидания. Cooldown при распознанной блокировке снижает число слотов до одного. Очередь, lease и занятые слоты сохраняются в `flashscore_source_requests` и `flashscore_source_control`. Быстрое обнаружение live/prematch и PBP выполняется с интервалом 15 секунд; сканирование всей завершённой Fonbet-истории для низкоприоритетного PBP backfill запускается отдельно, по умолчанию раз в 900 секунд (`FLASHSCORE_ARCHIVE_DISCOVERY_SECONDS`), чтобы не перечитывать тысячи завершённых событий на каждом live-цикле. Внутри archive enrollment существующие матчи, связи, задания, документы, заявки подтверждения и названия лиг загружаются пакетами, а не отдельным запросом для каждого события. Docker healthcheck отдельного Flashscore worker проверяет возраст `/tmp/healthy` (не старше 45 секунд), чтобы не считать застрявший executor здоровым из-за оставшегося после старта файла.

Post-match refresh сначала рассматривает завершённые события с `first_seen_at` за последние сутки и ожидающим архивным PBP; порядок отдаёт предпочтение заданиям, уже проходившим попытки поиска. Это не даёт старому архивному backfill поглотить весь ограниченный бюджет актуализации профилей. Каждое обновление остаётся обычным durable player job и проходит общий source regulator; `finished_at` сам по себе не служит историческим временем доступности результата.

Первый контрольный набор завершил доступный обход десяти профилей: Antonius 53, Djokovic 1 591, Alcaraz 502, Sinner 593, Swiatek 613, Sabalenka 787, Gauff 683, Medvedev 838, Zverev 1 142 и Fritz 924 распознанных завершённых матчей. Числа относятся к результатам каждого профиля и пересекаются между участниками; это не количество уникальных матчей. Полные повторы Sinner и Gauff вернули те же 593 и 683 записи и не добавили внешних матчей; Gauff продолжился из checkpoint после страницы 14. Поиск профиля использует описанный выше read-only JSON endpoint без запуска Chromium; Chromium остаётся для feed, истории и PBP. Для фонового импорта lease продлевается heartbeat только при совпадении текущего attempt token и неистёкшего lease. SQL-транзакция закрыта на время сетевой операции; межпроцессные ограничения выдаёт глобальный durable регулятор источника.
Поиск профиля читает JSON-ответ, который Flashscore генерирует при обычном вводе в собственный поиск; принимаются только строки типа `Player` из Tennis со стабильным ID и совпадающим именем. HTML-ссылки больше не являются единственным источником результатов. Найденный профиль сначала сохраняется как кандидат и отдельно ставится в durable import queue: его полная доступная история архивируется и импортируется без присвоения метрик участнику Fonbet. Имя само по себе не подтверждает личность; запись в метрики разрешается владельцу refresh-задания либо после point-in-time `confirmed` source identity. После ручного подтверждения участник использует тот же source ID, а повторная подготовка разбирает архивированные страницы для построения его метрик.

Каждая распознанная версия результата дополнительно записывается в неизменяемую `ext_tennis_match_observations`: ключ версии вычисляется по каноническому содержимому распознанной записи, а `available_at`, URL, хеш исходной страницы и версия парсера сохраняют происхождение и момент доступности. `ext_tennis_matches` служит текущей проекцией и обновляется при повторном разборе, если новая версия парсера исправила распознанные поля; observations при этом не перезаписываются. Повтор той же записи идемпотентен, а исправление позднее создаёт новую версию. Durable job v8 прошёл на десяти профилях, подтвердил точное равенство singles count и `allEventsCount` и исправил текущие категории ATP/BO5; контрольные числа и ограничения ручной PBP-сверки находятся в `docs/research/flashscore_profile_pagination_2026-10-20.md`.

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

Исторический replay админки использует ту же ленту признаков, что и live inference: срез видит только наблюдения не позже своего времени, сохранённый к этому моменту research/LLM контекст или нейтральные значения, правило подтверждающих срезов и исполнение с задержкой. Его банкролл отдельный. Walk-forward `make backtest` по-прежнему обучает модель заново и не заменяет этот replay.

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

`tennis_composite` остаётся поддерживаемым legacy checkpoint форматом; описание находится в [`03-COMPOSITE-MODEL.md`](03-COMPOSITE-MODEL.md). Новый `tennis_market_offset_boosting` checkpoint хранит независимые match/set heads одной записью реестра. Он использует строгий упорядоченный список из 23 tennis-v4 признаков, включая `market_is_set`; старые checkpoint читают сохранённый при обучении список. Формула и метрики выпуска фиксируются в [`NewModels.md`](NewModels.md).

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
MIN_STAKE=50
MAX_STAKE_FRACTION=0.20
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

## Независимый live-прогноз тенниса (research-контур)

`TennisForecast` — отдельный прогнозный контракт без коэффициентов, рыночных вероятностей, суммы и рекомендации ставки. Он хранит неизменяемые срезы вероятностей следующего очка, текущего гейма, сета и матча, сценарии на три очка и два гейма, качество/причины и версии модели/признаков. `neural` рассчитывает этот контур отдельно от обычных ML-предложений; результат может существовать без котировки и не создаёт запись в bankroll. `bet-manager` остаётся единственным компонентом регистрации ставок.

Для выбора событий прогнозный контур включает все live-матчи тенниса и prematch-матчи с `scheduled_start_at` от текущего времени до шести часов вперёд. Prematch-срез строится из состояния 0:0 и помечает отсутствие live-состояния; live-срез использует последнее достоверное состояние. Если live-состояние отсутствует или не разбирается, сохраняется недоступность с причиной, а не прогноз из 0:0. Более поздние prematch события не обрабатываются этим контуром. Окно не меняет общую очередь ставок и её policy.

Текущий исследовательский baseline использует исторические очки подачи только с известными знаменателями и точные правила счёта для расчёта ближайших состояний. Обычные Fonbet score polls не доказывают, что между опросами произошло ровно одно очко, поэтому не обновляют оценку подачи и не становятся очковыми метками. Повторные одинаковые снимки и пропуски не считаются наблюдениями. Baseline не читает Fonbet odds. Внешний importer использует разрешённый доступ; CAPTCHA и технические блокировки не обходятся. Логистическая регрессия, бустинг, отдельная GRU, аудит и стратегия оценки цены остаются исследовательскими этапами roadmap; до выполнения model-release гейтов кандидаты не становятся боевыми.

Снимки доступны через `GET /api/events/{event_id}/forecasts` и `/forecasts/latest`, а карточка матча показывает прогнозный срез отдельно от существующих вердиктов и ставок. Соседний повтор состояния подавляется по digest. Последовательность `A → B → A` сохраняется: повторный `A` получает следующий `sequence_no` и не схлопывается со старым срезом.

### Подготовка участников и сохранённые метрики

Перед сохранением независимого tennis forecast `neural` вызывает `POST /api/tennis-history/prepare`. Research подбирает канонические профили обоих участников и параллельно обновляет их; первый прогноз ждёт готовности пары. Точные неоднозначные совпадения не объединяются. Новая история импортируется полностью с архивом исходных документов. Неполная первая пагинация остаётся в ожидании, а сохранённые метрики можно использовать как `stale`, если последующее обновление не удалось. Повторные опросы счёта не инициируют повторный полный импорт.

`tennis_player_profiles` содержит текущий материализованный агрегат и курсор; `tennis_player_metric_snapshots` хранит неизменяемые point-in-time версии; `tennis_player_result_facts` хранит версии результатов отдельно по участникам с `played_at` и временем фактической доступности `available_at`. Поле `valid` — append-only коррекция: невалидная версия исключает результат из текущего агрегата, сохраняя старое значение для чтения `as_of` до момента исправления. При сборке метрик сначала выбирается последняя версия факта, затем применяются `valid` и временные ограничения. Новые более поздние матчи обновляют счётчики, Elo/Glicko-2/RD, форму, нагрузку и известные объёмы подачи инкрементально. Поздняя коррекция или out-of-order факт перестраивает метрики из выбранных версий фактов вне пути инференса. Сохранённая последовательность последних результатов позволяет пересчитать окна 90/365 дней при чтении без обхода полного архива. При коррекциях `A → B → A` сравнивается только последняя версия результата; старое совпадение `A` не подавляет новое исправление.

Метрики включают результат/объём по поверхности и уровню турнира, форму за последние 10/20 матчей и 90/365 дней, нагрузку и числители/знаменатели статистики подачи. Неизвестный объём не заменяется синтетическим числом. В текущем компактном Glicko-2 профиле соперник без известного рейтинга используется как prior 1500/RD 350; полноценный пересчёт по совместной рейтинговой истории, официальный рейтинг ATP/WTA и H2H остаются отдельными задачами. Профиль читается с `available_at <= forecast calculation time`; digest обоих профилей включён в forecast fingerprint, чтобы изменение истории инициировало прогноз и при неизменном score state.

Research предоставляет `GET /api/tennis-history/players/{participant_id}`. Backend отдаёт краткую метрику в карточке матча и профильный API. `last_full_history_scan_at` отделяет полный первоначальный/ежемесячный обход от обычной актуализации; между полными обходами загрузка продолжается до 30-дневного overlap от сохранённого source watermark. Ни подготовка игроков, ни прогноз не создают предложений ставок или операций bankroll.

### Подготовка участника и сохранённые метрики

Для одиночного теннисного события neural перед записью независимого прогноза вызывает `POST /api/tennis-history/prepare`. Research находит обоих участников по каноническим Fonbet ID/однозначным алиасам, параллельно актуализирует профили, а прогноз получает только после готовности обоих. Безопасное автоматическое сопоставление разрешено для совпавшего полного имени, а также для единственной пары с простой однословной фамилией и совпадающей первой буквой имени. Составные/дефисные фамилии, несколько частей имени, нечёткие совпадения и несколько подходящих профилей остаются в ручных подтверждениях. Неподтверждённые старые ссылки без записи в `tennis_player_identity_links` не считаются валидными. После подтверждения запускается импорт страниц до проверяемого конца доступной истории; если конец не подтверждён, профиль остаётся неполным. При последующем сбое подтверждённые ранее метрики допускаются со статусом `stale`. Подтверждённо пустая история допускает prior с высокой неопределённостью. Повторные score polls используют профильный кэш и не загружают историю заново.

Связь варианта Fonbet participant с Flashscore player ID хранится append-only в `tennis_player_identity_links` с `candidate`/`confirmed`/`revoked`, доказательствами и `available_at`. Однозначное совпадение полного имени или простой пары «полная фамилия + первая буква имени» подтверждается автоматически и может использовать общую историю профиля. Более сложные и неоднозначные формы сначала сохраняются как кандидат и требуют ручного решения. Существующий ручной маршрут `/api/tennis-history/players/{participant_id}/resolve` подтверждает выбранный ID новой версией, а перепривязка создаёт отзыв старой связи. Neural выбирает последнее решение на `as_of`; более поздний отзыв не должен возрождать старое подтверждение. Подтверждённые варианты Fonbet, ссылающиеся на один внешний ID, могут читать одну сохранённую историю, но исходные события и участники не сливаются.

`tennis_player_profiles` хранит актуальный агрегат и курсор истории; `tennis_player_metric_snapshots` — неизменяемые point-in-time версии; `tennis_player_result_facts` — версии результатов на уровне участника. Новые матчи дописываются инкрементально, а исправления/поздно импортированные матчи запускают пересборку из выбранных версий фактов. Окна формы и нагрузки истекают при чтении метрик из последней годовой последовательности; live-прогноз не сканирует весь архив. В профиле учитываются результаты и объёмы по поверхности/уровню, последние 10/20 матчей, окна 90/365 дней, Elo, Glicko-2/RD и известные числитель/знаменатель подачи. Если знаменатель подачи неизвестен, это хранится отдельно. В текущем компактном Glicko-2 профильном кэше неизвестный соперник получает prior 1500/RD 350; использование совместной хронологии рейтингов, официальный рейтинг ATP/WTA и личные встречи остаются последующим этапом.

Результат доступен только после фактического `available_at` и snapshot метрик выбирается с ограничением `available_at <= forecast calculated_at`. Версии источника `A → B → A` сохраняются, а повтор неизменного снимка подавляется. Версия/digest профилей обоих участников входит в объяснение и fingerprint прогноза, поэтому обновление метрик пересчитывает прогноз и при неизменном счёте. Research предоставляет `GET /api/tennis-history/players/{participant_id}` и `POST /api/tennis-history/refresh-due`; worker раз в час ставит в очередь ограниченную пачку отслеживаемых профилей, которые требуют суточной актуализации либо участвовали в завершившемся после предыдущей синхронизации матче. Backend отдаёт текущую сводку профиля в событии и проксирует просмотр. Подготовка профилей и forecast path не вызывают bet-manager и не создают операции virtual bankroll.


Очередь `tennis_player_refresh_jobs` хранит одно задание на участника, объединяет конкурентные запросы атомарным upsert и содержит статус, число попыток, lease, прогресс и ошибку. Worker забирает queued/просроченные задания через блокировку `SKIP LOCKED`; после перезапуска истёкший lease можно выполнить повторно. Работа скрейпера выполняется Research в отдельном потоке, HTTP endpoint только сохраняет задание.

Research принимает `RESEARCH_HTTP_PROXY` для публичных источников. `HTTP_PROXY`/`HTTPS_PROXY` маршрутизируют внешние HTTP-клиенты и Chromium, а `NO_PROXY` всегда дополняется внутренними Docker-хостами, включая MinIO и PostgreSQL, даже если окружение хоста уже задаёт собственное значение. Compose добавляет host-network relay, слушающий только адрес gateway проектной сети и перенаправляющий запросы в локальный proxy. На этом сервере UFW разрешает только TCP от `172.26.0.0/16` к `172.26.0.1:17890`; правило сохранено в `/etc/ufw/before.rules`. Внешний GET к Flashscore и запись в MinIO проверены из Research, в том числе после его перезапуска.

### Point-by-point история тенниса

Очковые ленты Flashscore хранятся отдельно от Fonbet score snapshots. Research загружает обычной Chromium-сессией доступные вкладки сетов, архивирует каждый полученный HTML в object storage и записывает `tennis_point_documents` с отдельными `retrieved_at`, `parsed_at`, `available_at`, content hash и версией парсера. Фактические ссылки на матч и вкладки PBP берутся из DOM профиля/страницы матча; на проверенной локали вкладки имеют путь `summary/point-by-point/set-N`. `tennis_point_history_versions` содержит неизменяемый результат разбора, порядок очков, счёт до/после, подающего, победителя и качество перехода. Парсер учитывает заголовки сетов и тай-брейка на русском и английском и структуру, где тай-брейк может располагаться вне основного history wrapper. Старые строки без доказуемого времени доступности имеют `availability_status=unknown_legacy` и не выдаются при `as_of`; время завершения разбора старого документа не переносится задним числом в прошлые live-срезы. Общий sport helper `is_legal_point_state` проверяет допустимость очкового состояния и используется парсером PBP и `neural` при чтении текущего счёта. Повтор соседнего документа с тем же хешем не добавляет подтверждений; изменившийся документ создаёт новую версию, включая возврат `A → B → A`. После изменения парсера новый parser version позволяет переобработать прежний HTML без изменения старых версий. Исторические страницы помечены `retrospective=true` и не становятся live-срезами задним числом.

Сопоставление хранится в `tennis_point_match_links`. Автоматическая связь допустима только при единственном совпадении обеих сторон, временного окна и турнира; если внешние ID игроков ещё не привязаны, допускается сравнение обеих полных имён вместе с датой и турниром. Одного имени или одной фамилии недостаточно; неоднозначность остаётся кандидатом до ручного подтверждения. Фактический URL матча берётся из ссылки, сохранённой в DOM профиля, когда он доступен. Чтение сначала выбирает последнюю на `as_of` версию назначения каждой связи, затем применяет её статус; старое подтверждение не воскресает после новой версии. API `GET /api/events/{event_id}/point-history` поддерживает `as_of`, возвращает `resolved_as_of`, стабильную пагинацию событий, а `include_versions=true` запрашивает историю версий; состояние задания выбирается из append-only событий статуса на тот же момент. Клиент фиксирует `resolved_as_of` из первой страницы и передаёт его при запросе следующих страниц. У каждой версии хранится set number даже при пустом разборе, но пустая страница без подтверждённой PBP-вкладки, с отсутствующей разметкой или fetch error не считается исправлением и не заменяет ранее принятую последовательность. Затереть предыдущий сет может только отдельная авторитетная версия с `explicit_empty_correction=true`; общая проверка `is_authoritative_point_history_version` используется в API, аудите и corpus review. Сырой HTML сначала сохраняется в MinIO, затем запускается парсер. Исследовательская карточка показывает число подтверждённых очков, время источника и разрывы. PBP не является обязательным условием baseline-прогноза по достоверному Fonbet state и не создаёт ставку.

Завершённые одиночные матчи Fonbet с числовым ID пополняют исторический PBP backfill независимо от шестичасового окна прогноза. Он переиспользует сохраняемую `tennis_point_refresh_jobs`, общие source slots и lease fencing; live задания имеют приоритет перед архивными. Перед PBP сборщик требует корректный завершённый результат и ищет архивный матч по двум подтверждённым профилям, дате и турниру. Неоднозначные кандидаты переходят в общую очередь ручных подтверждений, отклонённая пара исключается из последующих поисков. Архивные последовательности остаются retrospective: они могут подтвердить метку только для сохранённого Fonbet-среза с единственной совпадающей позицией. При импорте профилей пересекающиеся наборы Flashscore player/match ID сериализуются transaction advisory locks в детерминированном порядке; текущие projection и последнее observation читаются пакетно для всей страницы, что устраняет check-then-insert конфликт уникального match ID и заменяет несколько запросов на матч тремя пакетными чтениями. Для PBP-вкладок глобальный source regulator уже обеспечивает минимальный интервал запросов, поэтому повторная локальная пауза между сетами применяется только при автономном вызове без общего регулятора. Formatter Research принудительно использует UTC независимо от `TZ`: время событий и `available_at` в БД остаётся UTC, и логовый суффикс `UTC` теперь совпадает с фактическим временем, а не с локальным временем контейнера.

Аудит допуска (`scripts/research/tennis_label_audit.py`) строит один immutable manifest на общих матчах и временных границах, маскируя неизвестные цели. В train/validation/holdout считаются только уникальные матчи, у которых есть метка конкретной цели; число всех матчей окна выводится отдельно как `candidate_matches`. В хронологию входят только реальные `available_at`/`observed_at` официального результата: `scheduled_start_at` и время завершения не заменяют время знания результата. Для поиска доказанного Fonbet snapshot аудитор использует точный score и индивидуальный `first_seen_at`; сохраняемое время берётся из последнего найденного точного snapshot как консервативная верхняя граница доступности. Поиск идёт индексом `(event_id, observed_at)` и не восстанавливает более раннее знание по расписанию. Каждый выровненный пример записывается в JSONL provenance артефакт со ссылкой на Fonbet-срез, PBP-документ/версию и связь сторон. Аудит не открывает обучение, пока не пройдены фиксированные пороги четырёх целей и не сертифицирована доступность каждого входного признака.

Версионированный вход кандидатов `tennis_odds_free_v1` задаётся `ODDS_FREE_FEATURE_NAMES` в `services/neural/app/tennis_forecast.py`: Elo и разница Elo, 90-дневная форма и объём истории обоих игроков, маски отсутствующих метрик, сглаженная вероятность подачи и вес выборки, сет/гейм/очковое состояние, подающий, тай-брейк, формат best-of и поверхность. Builder принимает только эти спортивные значения; коэффициенты и рыночные вероятности игнорируются. Сохраняемый baseline forecast теперь использует тот же `ODDS_FREE_FEATURE_VERSION`; прежний литерал `tennis_odds_free_v2` не соответствовал allowlist и заменён, уже сохранённая история не переписывается. В live/replay состояние ограничивается `EventStateSnapshot.observed_at`, метрики — `TennisPlayerMetricSnapshot.available_at`, статистика подачи — собственным `available_at`. Новый вход или изменение времени его источника требует повышения версии признаков и повторного допуска.

Сбор live point history запускается через durable `tennis_point_refresh_jobs`; worker проверяет очередь, Research берёт lease и повторяет сбор с интервалом 30 секунд. Полный импорт профилей хранится в `flashscore_import_jobs`, прогресс страницы и профиля пишется в БД, а повторный вызов маршрута коалесцируется в почасовое задание с версией парсера в ключе. Каждый браузерный обход профиля выполняется в отдельном процессе с жёстким таймаутом; по превышении процесс завершается, а его checkpoint продолжает durable job после перезапуска. Запросы к Flashscore проходят через PostgreSQL очередь с приоритетами live PBP, подготовка участника, затем архив; каждая заявка обновляет heartbeat, lease продлевается во время ожидания и работы, а строки без heartbeat 15 секунд удаляются даже при ещё действующем lease. Каждая операция держит advisory lock на непулируемом соединении; записи результата по игроку дополнительно требуют актуальные token, running status и lease. Повторная обработка архивных строк разрешена для назначения фактов после позднего подтверждения личности; неизменённые факты подавляются по последней версии и остаются идемпотентными. Neural и API профиля скрывают legacy-метрики, пока нет действующей подтверждённой связи, и отклоняют кэш, чей source ID конфликтует с выбранной связью. Checkpoint хранит страницу, match IDs, архивный документ и следующее действие; при восстановлении текущая первая страница сравнивается с архивом, а сохранённый префикс принимается только при непрерывной нумерации и проверяемом содержимом. Ответ пустой страницы (`0`/пустое тело) считается завершением полной доступной истории только после второго последовательного HTTP 200 и точного совпадения `allEventsCount` с уникальными одиночными ID; нестабильный ответ, несовпадение счётчика, сетевой сбой, лимит страниц или неподдерживаемое тело оставляют импорт неполным. `allEventsCount` в Flashscore относится к одиночным результатам, тогда как общий feed включает парные/смешанные события; общий и одиночный счётчики архивируются отдельно. Парные строки остаются в источниковых документах, но не становятся метками результата одиночного игрока. Парсер новой версии может повторно обработать проверенные документы предыдущих версий по профилю, feed URL, номеру страницы и SHA-256, не загружая неизменённые страницы повторно. Полный импорт различает завершённые исходы и исключённые строки с причиной; если новая версия парсера снимает прежнюю метку, она дописывает `valid=false` fact и пересчитывает snapshot, не удаляя старую версию. Интервал между операциями не менее двух секунд. Ошибки получают backoff; завершённый матч получает статус `finalized` только после подтверждённой полной загрузки и явного маркера конца. Токены попыток fencing-ят просроченные процессы; запись результата требует действующий токен, `running` и неистёкший lease. Завершения и статусы записываются транзакционно, а append-only история доступна для `as_of`. Разбиение эксперимента записывается в единый хешированный manifest по целым матчам и применяется ко всем целям с масками меток; качество закрытого holdout не открывается до выбора кандидата. В развернутом окружении egress проходит через host relay на gateway проектной сети и ограниченное UFW правило. Runtime-проверка одного завершённого матча подтвердила три архивных сета, 224 подтверждённых ретроспективных события и непустой `as_of` API. Приёмка полного этапа всё ещё требует ручной сверки 20 матчей и тестов восстановления/fencing очереди.

## Отдельный исполнитель Flashscore

Сервис `flashscore-worker` собирается из Research image отдельной командой и исполняет durable очереди профилей, point history и архивного импорта. Он раз в 15 секунд ищет одиночные теннисные события: live с Fonbet-обновлением не старше 90 секунд и prematch до шести часов. Сначала он подготавливает оба профиля, затем ставит PBP только live-матчам. Общий Redis worker больше не dispatch-ит эти задания, HTTP Research не подбирает archive jobs при включённом dedicated executor. Существующие API подготовки/dispatch/import остаются совместимыми.

В dedicated worker default executor ограничивается `FLASHSCORE_BROWSER_PROCESSES` (по умолчанию 2, максимум 4). Chromium/Playwright pool thread-local, поэтому этот лимит уменьшает число browser owners внутри worker. Дополнительно Research и worker используют общий Docker volume с двумя OS file-lock слотами: каждый слот удерживается с момента запуска Chromium до закрытия браузера, а ядро освобождает его при падении процесса. Это охватывает также изолированные scraper subprocess, которые не ограничивались executor threads. При отсутствии свободного слота новая задача ждёт заданный `FLASHSCORE_BROWSER_SLOT_WAIT_SECONDS`; общий Postgres source limiter независимо ограничивает сетевые операции и интервалы запросов.

После часового прогона в поток добавлены `tennis_match_lookup_jobs` и поле `tennis_player_refresh_jobs.next_run_at`. Ошибки поиска профиля хранят причину и backoff; подтверждённый ответ без профиля перепроверяется через шесть часов, а неоднозначная личность ждёт ручного подтверждения. Match lookup работает только с подтверждёнными Flashscore ID обоих игроков. Снимок общей live-страницы переиспользуется 30 секунд; уникальное совпадение по двум ID создаёт версионируемую связь и немедленно будит PBP. PBP без ID или связи остаётся в `waiting_players`/`waiting_match` и не считается просроченным. Неоднозначные ссылки попадают в существующий поток ручного разрешения. Очередь показывает поиск матча, коды причины, следующую попытку, число новых PBP-документов и подтверждённых связей за последний час.

Проверка live-поиска выявила, что серверный HTML показывал кнопку раньше, чем Flashscore подключал её обработчик, а появившееся поле имело текстовый тип и placeholder `Type your search here`. Поиск теперь дожидается завершения загрузки страницы, нажимает видимую кнопку `aria-label=Search` и выбирает видимое поле по placeholder. Контрольный запрос `Arantxa Rus` вернул профиль `Rus Arantxa` (`84GyAaXB`) без автоматического изменения подтверждённых связей. При последней runtime-проверке 11 матчей оставались подходящими, 3 игрока были подготовлены; PBP-документы и связи за последний час не появились, так как соперники части матчей ещё не имеют подтверждённых профилей.

Глобальный регулятор `flashscore_source_control` (миграция 026) ограничивает всех исполнителей двумя активными запросами и интервалом не менее двух секунд между стартами. Заявки хранятся в `flashscore_source_requests`; слот назначается короткой транзакцией, а PostgreSQL-соединение закрыто на время сети. Live PBP сохраняет приоритет, архив повышается после 10 секунд ожидания. Cooldown при распознанном 403/429 временно снижает лимит до одного запроса. API очереди `GET /api/tennis-history/flashscore/queue` и статуса `GET /api/events/{event_id}/flashscore-research` только читают БД. Вкладка планировщика показывает эту очередь отдельно от существующего web research.

После закрытия подтверждения задание со старым статусом `waiting_confirmation` может остаться без активной заявки из-за прежнего поведения очереди. При dispatch Research теперь проверяет такие строки: если заявка ещё `pending`, задание остаётся замороженным; если заявка закрыта или отсутствует, поиск возобновляется без автоматического одобрения кандидата. Для матчевого задания сначала возобновляется только поиск источника, а PBP остаётся в `waiting_match` до установленной связи. На preflight-срезе 2026-10-21 обнаружено 199 таких заданий; после выкладки повторный проход вернул 200 заданий в очередь, оставил ноль ожиданий без pending-заявки и создал три новые неоднозначные заявки с тремя корректно замороженными заданиями.

Экспериментальный odds-free контракт `tennis_odds_free_v1` хранит упорядоченный allowlist в `services/neural/app/tennis_forecast.py`. Группы происхождения — метрики игроков, serving-оценки каждого участника, состояние события и покрытие — перечислены рядом с признаками. `validate_odds_free_feature_availability` отвергает отсутствующий или будущий source timestamp, кроме явно указанной группы с точным contract default. Default не получает фиктивного source timestamp: он отражается отдельно в provenance и должен соответствовать пропуску/приору в фактическом feature vector. Point-in-time сертификация не означает достаточность или качество наблюдаемых спортивных данных.

Для прогноза tennis score format `best_of` должен быть явно известен как 3 или 5. Если Fonbet metadata его не содержит или содержит неподдерживаемое значение, live parser и prematch baseline сохраняют недоступность `UNSUPPORTED_FORMAT`; они не предполагают best-of-3. Это предотвращает ложные вероятности матча и сценарии при неизвестном формате. Формат входит в odds-free feature contract и подлежит той же проверке времени доступности.

Сценарные ветви на 3 очка и 2 гейма объединяются по полному конечному состоянию, включая подающего и тай-брейк. Нельзя отбрасывать маловероятные состояния ради UI top-N: оставшийся набор перенормируется до вероятностной массы 1. Завершённый матч является поглощающим состоянием. Regression в октябре 2026 выявила потерю массы при усечении неизвестного подающего до восьми исходов; усечение удалено и добавлены тесты для deuce, tie-break, best-of-5 и terminal match.

Поиск Flashscore-профиля использует потокобезопасный process-level `httpx.Client` с keep-alive пулом максимум из двух соединений. Каждый реальный GET отдельно резервирует durable source slot, поэтому повторное использование TCP/TLS не обходит общий интервал или concurrency limit. Пул закрывается при завершении Research/worker. Парный короткий замер на 2026-10-21 для одинакового поискового GET показал медиану 0.21665 с с новым клиентом против 0.07025 с с pooled connection; это latency endpoint, не end-to-end throughput. Подробности — `docs/research/flashscore_http_pool_benchmark_2026-10-21.json`.

`services/neural/app/independent_tennis_models.py` содержит fixture-only основу последовательного odds-free GRU: статические признаки игрока плюс до 64 доступных состояний с интервалом и маской пропусков, отдельные heads point/game/set/match, loss маскирует неизвестные цели, веса нормируются по целому матчу. Префикс выбирает только `observed_at <= as_of`, сжимает соседние одинаковые polling-снимки, но сохраняет последовательности `A → B → A`. Checkpoint фиксирует feature allowlist/version, цели, seed и архитектуру; CPU загрузка parity-tested. Этот модуль не строит выборку из БД и не запускает обучение.

Если dispatch отзывает устаревший архивный player-refresh lease ради участника текущего live/prematch события, профиль не остаётся в UI в ложном состоянии `running`: при наличии метрик он переводится в `stale`, без метрик — в `pending`, с причиной `REFRESH_PREEMPTED_FOR_CURRENT_EVENT`. Ранее сохранённые метрики не меняются; старый изолированный subprocess остаётся ограждён fencing token и не может опубликовать результат.

Свежий рабочий preflight от 07:50 UTC сохранён в `docs/research/tennis_label_audit_2026-10-21_0750.json`, manifest `tennis_training_manifest_6d13c80e7fdd.json`. В manifest 1,860 матчей; point/game/set размечены в 22 матчах глобально, но только 15 на общей timeline (train 6, validation 2/2/1/1, holdout 3), все PBP ретроспективны. Матчевая цель имеет размеры train 892, validation 149 каждое, holdout 372, но все temporal precedence folds провалились. Feature PIT 0/3,266, 470 результатов без доказанного source-времени. Gate `blocked`; holdout quality закрыт.

Инкрементальный refresh известного Flashscore-профиля ограничен отдельным deadline (по умолчанию 120 секунд), поскольку он читает только recent-overlap окно; первичная полная история сохраняет 300 секунд. Настройки — `FLASHSCORE_TENNIS_INCREMENTAL_TIMEOUT_SECONDS` и `FLASHSCORE_TENNIS_PROFILE_TIMEOUT_SECONDS`. Timeout прекращает изолированный Chromium-процесс, оставляет задание повторяемым и не удаляет ранее сохранённые метрики. Выкладка 2026-10-21 16:22 UTC прошла с healthy Research/worker; первый короткий API snapshot не является замером ускорения. Post-change soak и парное измерение profile/PBP latency остаются обязательными.

Последний preflight срез после runtime soak сохранён в `docs/research/tennis_label_audit_2026-10-21_0623.json`, общий manifest — `docs/research/tennis_training_manifest_c51b3556c006.json`; сводка всех срезов и provenance в `docs/research/tennis_training_preflight_2026-10-21.md`. На 06:23 UTC рабочая БД содержит 2,299 подходящих одиночных событий, 2,132 матчевые метки и PBP-метки в 12–13 уникальных матчах: 233 point, 229 game и 23 set примера. Сохранено 79 версий PBP-документов для 20 внешних матчей и 20 связей; все обучающие PBP-исходы ретроспективны. Общий manifest содержит 1,840 датированных матчей, из них 1,472 development и 368 закрытый holdout. На общей временной линии игровых целей семь матчей: два в начальном train, по одному в каждом validation и один в holdout; сетовая цель дополнительно не имеет обоих классов во всех окнах. Матчевая цель проходит пороги, общий gate `blocked`. У 456 событий нет доказанного времени доступности результата, а feature-level PIT certification не подключена к dataset builder. Архивный `available_at` означает фактическое время повторного получения и не восстанавливает историческую доступность. Holdout quality не открывался, обучение независимой модели не запускалось.

При принятии завершённого официального результата `EventResultMatcher` записывает `available_at` в `fonbet_official_result`. Для того же результата сохраняется первое время наблюдения, новая официальная коррекция получает собственное время. Legacy исходы без доказуемого времени не датируются `scheduled_start_at` или `finished_at` и не входят в обучение.

Очередь поддерживает серверные фильтры по типу, статусу, режиму и поисковой строке, стабильную пагинацию и отдельное включение завершённых задач за последние сутки. Её сводка не зависит от фильтра страницы и показывает число подходящих событий, подготовленных профилей, заданий по статусам, требующих идентификации игроков, просроченных live PBP, активных сетевых слотов и последнее heartbeat executor. Список обновляется вместе с вкладкой scheduler; если запрос API не удался, интерфейс оставляет последнее успешное значение и помечает его устаревшим.

Счётчик просроченного live PBP включает только runnable `queued`/`retry` задания с подтверждённой связью источника и истёкшим `next_run_at`. `running`, `waiting_players`, `waiting_match` и `waiting_confirmation` не считаются просроченными только из-за старой даты предыдущей попытки.

## Административные подтверждения внешних данных

Неоднозначные связи Flashscore с участником Fonbet или событием Fonbet сохраняются в общей таблице `manual_confirmation_requests`; типизированные факты подтверждения и отказа дополняются append-only аудитом `manual_confirmation_decisions`. Первая версия обрабатывает профиль игрока и внешний матч, схема допускает новые типы ручной проверки. Для каждого кандидата показывается отдельная заявка с доказательствами и парой действий «Да»/«Нет». Решение проверяет версию заявки и выполняется в одной транзакции с изменением append-only identity/match links и постановкой следующего durable job. Пока заявка имеет статус `pending`, её evidence и версия неизменны; связанное задание хранится в `waiting_confirmation` без lease и токена попытки. Планировщики подготовки, live-поиска, суточного обновления и архивного backfill пропускают эту сущность, поэтому ожидание не занимает сетевой слот. Подтверждение закрывает заявку и возобновляет зависимое задание; отказ закрывает только эту пару и возобновляет поиск, если для сущности не осталось других ожидающих решений.

Подтверждение профиля запускает обновление метрик. Отказ сохраняет только отклонённую пару Fonbet participant ↔ Flashscore player, исключает этот ID из следующих поисков и ставит новое задание профиля. Подтверждение матча фиксирует порядок сторон и ставит PBP; отказ сохраняет пару event ↔ source match и повторно ищет матч, не присоединяя отвергнутый кандидат к другому событию. При отсутствии альтернатив очередь показывает `NO_ALTERNATIVE_PROFILE_DATA` или `NO_ALTERNATIVE_MATCH_DATA`; профиль/PBP остаются без подтверждённых входов.

Административная карточка является сохраняемым снимком предложения. Если профильная строка `tennis_player_profiles` отсутствует к моменту решения, backend восстанавливает её из канонического Fonbet participant и сохранённых `candidate_id`/`profile_url` в заявке. Отсутствующая денормализованная строка не делает доступный в снимке Flashscore-кандидат недоступным; неполным считается только само сохранённое предложение без стабильного source ID или URL.

Ручная проверка нужна для составных или неразрешённых личностей. Помимо полного имени в том же порядке, автоматически подтверждается только имя из ровно двух простых слов «полная фамилия + инициал» против ровно двух слов Flashscore, где полная фамилия совпадает и первое имя кандидата начинается с того же символа; порядок двух слов может различаться. Дефисы, дополнительные части, несколько инициалов, близкие написания, несовпавшая первая буква, несколько безопасных кандидатов и конфликт с ранее подтверждённым ID остаются на ручную проверку. Старые pending-заявки проходят то же правило; автоматическое решение создаёт identity link и audit event, ставит обновление профиля и закрывает конкурирующие заявки.

Админская сессия выдаётся backend после сверки настроенных `ADMIN_USERNAME` и SHA-256 digest `ADMIN_PASSWORD_SHA256`; подписанный короткоживущий HttpOnly/SameSite cookie подписывается `ADMIN_SESSION_SECRET`. Если серверная конфигурация отсутствует, mutating confirmation endpoints закрыты. Research-only legacy resolve route требует `ADMIN_INTERNAL_TOKEN`. Frontend больше не содержит парольный digest. Решения не создают ставки и не меняют ledger.

### Независимый tennis PIT-аудит и повторный разбор PBP

`scripts/research/tennis_label_audit.py` формирует единый whole-match manifest и provenance каждой цели. Для каждого сохранённого примера он также проверяет timestamp источников закрытого odds-free контракта (`player_metrics`, обе стороны подачи, `event_state`, `event_surface`) относительно времени Fonbet-среза. Отсутствующий timestamp или время позже среза оставляет пример несертифицированным. Текущий audit fail-closed: timestamp состояния известен, но метрики игроков, подача и покрытие не имеют достаточной point-in-time истории; такие признаки нельзя заменять сегодняшней версией профиля или ретроспективной статистикой.

Финальный training gate требует одновременного прохождения размеченных уникальных матчей и обоих классов в initial train, четырёх expanding validation и закрытом holdout; временного предшествования меток train прогнозам validation; PIT каждого входа; и доказанного времени доступности каждого результата. Любая неудача оставляет `blocked`. Независимый контракт `tennis_odds_free_v1` — ровно 30 спортивных полей без коэффициентов и производных рынка; это отдельная линия от существующего baseline `tennis_market_v4`. Fixture-only LR/GB и GRU в `services/neural/app/independent_tennis_models.py` принимают признаки явно, не подключены к рабочему загрузчику/реестру, применяют маски неизвестных целей, match-balanced weights, causal GRU до 64 состояний, фиксированный seed, CPU fallback и версионированное сохранение checkpoint. Наличие этого кода не означает обучения модели.

Срез после исправления неавторитетных пустых PBP-версий 2026-10-21 08:32 UTC: point/game/set имеют по 24 матча с метками и 759/452/52 размеченных примера; match — 2,162. Odds-free feature PIT 0/3,382. Единый manifest содержит 1,870 матчей (1,496 development / 374 закрытый holdout); метрики holdout не раскрывались. Пороговые игровые цели не выполнены, temporal precedence не подтверждён, feature PIT не сертифицирован; независимое обучение заблокировано. Интеграционная проверка `as_of` подтверждает историю `A → B → A`, неизменность старого чтения после поздней неавторитетной страницы и явного снятия связи; отдельный PostgreSQL test подтверждает fencing истёкшего refresh lease/token. Отдельный тест процесса перезапуска браузерного worker ещё не выполнен. После фикса backend, Research и flashscore-worker пересобраны; API реально отдал 164 PBP-события на контрольном связанном матче. Подробности и provenance находятся в `docs/research/tennis_training_preflight_2026-10-21.md` и связанных JSON/JSONL артефактах.

После 30-минутного soak 2026-10-21 08:42–09:12 UTC (60/60 queue API HTTP 200 и healthy worker samples, без cooldown, максимум два сетевых слота) свежий независимый audit содержит 408 PBP-документов по 41 внешнему матчу и 45 Fonbet-связей. Метки point/game/set есть только в 25 уникальных матчах на цель (793/468/54 тренировочных примера); match — 2,174. Observed PBP — 0; odds-free feature PIT — 0/3,489. Manifest `tennis_training_manifest_1a88d8469f69.json`: 1,882 матчей, 1,505 development и 377 закрытый holdout. Gate `blocked`: игровые цели существенно ниже минимума, метки train не предшествуют validation прогнозам и признаки не сертифицированы. В последнем soak sample был один кратковременный overdue PBP; контрольная API-проверка через три минуты показала ноль. Техническая сверка 20 PBP-архивов без score/server/TB mismatch и материалы сохранены в `docs/research/tennis_pbp_manual_review_2026-10-21.*`. Свежие детали, backlog очереди и неразрешённые проверки — в `docs/research/tennis_training_preflight_2026-10-21.md`.

После этого baseline-периода runtime выявил, что profile parser иногда вызывал `asyncio.run()` из потока с уже запущенным event loop. Добавлен `_run_browser_awaitable`, который переносит coroutine в отдельный event-loop thread для такого sync caller; regressions тестируют этот путь. Research + tennis point-history suite прошёл 116 тестов, обновлённые Research и Flashscore worker развернуты. Контейнеры были пересозданы, пока глобальный source limiter показывал 1/2 активных слота и очередь — один running job; новый executor восстановил dispatch и heartbeat, активные DB leases валидны, profile imports снова проходят. Новый 30-минутный soak после фикса ещё собирается; предыдущий soak и его audit являются baseline до данного исправления, а не финальной runtime-приёмкой.

Player profile refresh выполняется через отдельный `spawn` process, чтобы timeout или потеря lease завершали назначенный Chromium. Child process пишет page checkpoints только при текущих `participant_id`, fencing token, `running` status и неистёкшем lease; parent продлевает lease heartbeat и прекращает child при потере владельца. После rollout финального process-isolated пути сервисы поднялись из пересоздания во время активного Flashscore slot и возобновили dispatch. Research+tennis point-history suite: 116 tests passed. 30-минутный post-rollout soak записывается в `docs/research/flashscore_soak_2026-10-21_after-profile-child.jsonl`; начальный snapshot после рестарта временно показывал два overdue PBP, поэтому acceptance зависит от дальнейшей динамики очереди.

Завершившиеся player scraping и archive import children обязательно `join()`-ятся, чтобы не накапливать zombie process. После этого финальные образы снова пересобраны и пересозданы; API, Research и worker поднялись healthy. Финальный read-only soak `docs/research/flashscore_soak_2026-10-21_post-process-reaping.jsonl` завершён: 60/60 HTTP 200, worker healthy 60/60, cooldown отсутствовал, слоты ≤2. В 10 из 60 замеров был один overdue PBP, последний и отдельный итоговый queue snapshot показывали 0. После soak аудит `docs/research/tennis_label_audit_2026-10-21_final.json` (10:05:52 UTC) создал manifest на 1,902 матча, но gate остался закрыт: point/game/set — по 25 размеченных матчей, 0 observed PBP; match — 2,194; PIT — 0/3,509; 498 result timestamps неизвестны, 420 размеченных исходов исключены из manifest; временной порядок train labels и validation forecasts не проходит. Holdout quality не открывался. Кодовые тесты после сборки: research/PBP 65, fencing integration 1, odds-free model primitives 17 passed. Никакого обучения или SHADOW не выполнялось.

Парсер `flashscore-point-history-v4` сохраняет завершение стандартного тай-брейка отдельным `terminal_from_tiebreak_result` событием. Это событие даёт исход текущего tie-break/game и финальный счёт сета `[7,6]` либо `[6,7]`, но не считается дополнительным очком. Активная сторона подачи извлекается только из единственного узла `matchHistoryRow__servis`, помеченного `matchHistoryRow__highlight`; у Flashscore есть placeholder для обеих сторон, поэтому порядок DOM-узлов не является признаком подачи. Если уникального маркера нет, подающий остаётся неизвестным.

Версия v4 исправляет дефект v3, который выбирал последний placeholder и фактически записывал неверную сторону подачи. Сохранённые версии v3 неизменяемы; 2026-10-21 append-only replay добавил 122 версии v4 из 209 документов (87 соседних повторов/дубликатов подавлены, отсутствующих архивов и parse errors нет). Для последующих replay утилита подавляет только соседнее повторение одного content hash, сохраняя переход A→B→A отдельным событием. Прежнее чтение `as_of=2026-10-21T07:23:33.334131Z` для Fonbet event `5fd2d2ab-6230-4b26-9818-702d1d3674b6` осталось полностью идентичным после replay; новое чтение использует v4 и оставляет подачу неизвестной там, где нет уникального source marker.

Read-only source review 2026-10-21 сравнил архивы 30 связанных матчей: у 20 найдены point rows (177 геймов, 925 исходных состояний, 152 deuce/advantage-события), в источнике обнаружено 14 очков тай-брейка. Состояния очков, известные маркеры подачи и маркер победителя тай-брейка совпали с оригинальными DOM-узлами (0 расхождений). Из 177 проверенных геймов у 127 не было уникального server marker; это `unknown`, не догадка. В 10/30 архивов point rows отсутствовали или не распознались. Полные данные: `docs/research/tennis_pbp_source_review_2026-10-21_postv4.json`.

Повторная source parity проверка 08:18 UTC использует все извлечённые parser point states, включая `ambiguous`, чтобы source review измерял точность разбора DOM отдельно от пригодности label. В матче Tamm–Shepp один первый переход 0:0→30:0 остаётся ambiguous из-за пропуска промежуточного очкового состояния; он сравнивается с источником, но не подтверждается для обучения. Сводка 30 матчей: 19 с PBP, 169 геймов, 882 состояния, 144 deuce/advantage, 28 tie-break rows, ноль расхождений; подробности `docs/research/tennis_pbp_source_review_2026-10-21_0820.json`. Двенадцать матчей имеют явные пробелы/отсутствующую PBP-разметку, а ручная проверка оригиналов остаётся открытой.

Расширенный read-only срез 08:19 UTC охватил 36 уникальных связанных матчей; 25 содержали PBP, проверено 222 гейма и 1,169 состояний, 198 deuce/advantage и 39 tie-break rows. Score/server/tie-break mismatch — 0; 12 матчей имеют gaps или отсутствующую разметку. Артефакт: `docs/research/tennis_pbp_source_review_2026-10-21_0821.json`. Автоматическая проверка не закрывает требование ручного осмотра исходного HTML.

Очковый label audit выравнивает подтверждённый переход по уникальному счёту/позиции, даже если источник не сообщает подающего; если подающий известен, он должен совпасть со снимком. Повторившийся идентичный счёт остаётся неоднозначным и исключается. Для неизвестных значений сохраняется признак missing; это не основание подставлять сторону. Аудит 07:29 UTC после v4 находит 20 матчей/616 очковых примеров (301/315 классы), 20/365 геймовых, 20/42 сетовых и 2,148 матчевых. Все PBP-примеры retrospective. PIT остаётся `0/3,171`, 472 результата не имеют надёжного source timestamp, walk-forward и временной порядок не проходят; обучение запрещено. Полный JSON, provenance и общий manifest — `docs/research/tennis_label_audit_2026-10-21_postv4.json` и соседние артефакты.

Изменение parser version само по себе не меняет старые выводы. `scripts/research/reparse_tennis_point_documents.py` читает сохранённые исходные HTML из object storage и добавляет новую пару DB document/history-version с текущими `parsed_at`/`available_at`; исходный `retrieved_at`, старые версии и прежнее `as_of` остаются неизменными. Утилита по умолчанию dry-run; `--apply` явно включает append-only запись. `scripts/research/tennis_pbp_corpus_review.py` создаёт read-only structural consistency отчёт по последним подтверждённым связям и версиям. Такой отчёт не подменяет осмотр оригинальных HTML человеком.

Синхронный Flashscore PBP-fetcher переиспользует Chromium в границах одного worker-потока через `ThreadLocalChromium`; каждый матч получает новый browser context, который закрывается после задания. Browser process перезапускается после 50 заданий, 15 минут или необработанной ошибки навигации. Это убирает повторный запуск Chromium между PBP-заданиями, сохраняя изоляцию cookies/storage. Настройки `FLASHSCORE_BROWSER_MAX_JOBS` и `FLASHSCORE_BROWSER_MAX_AGE_SECONDS` задаются отдельно. Частоту сетевых операций по-прежнему обеспечивает глобальный source regulator; production paired benchmark числа запусков, времени и памяти остаётся отдельной проверкой.

Когда для матча уже подтверждён стабильный Flashscore ID, PBP-сбор начинает с `/summary/point-by-point/`, пропуская загрузку summary page. При 404 или отсутствующих сетовых вкладках используется прежний summary-first discovery; архивирование, парсер и правила качества не меняются. Direct raw HTTP GET не заменяет браузер: проверенный ответ не содержал PBP DOM. Read-only пилот на `WAlDxZFD` собрал те же два сета за три навигации direct-index против четырёх summary-first; задержка сети менялась от 13.5 до 36.4 секунд на тех же данных, поэтому это измерение количества запросов, а не устойчивого speedup. Подробности: `docs/research/flashscore_pbp_route_benchmark_2026-10-21.json`, `docs/research/tennis_point_parser_benchmark_2026-10-21.json`; полный throughput benchmark и process-launch comparison остаются открыты.

The dedicated executor writes its process liveness heartbeat from a daemon thread rather than the asyncio dispatch loop. Database locks or long Chromium coordination can delay the dispatcher without falsely marking an otherwise working process unhealthy. Durable job ownership, fencing, and result writes remain controlled by their existing leases; heartbeat does not extend job leases. A regression test verifies the heartbeat thread writes independently. After redeploy on 2026-10-21 10:30 UTC, the queue API reported `healthy=true`, a fresh heartbeat, and the worker resumed PBP dispatch with two running jobs; this smoke check is not a substitute for a long soak or restart/fencing verification.

Профильная очередь планируется по текущей ценности события: сначала участники live-матчей с Fonbet-наблюдением не старше 90 секунд, затем участники prematch-матчей в окне 0–6 часов, после них — исторический backlog. В срезе 14:08 UTC обнаружено, что старая сортировка только по `next_run_at` отправляла первые слоты на задания участников уже неактуальных событий возрастом более двух часов; 17 первых записей не относились к текущему окну. Диспетчер теперь сортирует по классу актуальности и, когда старые активные попытки занимают все слоты, отзывает их fencing token и lease; дочерний скрапер видит потерю владения и завершается, а его результат отклоняется. Архивные задания сохраняются и продолжают работу после актуальных событий. Приоритет и выборка preemption покрыты SQL-регрессиями; throughput после выкладки проверяется отдельно.

Preflight после этой правки (2026-10-21 14:17 UTC) построил общий manifest на 2,015 матчах, но он не открывает допуск: P/G/S имеют по 31 размеченному матчу (22 на общей хронологии; train 9, validation 3/1/2/1, holdout 6), а матчевые метки нарушают train-label-before-validation порядок во всех fold. В live-сборе и в обучающем архиве наблюдавшихся PBP по-прежнему 0; исторические последовательности ретроспективны. Полные counts и ограничения записаны в `docs/research/tennis_training_preflight_2026-10-21_queue_priority.md`. Рабочее обучение и SHADOW остаются закрыты.

Источник времени результата отдельно проверен в коде: `CanonicalMatchResult.available_at` optional, а `EventResultMatcher` заполняет его текущим `utc_now()`, если источник не передал значение. Для старых Fonbet-результатов это время текущего получения, не историческая публикация. Оно сохраняется без backdating; более ранний cutoff допускается только при доказательстве из архивного первичного документа. Текущая odds-free схема audit/inference — `tennis_odds_free_v1`; отдельного `v2` writer в коде не найден.

The tennis training audit may use a current, confirmed Flashscore match link and the archived `tour_level`/gender to improve retrospective ATP/WTA/Challenger/ITF reporting where Fonbet tournament metadata is insufficient. Mixed `atp_wta` and female Challenger levels are normalized using the archived match gender. The report records which source supplied each category. These retrospective labels are descriptive audit metadata only: they are excluded from the odds-free feature vector, PIT certification, and the chronological input manifest.

The 2026-10-21 post-heartbeat 30-minute soak completed with 60/60 successful queue API samples and a healthy executor in every sample, while respecting the two-slot source cap. Thirteen samples briefly showed overdue PBP and the final queue returned zero; this is evidence of recovery, not a zero-lag guarantee. The regenerated audit classifies confirmed archived tour metadata only for retrospective reporting (never model inputs) and still blocks training: point/game/set are far below admission counts, train labels were first observed after some validation forecasts, and no feature set has complete point-in-time provenance. See `docs/research/tennis_training_preflight_2026-10-21.md` for counts and artifacts.

For confirmed live links found before a profile-history row exists, PBP collection now reuses the durable link's Flashscore URL after validating its host, match path, and both confirmed player IDs, then reconstructs a canonical summary URL and side order. If direct point-by-point navigation times out or returns 404, collection retries the confirmed summary URL and continues set-tab discovery. This repaired a real `unmatched` case for Fonbet `69148307` and archived the source page, but that page contained no point history; it remains in retry with `point_history_unavailable`. A confirmed URL and successful HTTP response do not certify that Flashscore has published PBP data.


#### Проверка сценарного движка и повторный допуск — 2026-10-21

После устранения обрезания ветвей при неизвестном подающем создана и выкачена новая сборка `neural`. SHA-256 исходного `tennis_forecast.py` совпадает с файлом в контейнере, healthcheck healthy. Odds-free forecast и fixture-only LR/GB/GRU прошли 21 тест. Свежий audit `docs/research/tennis_label_audit_2026-10-21_after_scenario_fix.json` (12:01 UTC) построил manifest `tennis_training_manifest_after_scenario_fix.json`: 1,961 матч, 1,568 development / 393 закрытый holdout. P/G/S имеют по 26 размеченных матчей, но только 19 на общей временной линии; train 9, validation 3/4/0/3, holdout 0. Match содержит 2,253 метки, но временной порядок train→validation не проходит ни в одном fold. Feature PIT 0/3,649; 484 result timestamps неизвестны, 420 меток исключены. Качество holdout не открывалось, gate остаётся `blocked`, рабочее обучение и SHADOW не выполнялись.


Официальные результаты и их исправления сохраняются в `Event.metadata_json.fonbet_official_result_observations` как последовательность доступных Neurobet версий. Повтор неизменённого результата не добавляет запись; correction и возврат `A → B → A` дают три версии со своими `available_at`. Исторические события без достоверного времени этой логикой не backfill-ятся. Это сохраняет временную историю новых результатов, но не восстанавливает отсутствующие старые наблюдения; аудит продолжает исключать неизвестные времена.


`TennisPipeline` пишет live-снимки как `status=live`; официальный result matcher отдельно переводит `Event` в finished. Поэтому старые расписания и `finished_at` не используются как время появления результата. Для последующих наблюдений matcher сохраняет явную version history; старые строки без первичного доказательства остаются исключёнными из PIT admission.


Live PBP discovery может вернуть `matched`, `ambiguous` или отсутствие связи. Потребитель обязан проверять tagged union до чтения match ID. Для `ambiguous` он сохраняет все кандидаты и подтверждения, затем паркует PBP job в `waiting_confirmation`; такой job не должен занимать browser/source slot или повторять поиск до решения. Ошибка формы ответа не считается сетевым retry.

Выкладка 2026-10-21 добавила проверку tagged-union результата live discovery, сохранение кандидатных Fonbet↔Flashscore связей и парковку PBP задания при неоднозначности. URL каждого кандидата валидируется через allowlist Flashscore, а query `mid` должен совпадать с идентификатором источника. Unit/integration suite и runtime health проверены; полное поведение на реальном неоднозначном live-матче, отсутствие lease во время ожидания и пробуждение PBP после решения остаются отдельным acceptance gate.

Для ускорения локального PBP-разбора Research устанавливает `lxml` и использует C-backed HTML tree; встроенный `HTMLParser` остаётся fail-safe fallback для других потребителей общего tennis package. Версия парсера — `flashscore-point-history-v5`; on-disk raw HTML и формат событий не меняются. На 30 документах v5 дал 3.55× parse throughput (рост на 254.6%) при точном совпадении 240/240 event signatures; fallback совпадает по результатам на parser fixtures, но не получает ускорения lxml. Это не оценивает сетевую задержку получения страницы.

Admission report рабочей БД от 2026-10-21 12:49 UTC: [`tennis_label_audit_2026-10-21_post-lxml.json`](docs/research/tennis_label_audit_2026-10-21_post-lxml.json), общий manifest — `tennis_training_manifest_da9f2222643d.json`. На timeline есть 28 матчей с метками point/game/set и 1,986 с матчевой меткой; P/G/S распределены по окнам 9 train, 3/0/3/1 validation и 5 holdout, матч — 952 train, 159 в каждом validation и 398 holdout. Holdout quality не открывались. Время доступности архивных результатов позже части исторических validation forecasts, а feature PIT сертифицировано для 0/3,754 примеров; admission остаётся `blocked`, обучение запрещено до новых доказательств и повторного допуска.

#### Уточнение PIT-источников и контрактных priors — 2026-10-21

Прогнозный `tennis_odds_free_v1` читает поверхность только при наличии `metadata_json.surface_available_at <= calculation cutoff`; простое наличие поля `surface` в текущих метаданных не является историческим доказательством. При отсутствии времени или при будущем timestamp используется `unknown`. Идентичный упорядоченный feature vector теперь входит в fingerprint forecast, поэтому изменение спортивного входа не теряется как повтор неизменного счёта.

Label audit проверяет те же источники, что и inference: metric snapshots с их `available_at` и действующей на срезе подтверждённой Flashscore-идентичностью; serve в метриках либо `ExtTennisServeLog` по точному нормализованному полному имени и окну 365 дней; сохранённый Fonbet state; surface с её отдельным временем доступности. Когда источник не был доступен, audit допускает только явные значения самого контракта (player prior + missing flag, serve prior `0.62` + sample weight `0`, surface `unknown`) и считает их отдельно как `contract_defaulted`, а не как наблюдённое покрытие. Будущий/поздний источник нельзя подставить вместо prior.

Последний срез `docs/research/tennis_label_audit_2026-10-21_feature_provenance.json` от 14:05 UTC проверил 3,977 примеров: PIT-проверка входов пройдена для всех только потому, что каждый пропущенный источник явно прошёл через контрактный default; полностью наблюдавшиеся player metrics есть у 63 примеров. У serve и surface нет безопасно сопоставленных наблюдений. `source_available_at` остаётся null для default; audit отдельно пишет `contract_defaulted_sources`, не приписывая prior выдуманное время источника. Это сертифицирует причинность fallback, но не качество и не достаточность спортивных данных. Исторический v1 исключает Glicko и H2H; они не должны молча добавляться в тот же контракт.

Тот же отчёт оставляет admission `blocked`: P/G/S имеют по 31 уникальному размеченному матчу (1,009 point, 596 game, 67 set examples), все метки retrospective, observed PBP — 0; общая timeline даёт 9 initial-train, validation 3/1/2/1 и holdout 6 матчей. Для match есть 2,305 меток; общая manifest timeline — 2,013 матчей (966 train, 161 в каждом validation, 403 holdout), но все validation-folds нарушают temporal precedence: часть train результатов стала известна только 2026-10-21 после прогнозов 12–14 октября. Из 2,499 допустимых событий 484 результата без доказанного времени доступности; 420 подтверждённых исходов исключены из manifest. Holdout quality не открывалось. Обучение LR/GB/GRU и SHADOW остаются закрыты.

Повторный рабочий admission audit после live PBP-проверки сохранён в `docs/research/tennis_label_audit_2026-10-21_after_live_pbp_check.json` (2026-10-21 13:20 UTC), с единым manifest `tennis_training_manifest_89fd0bc624ef.json` и examples provenance `tennis_training_examples_c94c03672579.jsonl`. В текущем срезе 29 уникальных P/G/S-матчей и 2,286 матчевых меток; в общем хронологическом manifest для игровых целей остаются 22 матча: initial train 9, validation 3/0/3/1, holdout 6. Для match это 957 train, 160/159/159/160 validation и 399 holdout. Все четыре временных validation-folds провалены, поскольку часть меток стала известна позже прогнозных срезов. Из 3,800 проверенных odds-free примеров ни один не имеет полного PIT-доказательства входов; holdout quality не открывались. У обоих проверенных live Flashscore матчей (`d0zFZ3Fj`, `dI1TK15L`) связи и URL валидны, но сохранённые HTML содержат Summary/Odds/H2H/Draw без PBP/set links; подтверждённая связь сама по себе не означает наличие очковой истории. Обучение остаётся закрытым, пороги не меняются.

Если у полученной страницы нет source-provided PBP/set tabs, Research архивирует именно summary HTML с `fetch_status=point_history_tab_missing`, `set=null` и `collection_status=PBP_TAB_NOT_PUBLISHED`; сборщик не синтезирует сет 1 и не отправляет лишний запрос на несуществующую вкладку. Для других неполных результатов `TennisPointRefreshJob.last_error` сохраняет детальную причину (`result.reason`), а не теряет её за общим `partial`. Это состояние не считается пустым исправлением и не удаляет предшествующую авторитетную последовательность.

Происхождение PBP-документа определяется ближайшим `EventStateSnapshot` с `observed_at <= retrieved_at`: только свежий (не старше 90 секунд) snapshot со статусом `live`, при отсутствии завершения матча на момент получения, позволяет поставить `retrospective=false`. Время `available_at` и каждого события не переносится назад к предполагаемому моменту розыгрыша; точная очковая позиция всё равно должна однозначно выравниваться с сохранённым Fonbet-срезом. Все прочие документы остаются retrospective.

После выкладки этой логики повторный аудит 2026-10-21 13:33 UTC сохранён в `docs/research/tennis_label_audit_2026-10-21_after_live_provenance_fix.json`, manifest `tennis_training_manifest_015d7f19e00a.json`. В срезе 29 P/G/S матчей (train 9, validation 3/0/3/1, holdout 6), матч — 2,289 (train 958, validation 160/160/159/160, holdout 400). Все временные validation-folds провалены; PIT — 0/3,848, observed PBP — 0; holdout quality закрыто. Новый provenance-контракт ещё не получил реальной live PBP последовательности и пока подтверждён unit-тестами, не фактическим production-документом.

#### Глобальный предел Chromium и async profile fetch — 2026-10-21

Лимит в два браузера реализован общими kernel `flock` слотами в Docker volume, смонтированном одновременно в Research и `flashscore-worker`. Слоты используются sync PBP browser pool и async Playwright profile pagination; при завершении процесса ядро освобождает lock. Обнаруженный в runtime async profile launcher, который обходил sync pool, переведён на `ThreadLocalChromium.browser_slot()`. Регрессии блокировки: async и sync pool конкурируют за общий лимит; прежние 58 Research/Flashscore тестов прошли до rollout. После сборки нового image Research и worker используют одинаковый digest. Короткая runtime-проверка видела два живых browser roots в worker, ноль в Research; async acquisition в Research завершился ожидаемым `FLASHSCORE_BROWSER_SLOT_TIMEOUT`. Первый worker memory sample был около 930 MiB. Эти снимки подтверждают lock-путь, но стабильность под длительной ротацией ещё проверяется 30-минутным soak в `docs/research/flashscore_soak_2026-10-21_async-profile-slot.jsonl` и `docs/research/flashscore_resources_2026-10-21_async-profile-slot.jsonl`.

Для ускорения следующих Research builds Docker context исключает локальные архивы (`OnlineData`, `models`, `backups`), research отчёты и frontend; Dockerfile их не копирует. Это снижает передачу контекста с сотен MiB до нескольких MiB и не влияет на данные, монтируемые runtime.

#### Итог soak и ограничение повторного admission audit — 2026-10-21

После добавления общего async/sync browser-slot lock завершён 30-минутный read-only soak (15:31:54–16:01:54 UTC). Все 60 запросов очереди ответили HTTP 200; latency p50 0.591 s, p95 1.461 s, максимум 5.243 s. Heartbeat Flashscore worker был healthy во всех 60 срезах, cooldown не включался, суммарное число Chromium root-процессов не превышало 2. Максимальные Docker memory readings: worker 929.8 MiB / 2 GiB, Research 192.7 MiB / 1.5 GiB. Из 55 измерений ресурсов одно завершилось `docker stats` timeout; API и worker в это время продолжили работу. `overdue_pbp` временами достигал 2 из-за ограниченной конкурентности PBP и оставался видимым в сохраняемой очереди; потерянных задач не выявлено. Сырые измерения находятся в `docs/research/flashscore_soak_2026-10-21_async-profile-slot.jsonl` и `docs/research/flashscore_resources_2026-10-21_async-profile-slot.jsonl`.

Последний полностью завершённый label audit — 2026-10-21 14:17 UTC (`docs/research/tennis_label_audit_2026-10-21_queue_priority.json`). Он нашёл 31 уникальный матч с P/G/S-метками: 9 матчей в первом train, 3/1/2/1 в четырёх validation-окнах и 6 в holdout; все эти PBP-метки retrospective, observed PBP — 0. Match-цель имела 2,307 меток, но temporal precedence не прошёл ни один validation fold: доступность части train результатов зафиксирована 21 октября после forecast-срезов 12–14 октября. Матчевые coverage минимумы пройдены, качество holdout не открывалось. Feature contract `tennis_odds_free_v1` прошёл causal audit для 3,979 примеров через явные contract defaults, но полностью наблюдавшиеся metric snapshots были только у малой доли входов; defaults не подтверждают полноту или качество признаков.

Повторный полный audit был запущен после soak, но остановлен до завершения, когда PostgreSQL достиг примерно 1.999 GiB из лимита 2 GiB. Сервис оставался healthy; частичный пустой вывод не является отчётом. Безопасная census-проверка на 16:06 UTC показала 635 PBP documents/versions, 59 source match IDs, 67 distinct confirmed Fonbet event links и 3,152 numeric Fonbet tennis events. Эти агрегаты не равны пересчитанным обучающим меткам и не заменяют новую версию manifest. См. `docs/research/tennis_training_preflight_2026-10-21_async-profile-slot.md`. Обучение LR/GB/GRU остаётся закрыто до полного аудита с минимальными метками по каждой цели и доказанной временной доступностью.

#### Исправление ошибочного ручного решения — 2026-10-21

Запросы админских подтверждений можно исправить после решения, пока это последнее terminal-решение по сущности и нет нового ожидающего предложения. В API передаётся противоположное решение с текущей версией; исходная заявка сохраняется, а `ManualConfirmationDecision` append-only журналируется с полем `corrected_previous_status`. Подтверждённая player identity при исправлении на reject получает новую temporal link-версию `rejected`, удаляется из текущего `source_identities` и ставится повторный поиск. Если по ошибочной identity уже импортировались результаты, система добавляет `valid=false` версии этих фактов с причиной `admin_rejected_profile_identity` и новую метрику-снимок, исключающую отклонённого профиля; прежние факты, снимки и ссылки не переписываются, поэтому фиксированный `as_of` остаётся воспроизводимым. Исправление reject на approve создаёт новую `confirmed` link-версию и восстанавливает только факты, инвалидированные этим исправлением, после чего ставится обновление профиля. UI показывает «Отменить подтверждение» / «Отменить отказ» только для последнего исправляемого решения. Superseded, устаревшие решения и заявки с новым pending предложением защищены `409`.

В 16:13 UTC вручную исправлена ошибочная связь «Иванович И» ↔ Flashscore `Ivanovic Gala` (`xAN9eFGN`, request `0984f81c-2c24-4df6-8024-9ebd9f5e4816`): approved заменён на rejected, профиль поставлен на повторный поиск, этот ID добавлен в rejection list. Предыдущая отдельная связь того же Flashscore ID с «Иванович Г» сохранена. API/DB подтверждают новое состояние; журнал содержит оба решения с версиями 1 и 2.

#### Безопасный потоковый admission audit — 2026-10-21 16:17 UTC

Полный PIT label audit переведён на потоковое чтение больших наборов PBP-версий и лёгкие отдельные счётчики документов, чтобы не загружать весь архив в ORM-списки при лимите PostgreSQL 2 GiB. Отчёт `docs/research/tennis_label_audit_2026-10-21_streaming.json` построил общий manifest `tennis_training_manifest_2026-10-21_streaming_retry.json` (2,060 целых матчей; 1,648 development; 412 закрытый holdout; digest `aa4483be6a99ab4ec8755fbbca2987838f3eb609b61fc845e67971cd6910857f`). Gate остаётся `blocked`: point/game/set — по 31 уникальному матчу (train 9, validation 3/1/2/1, holdout 6), все игровые последовательности retrospective; match — 2,352 метки и количественные минимумы проходят, но train-label availability позже validation forecasts во всех четырёх folds. Odds-free PIT проверен на 4,024 примерах с явными defaults; реальные metric snapshots есть у 69, serve/surface наблюдений нет. Holdout quality не вычислялось. Подробные counts, причины и дальнейшие блокеры: `docs/research/tennis_training_preflight_2026-10-21_streaming.md`.

#### Доказательство более раннего времени результата — 2026-10-21

Для legacy-события более раннее время знания результата допустимо только если сохранённый `EventStateSnapshot` уже содержит полный numeric match score, и все упорядоченные числовые токены точно совпадают с позднее проверенным official final score. Используется `observed_at` этого конкретного Fonbet-снимка; `scheduled_start_at` и изменяемый `Event.finished_at` не подставляются и метаданные результата не переписываются. Эти исходные версии сохраняются в audit provenance. Начальная проверка 20 матчей нашла точное совпадение у 19; в этих 19 наблюдение предшествовало сегодняшнему времени дозаписи metadata. До завершения полного пересчёта admission manifest остаётся прежним и обучение не допускается. Поиск точных счетов в audit выполняется отдельным `event_id` lookup с точным regex-фильтром и `LIMIT 1`, используя индекс события/времени; широкие `OR`-пачки по hypertable упирались в timeout. Полный результат после смены метода пока не получен.

#### Транслитерация и автоматическое подтверждение Flashscore — 2026-10-21

Автоподтверждение сокращённого имени разрешено только при точном совпадении фамилии после нормализации распространённых вариантов транслитерации и совпадении первой буквы полного имени с инициалом; составные и дефисные имена остаются ручными. Для Flashscore учитываются варианты `ж↔zh/j`, `ш↔sh/s`, `ц↔ts/c`, `ч↔ch/c/tch`, `х↔kh/h`, а также распространённые варианты `я/ю/е`. Источники и исходные написания сохраняются без переписывания. На live-странице повторные ссылки с одним `mid` сворачиваются в один матч-кандидат; связь матча проверяется по двум отдельным URL-slug профилей с точным ID, а не по произвольному вхождению подстроки. Единственная пара подтверждённых профилей автоматически связывается с одним найденным live-матчем; если для той же пары остаётся несколько разных `mid`, они требуют ручного выбора.

Свежий 30-минутный runtime soak после slug matching и heartbeat healthcheck завершён 18:00–18:31 UTC: 59 запросов, 57 HTTP 200 и 2 таймаута по 10 секунд; среди успешных ответов latency p50 0.606 s, p95 2.071 s, максимум 9.562 s. Worker heartbeat был healthy во всех 57 успешных срезах, cooldown не включался, `overdue_pbp` оставался 0. Сводка eligible events колебалась от 0 до 25; зафиксированные rolling counts — 12–16 документов и 12 подтверждённых связей. Отдельный 11-минутный срез ресурсов не зафиксировал ошибок: максимум worker 510.7 MiB / 2 GiB, Research 201.4 MiB / 1.5 GiB и два Chromium root-процесса. Два timeout означают, что безошибочный API soak и восстановление после искусственно воспроизведённого I/O stall не подтверждены; исходные данные сохранены в `docs/research/flashscore_soak_2026-10-21_post-stale-health.jsonl` и `docs/research/flashscore_resources_2026-10-21_post-stale-health-labels.jsonl`.

По результату проверен и ускорен backend queue API: job-запросы теперь ограничены текущими eligible events плюс завершениями за последние 24 часа, выборка player jobs ограничена участниками актуальных матчей, а история связей использует только последнюю доступную версию каждого source match. Backend пересобран и healthy. Десять последовательных запросов с `limit=50` дали HTTP 200, p50 0.057 s, максимум 0.349 s; worker healthy и `overdue_pbp=0` во всех ответах. API regression на bounded projection прошёл (1 passed). Это smoke после изменения, не заменяет 30-минутный soak.

Для устаревших заявок связи матча добавлена автоматическая reconciliation-проверка: она закрывает заявку только если у события остался один pending кандидат, у обеих сторон есть однозначные подтверждённые профили Flashscore, `mid` совпадает и ID профилей находятся в двух отдельных match slug. Другой pending кандидат, конфликт уже подтверждённой связи, отказ пары или неполные IDs оставляют ручное подтверждение. Системное решение append-only журналируется, версия заявки повышается, PBP job возобновляется. Recovery запустили на реальном сохранённом наборе: две заявки (Адкар—Шкутова и Ла Серна—Торрес) были единственными кандидатами и прошли точную проверку; обе стали `approved` с `system:auto_exact_confirmed_player_ids`, обе матчевые связи получили `matched`, PBP jobs — `queued`. Point-history API для обоих событий отвечает 200, пока `total=0`; фактическая загрузка документов ещё ожидается. Regression helper: `test_confirmation_recovery.py` — 5 passed.

В `tennis_flashscore._name_word_groups` сохранена кириллическая `й` при Unicode NFKD-folding: буква временно защищается от разложения в `и + combining breve`, затем сравнивается с распространёнными написаниями `i/y/j`. Это устраняет ложное расхождение фамилий вида `Чайковская` ↔ `Caykovskaya` и не расширяет замену `i` на `y` для всего имени. Для `ч/ц/ш/ж` продолжают применяться варианты `ch/c`, `ts/c`, `sh/s`, `zh/j`; автоподтверждение требует полного совпадения фамилии по варианту транслитерации и первой буквы имени, многословные/дефисные фамилии остаются ручными. Regression tests покрывают варианты фамилий и инициалов и отрицательный похожий вариант; это проверка правила, не подтверждение конкретной личности без условий matcher.
