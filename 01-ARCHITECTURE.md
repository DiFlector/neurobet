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

Доступ к Flashscore проходит через durable PostgreSQL очередь и глобальный регулятор из миграции 026. Два сетевых слота назначаются короткой транзакцией, между стартами выдерживается минимум две секунды, а сетевой запрос выполняется без открытого соединения БД. Приоритеты: live point-by-point — 0, подготовка участника — 10, архивная страница — 20; архив повышается до ранга 5 после десяти секунд ожидания. Cooldown при распознанной блокировке снижает число слотов до одного. Очередь, lease и занятые слоты сохраняются в `flashscore_source_requests` и `flashscore_source_control`.

Первый контрольный набор завершил доступный обход десяти профилей: Antonius 53, Djokovic 1 591, Alcaraz 502, Sinner 593, Swiatek 613, Sabalenka 787, Gauff 683, Medvedev 838, Zverev 1 142 и Fritz 924 распознанных завершённых матчей. Числа относятся к результатам каждого профиля и пересекаются между участниками; это не количество уникальных матчей. Полные повторы Sinner и Gauff вернули те же 593 и 683 записи и не добавили внешних матчей; Gauff продолжился из checkpoint после страницы 14. Поиск профиля также выполняется одной Chromium-сессией без предварительного дублирующего GET. Для фонового импорта lease продлевается heartbeat только при совпадении текущего attempt token и неистёкшего lease. Advisory lock источника остаётся session-level на время запроса, но SQL-транзакция закрывается на время сетевой операции.
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

Сопоставление хранится в `tennis_point_match_links`. Автоматическая связь допустима только при единственном совпадении обеих сторон, временного окна и турнира; если внешние ID игроков ещё не привязаны, допускается сравнение обеих полных имён вместе с датой и турниром. Одного имени или одной фамилии недостаточно; неоднозначность остаётся кандидатом до ручного подтверждения. Фактический URL матча берётся из ссылки, сохранённой в DOM профиля, когда он доступен. Чтение сначала выбирает последнюю на `as_of` версию назначения каждой связи, затем применяет её статус; старое подтверждение не воскресает после новой версии. API `GET /api/events/{event_id}/point-history` поддерживает `as_of`, возвращает `resolved_as_of`, стабильную пагинацию событий, а `include_versions=true` запрашивает историю версий; состояние задания выбирается из append-only событий статуса на тот же момент. Клиент фиксирует `resolved_as_of` из первой страницы и передаёт его при запросе следующих страниц. Для каждого документа сохраняется номер сета также при пустом разборе, чтобы новая версия сета замещала прежнюю последовательность. Сырой HTML сначала сохраняется в MinIO, затем запускается парсер. Исследовательская карточка показывает число подтверждённых очков, время источника и разрывы. PBP не является обязательным условием baseline-прогноза по достоверному Fonbet state и не создаёт ставку.

Завершённые одиночные матчи Fonbet с числовым ID пополняют исторический PBP backfill независимо от шестичасового окна прогноза. Он переиспользует сохраняемую `tennis_point_refresh_jobs`, общие source slots и lease fencing; live задания имеют приоритет перед архивными. Перед PBP сборщик требует корректный завершённый результат и ищет архивный матч по двум подтверждённым профилям, дате и турниру. Неоднозначные кандидаты переходят в общую очередь ручных подтверждений, отклонённая пара исключается из последующих поисков. Архивные последовательности остаются retrospective: они могут подтвердить метку только для сохранённого Fonbet-среза с единственной совпадающей позицией.

Аудит допуска (`scripts/research/tennis_label_audit.py`) строит один immutable manifest на общих матчах и временных границах, маскируя неизвестные цели. В train/validation/holdout считаются только уникальные матчи, у которых есть метка конкретной цели; число всех матчей окна выводится отдельно как `candidate_matches`. В хронологию входят только реальные `available_at`/`observed_at` официального результата: `scheduled_start_at` и время завершения не заменяют время знания результата. Каждый выровненный пример записывается в JSONL provenance артефакт со ссылкой на Fonbet-срез, PBP-документ/версию и связь сторон. Аудит не открывает обучение, пока не пройдены фиксированные пороги четырёх целей и не сертифицирована доступность каждого входного признака.

