# ⚡ Neurobet

![Version](https://img.shields.io/badge/version-2.0-blue.svg)
![Python](https://img.shields.io/badge/Python-3.11-green.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)
![Next.js](https://img.shields.io/badge/Next.js-15-black.svg)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)

**Neurobet** — современная система автоматического парсинга LIVE матчей и коэффициентов с Fonbet, сохранением снимков в SQLite и интерактивным отображением истории коэффициентов в графиках.

---

## ✨ Основные возможности

* **🔄 Автоматический парсинг каждую минуту**: Фоновую работу обеспечивает APScheduler в бэкенде.
* **📊 График истории коэффициентов**: Наведение на коэффициент открывает всплывающий график (Recharts) с первой записью, текущим значением, минимумом и максимумом.
* **🎨 Движение коэффициентов (Динамическая подсветка)**:
  * 🔴 **Красная кнопка & кэф** (`#d63031` / `#ff7675`): коэффициент **вырос**.
  * 🟢 **Зеленая кнопка & кэф** (`#00b894` / `#55efc4`): коэффициент **упал**.
  * ⚪ **Серый цвет по умолчанию**: коэффициент **без изменений**.
* **🛡️ Безопасный режим (Safe Mode)**: Переключатель, скрывающий опасные коэффициенты (`< 1.1` или `> 2.1`). Включен по умолчанию.
* **💾 Размер базы данных на диске**: Отображение актуального размера файла SQLite (`autobet.db`) прямо в шапке UI.
* **⚽ Поддержка кириллицы и видов спорта**: Корректный поиск и фильтрация футбола, хоккея, баскетбола, тенниса и киберспорта благодаря кастомной `py_lower` функции в SQLite.
* **🧠 NeuralBet ML**: GRU + LightGBM ensemble с as-of статистикой команд/игроков, sibling-coherence и единым predict path (UI / bot / backtest).
* **🐳 Полная контейнеризация**: Запуск бэкенда и фронтенда одной командой через `docker compose`.

---

## 🏗️ Архитектура проекта

```
neurobet/
├── backend/                  # FastAPI (парсинг, admin proxy, MCP)
├── ai_service/               # PyTorch GRU + LightGBM, обучение/инференс
│   └── app/neuralbet/model_registry.py  # реестр моделей, .nbmodel.zip
├── frontend/autobet/         # Next.js UI + админка
├── shared/                   # neurobet_features, neurobet_filters
├── data/                     # prod: postgres + models
├── data-dev/                 # dev: отдельная БД и модели
├── docker-compose.yml        # prod stack
├── docker-compose.dev.yml    # dev overlay (параллельно с prod)
├── docker-compose-cuda.yml   # GPU overlay (prod или dev)
├── .env.example              # prod template
├── .env.dev.example          # dev template
└── infrastructure/nginx-dev-location.snippet
```

---

## Prod vs Dev

| | Prod | Dev |
|---|------|-----|
| URL | `https://necrolich.ru/diflector/neurobet` | `https://necrolich.ru/diflector/dev/neurobet` |
| Env | `.env` | `.env.dev` |
| Data | `./data` | `./data-dev` |
| Обучение | нет | да |
| Модели | upload + activate | upload + export `.nbmodel.zip` |

---

## Как запускать

Рабочая директория на сервере: `/srv/neurobet`

### Первичная настройка

```bash
cd /srv/neurobet
cp .env.example .env          # prod
cp .env.dev.example .env.dev  # dev — отдельный POSTGRES_DB

# Nginx: добавить location из infrastructure/nginx-dev-location.snippet
# в /srv/nginx-master/nginx/snippets/diflector-locations.conf
# client_max_body_size 128m на prod location /diflector/neurobet
docker exec nginx_master nginx -t && docker exec nginx_master nginx -s reload
```

### Prod

| Действие | CPU | CUDA |
|----------|-----|------|
| Запуск / rebuild | `docker compose up --build -d` | `+ -f docker-compose-cuda.yml` |
| Логи | `docker compose logs -f` | |
| Остановка | `docker compose down` | |

Админка: `/admin` · MCP: `/api/mcp`

### Dev (параллельно с prod)

Префикс: `docker compose -f docker-compose.yml -f docker-compose.dev.yml -p neurobet-dev --env-file .env.dev`

| Действие | Команда |
|----------|---------|
| Запуск | `{prefix} up --build -d` |
| CUDA | добавить `-f docker-compose-cuda.yml` |
| Логи | `{prefix} logs -f` |
| Остановка | `{prefix} down` |

### После изменений в коде

| Изменили | Действие |
|----------|----------|
| backend / ai | `docker compose up -d --build backend ai` |
| frontend / NEXT_PUBLIC_* | `docker compose up -d --build frontend` |
| `.env` (не NEXT_PUBLIC) | `docker compose restart backend ai` |

### Перенос модели dev → prod

1. Dev `/admin` → **Экспорт текущей** → имя → скачать `*.nbmodel.zip`
2. Prod `/admin` → **Загрузить .nbmodel.zip**
3. Prod → **Активировать**
4. Проверка: `docker compose logs -f ai`

**Не копируйте** `./data/models/` между prod и dev — только `.nbmodel.zip`.

### Перенос архива обучения (finished + team_stats)

Общий архив `finished_events` / `finished_bets` живёт в **prod Postgres** (`ARCHIVE_DATABASE_URL` на dev указывает на `neurobet_postgres`). Live-данные dev — в отдельной БД `autobet_dev`.

**Банкрол и live_bets** (симулированные ставки) всегда локальны для каждого стека — dev не делит боевой банкрол с prod.

1. `/admin` → **Архив обучения** → **Экспорт .nbarchive.zip** (на любом сервере)
2. На целевом сервере → **Импорт .nbarchive.zip** (заменяет finished-таблицы и `team_stats.json`)
3. После импорта AI автоматически перечитывает кэш команд

Формат: `manifest.json`, `finished_data.sql`, опционально `team_stats.json`.

**Импорт полностью заменяет** данные в `finished.finished_events`, `finished.finished_bets`, `finished.finished_odds_history` и `team_stats.json` на новом сервере (TRUNCATE + загрузка). Live-матчи (`live.*`) и банкрол (`finished.live_bets`, `bankroll_*`) **не** входят в архив.

### Экспорт архива из Postgres (CLI)

На сервере с neurobet (старый одно-стековый или текущий prod):

```bash
cd /srv/neurobet
./scripts/export_nbarchive_pg.sh
```

Создаёт `neurobet-archive-YYYYMMDD-HHMMSS.nbarchive.zip` в каталоге `neurobet` (или путь из аргумента).

Импорт на целевом сервере: админка → **Архив обучения** → **Импорт** (полностью заменяет `finished_events`, `finished_bets`, `finished_odds_history` и `team_stats.json`).

---

## CPU / CUDA

Оба стека (prod и dev) могут работать на CPU или GPU — overlay `docker-compose-cuda.yml` подключается независимо.

```bash
# Prod CUDA
docker compose -f docker-compose.yml -f docker-compose-cuda.yml up --build -d

# Dev CUDA
docker compose -f docker-compose.yml -f docker-compose.dev.yml -f docker-compose-cuda.yml \
  -p neurobet-dev --env-file .env.dev up --build -d
```

---

## 📡 API (кратко)

| Метод | Эндпоинт | Описание |
| :--- | :--- | :--- |
| `GET` | `/api/matches` | LIVE матчи |
| `GET` | `/api/stats` | Статистика |
| `GET` | `/api/admin/models` | Реестр моделей |
| `POST` | `/api/admin/models/upload` | Загрузка `.nbmodel.zip` |
| `POST` | `/api/admin/models/{slug}/activate` | Активация модели |
| `GET` | `/api/admin/archive/export` | Экспорт `.nbarchive.zip` |
| `POST` | `/api/admin/archive/import` | Импорт `.nbarchive.zip` |

---

## 🛠 Управление контейнерами

```bash
docker compose logs -f
docker compose down

# Dev
docker compose -f docker-compose.yml -f docker-compose.dev.yml -p neurobet-dev --env-file .env.dev logs -f
```
