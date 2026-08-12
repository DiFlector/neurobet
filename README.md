# ⚡ Autobet - Fonbet LIVE Parser & Odds History Platform

![Version](https://img.shields.io/badge/version-2.0-blue.svg)
![Python](https://img.shields.io/badge/Python-3.11-green.svg)
![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)
![Next.js](https://img.shields.io/badge/Next.js-15-black.svg)
![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)

**Autobet** — современная система автоматического парсинга LIVE матчей и коэффициентов с Fonbet, сохранением снимков в SQLite и интерактивным отображением истории коэффициентов в графиках.

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
* **🤖 DeepSeek WASM Модуль**: Интегрированный модуль в `backend/ai/deepseek` для прямого взаимодействия с `chat.deepseek.com` через WebAssembly SHA3 Proof-of-Work ресолвер.
* **🐳 Полная контейнеризация**: Запуск бэкенда и фронтенда одной командой через `docker compose`.

---

## 🏗️ Архитектура проекта

```
autobet/
├── backend/                  # FastAPI сервис (Python 3.11 + uv)
│   ├── ai/
│   │   └── deepseek/         # WASM SHA3 PoW модуль DeepSeek Web Client
│   ├── database.py           # Таблицы events, odds_history, latest_odds, py_lower
│   ├── parser_service.py     # Парсер Fonbet LIVE (ротация CDN и каталогов)
│   ├── main.py               # REST API и планировщик парсинга каждые 60с
│   └── Dockerfile
├── frontend/
│   └── autobet/              # Next.js 15 UI (React 19 + TypeScript)
│       ├── app/              # Дашборд, фильтры, шапка со статистикой
│       ├── components/       # MatchCard, OddsButton, OddsHistoryGraph, SubMarketsDrawer
│       └── Dockerfile
├── data/                     # Монтируемый том Docker для базы данных SQLite
└── docker-compose.yml        # Оркестрация контейнеров (порты 8000 и 3000)
```

---

## 🚀 Быстрый запуск (Docker Compose)

Убедитесь, что у вас установлены [Docker](https://www.docker.com/) и `docker compose`.

### 1. Клонирование репозитория и запуск

```bash
git clone https://github.com/DiFlector/autobet.git
cd autobet

# Запуск приложения в Docker
docker compose up --build -d
```

### 2. Доступ к интерфейсу

* **Frontend UI**: [http://localhost:3000](http://localhost:3000)
* **Backend API Docs**: [http://localhost:8000/docs](http://localhost:8000/docs)

---

## 📡 API Эндпоинты

| Метод | Эндпоинт | Описание |
| :--- | :--- | :--- |
| `GET` | `/api/matches` | Получение LIVE матчей с фильтрацией по виду спорта и поиску |
| `GET` | `/api/matches/{event_id}/odds-history` | История коэффициентов для конкретного исхода |
| `GET` | `/api/stats` | Статистика (активные матчи, всего записей истории, размер БД) |
| `POST` | `/api/trigger-scrape` | Ручной запуск парсинга вне расписания |

---

## 🛠 Управление контейнерами

```bash
# Просмотр логов
docker compose logs -f

# Остановка контейнеров
docker compose down
```