Версионированный вход кандидатов `tennis_odds_free_v1` задаётся `ODDS_FREE_FEATURE_NAMES` в `services/neural/app/tennis_forecast.py`: Elo и разница Elo, 90-дневная форма и объём истории обоих игроков, маски отсутствующих метрик, сглаженная вероятность подачи и вес выборки, сет/гейм/очковое состояние, подающий, тай-брейк, формат best-of и поверхность. Builder принимает только эти спортивные значения; коэффициенты и рыночные вероятности игнорируются. В live/replay состояние ограничивается `EventStateSnapshot.observed_at`, метрики — `TennisPlayerMetricSnapshot.available_at`, статистика подачи — собственным `available_at`. Новый вход или изменение времени его источника требует повышения версии признаков и повторного допуска.

Сбор live point history запускается через durable `tennis_point_refresh_jobs`; worker проверяет очередь, Research берёт lease и повторяет сбор с интервалом 30 секунд. Полный импорт профилей хранится в `flashscore_import_jobs`, прогресс страницы и профиля пишется в БД, а повторный вызов маршрута коалесцируется в почасовое задание с версией парсера в ключе. Каждый браузерный обход профиля выполняется в отдельном процессе с жёстким таймаутом; по превышении процесс завершается, а его checkpoint продолжает durable job после перезапуска. Запросы к Flashscore проходят через PostgreSQL очередь с приоритетами live PBP, подготовка участника, затем архив; каждая заявка обновляет heartbeat, lease продлевается во время ожидания и работы, а строки без heartbeat 15 секунд удаляются даже при ещё действующем lease. Каждая операция держит advisory lock на непулируемом соединении; записи результата по игроку дополнительно требуют актуальные token, running status и lease. Повторная обработка архивных строк разрешена для назначения фактов после позднего подтверждения личности; неизменённые факты подавляются по последней версии и остаются идемпотентными. Neural и API профиля скрывают legacy-метрики, пока нет действующей подтверждённой связи, и отклоняют кэш, чей source ID конфликтует с выбранной связью. Checkpoint хранит страницу, match IDs, архивный документ и следующее действие; при восстановлении текущая первая страница сравнивается с архивом, а сохранённый префикс принимается только при непрерывной нумерации и проверяемом содержимом. Ответ пустой страницы (`0`/пустое тело) считается завершением полной доступной истории только после второго последовательного HTTP 200 и точного совпадения `allEventsCount` с уникальными одиночными ID; нестабильный ответ, несовпадение счётчика, сетевой сбой, лимит страниц или неподдерживаемое тело оставляют импорт неполным. `allEventsCount` в Flashscore относится к одиночным результатам, тогда как общий feed включает парные/смешанные события; общий и одиночный счётчики архивируются отдельно. Парные строки остаются в источниковых документах, но не становятся метками результата одиночного игрока. Парсер новой версии может повторно обработать проверенные документы предыдущих версий по профилю, feed URL, номеру страницы и SHA-256, не загружая неизменённые страницы повторно. Полный импорт различает завершённые исходы и исключённые строки с причиной; если новая версия парсера снимает прежнюю метку, она дописывает `valid=false` fact и пересчитывает snapshot, не удаляя старую версию. Интервал между операциями не менее двух секунд. Ошибки получают backoff; завершённый матч получает статус `finalized` только после подтверждённой полной загрузки и явного маркера конца. Токены попыток fencing-ят просроченные процессы; запись результата требует действующий токен, `running` и неистёкший lease. Завершения и статусы записываются транзакционно, а append-only история доступна для `as_of`. Разбиение эксперимента записывается в единый хешированный manifest по целым матчам и применяется ко всем целям с масками меток; качество закрытого holdout не открывается до выбора кандидата. В развернутом окружении egress проходит через host relay на gateway проектной сети и ограниченное UFW правило. Runtime-проверка одного завершённого матча подтвердила три архивных сета, 224 подтверждённых ретроспективных события и непустой `as_of` API. Приёмка полного этапа всё ещё требует ручной сверки 20 матчей и тестов восстановления/fencing очереди.

## Отдельный исполнитель Flashscore

Сервис `flashscore-worker` собирается из Research image отдельной командой и исполняет durable очереди профилей, point history и архивного импорта. Он раз в 15 секунд ищет одиночные теннисные события: live с Fonbet-обновлением не старше 90 секунд и prematch до шести часов. Сначала он подготавливает оба профиля, затем ставит PBP только live-матчам. Общий Redis worker больше не dispatch-ит эти задания, HTTP Research не подбирает archive jobs при включённом dedicated executor. Существующие API подготовки/dispatch/import остаются совместимыми.

После часового прогона в поток добавлены `tennis_match_lookup_jobs` и поле `tennis_player_refresh_jobs.next_run_at`. Ошибки поиска профиля хранят причину и backoff; подтверждённый ответ без профиля перепроверяется через шесть часов, а неоднозначная личность ждёт ручного подтверждения. Match lookup работает только с подтверждёнными Flashscore ID обоих игроков. Снимок общей live-страницы переиспользуется 30 секунд; уникальное совпадение по двум ID создаёт версионируемую связь и немедленно будит PBP. PBP без ID или связи остаётся в `waiting_players`/`waiting_match` и не считается просроченным. Неоднозначные ссылки попадают в существующий поток ручного разрешения. Очередь показывает поиск матча, коды причины, следующую попытку, число новых PBP-документов и подтверждённых связей за последний час.

Проверка live-поиска выявила, что серверный HTML показывал кнопку раньше, чем Flashscore подключал её обработчик, а появившееся поле имело текстовый тип и placeholder `Type your search here`. Поиск теперь дожидается завершения загрузки страницы, нажимает видимую кнопку `aria-label=Search` и выбирает видимое поле по placeholder. Контрольный запрос `Arantxa Rus` вернул профиль `Rus Arantxa` (`84GyAaXB`) без автоматического изменения подтверждённых связей. При последней runtime-проверке 11 матчей оставались подходящими, 3 игрока были подготовлены; PBP-документы и связи за последний час не появились, так как соперники части матчей ещё не имеют подтверждённых профилей.

Глобальный регулятор `flashscore_source_control` (миграция 026) ограничивает всех исполнителей двумя активными запросами и интервалом не менее двух секунд между стартами. Заявки хранятся в `flashscore_source_requests`; слот назначается короткой транзакцией, а PostgreSQL-соединение закрыто на время сети. Live PBP сохраняет приоритет, архив повышается после 10 секунд ожидания. Cooldown при распознанном 403/429 временно снижает лимит до одного запроса. API очереди `GET /api/tennis-history/flashscore/queue` и статуса `GET /api/events/{event_id}/flashscore-research` только читают БД. Вкладка планировщика показывает эту очередь отдельно от существующего web research.

Экспериментальный odds-free контракт `tennis_odds_free_v1` хранит упорядоченный allowlist в `services/neural/app/tennis_forecast.py`. Группы происхождения — метрики игроков, serving-оценки каждого участника, состояние события и покрытие — перечислены рядом с признаками. `validate_odds_free_feature_availability` отвергает отсутствующий источник или источник позже `as_of`; это не подменяет timestamps источника. Пока текущие метрики не имеют подтверждённых индивидуальных времён доступности, обучение и point-in-time сертификация остаются закрытыми.

При принятии завершённого официального результата `EventResultMatcher` записывает `available_at` в `fonbet_official_result`. Для того же результата сохраняется первое время наблюдения, новая официальная коррекция получает собственное время. Legacy исходы без доказуемого времени не датируются `scheduled_start_at` или `finished_at` и не входят в обучение.

Очередь поддерживает серверные фильтры по типу, статусу, режиму и поисковой строке, стабильную пагинацию и отдельное включение завершённых задач за последние сутки. Её сводка не зависит от фильтра страницы и показывает число подходящих событий, подготовленных профилей, заданий по статусам, требующих идентификации игроков, просроченных live PBP, активных сетевых слотов и последнее heartbeat executor. Список обновляется вместе с вкладкой scheduler; если запрос API не удался, интерфейс оставляет последнее успешное значение и помечает его устаревшим.

## Административные подтверждения внешних данных

Неоднозначные связи Flashscore с участником Fonbet или событием Fonbet сохраняются в общей таблице `manual_confirmation_requests`; типизированные факты подтверждения и отказа дополняются append-only аудитом `manual_confirmation_decisions`. Первая версия обрабатывает профиль игрока и внешний матч, схема допускает новые типы ручной проверки. Для каждого кандидата показывается отдельная заявка с доказательствами и парой действий «Да»/«Нет». Решение проверяет версию заявки и выполняется в одной транзакции с изменением append-only identity/match links и постановкой следующего durable job. Пока заявка имеет статус `pending`, её evidence и версия неизменны; связанное задание хранится в `waiting_confirmation` без lease и токена попытки. Планировщики подготовки, live-поиска, суточного обновления и архивного backfill пропускают эту сущность, поэтому ожидание не занимает сетевой слот. Подтверждение закрывает заявку и возобновляет зависимое задание; отказ закрывает только эту пару и возобновляет поиск, если для сущности не осталось других ожидающих решений.

Подтверждение профиля запускает обновление метрик. Отказ сохраняет только отклонённую пару Fonbet participant ↔ Flashscore player, исключает этот ID из следующих поисков и ставит новое задание профиля. Подтверждение матча фиксирует порядок сторон и ставит PBP; отказ сохраняет пару event ↔ source match и повторно ищет матч, не присоединяя отвергнутый кандидат к другому событию. При отсутствии альтернатив очередь показывает `NO_ALTERNATIVE_PROFILE_DATA` или `NO_ALTERNATIVE_MATCH_DATA`; профиль/PBP остаются без подтверждённых входов.

Административная карточка является сохраняемым снимком предложения. Если профильная строка `tennis_player_profiles` отсутствует к моменту решения, backend восстанавливает её из канонического Fonbet participant и сохранённых `candidate_id`/`profile_url` в заявке. Отсутствующая денормализованная строка не делает доступный в снимке Flashscore-кандидат недоступным; неполным считается только само сохранённое предложение без стабильного source ID или URL.

Ручная проверка нужна для составных или неразрешённых личностей. Помимо полного имени в том же порядке, автоматически подтверждается только имя из ровно двух простых слов «полная фамилия + инициал» против ровно двух слов Flashscore, где полная фамилия совпадает и первое имя кандидата начинается с того же символа; порядок двух слов может различаться. Дефисы, дополнительные части, несколько инициалов, близкие написания, несовпавшая первая буква, несколько безопасных кандидатов и конфликт с ранее подтверждённым ID остаются на ручную проверку. Старые pending-заявки проходят то же правило; автоматическое решение создаёт identity link и audit event, ставит обновление профиля и закрывает конкурирующие заявки.

Админская сессия выдаётся backend после сверки настроенных `ADMIN_USERNAME` и SHA-256 digest `ADMIN_PASSWORD_SHA256`; подписанный короткоживущий HttpOnly/SameSite cookie подписывается `ADMIN_SESSION_SECRET`. Если серверная конфигурация отсутствует, mutating confirmation endpoints закрыты. Research-only legacy resolve route требует `ADMIN_INTERNAL_TOKEN`. Frontend больше не содержит парольный digest. Решения не создают ставки и не меняют ledger.
