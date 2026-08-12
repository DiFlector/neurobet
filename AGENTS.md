# AGENTS.md - Autobet System Architecture & Agent Guidelines

Welcome to **Autobet** — a modern, containerized Fonbet LIVE parser and odds tracking platform built with FastAPI, SQLite, Next.js, and DeepSeek Web WASM integration.

---

## 🚀 Technical Stack & Architecture

### Backend (`/backend`)
* **Framework**: FastAPI + Uvicorn
* **Package Manager**: `uv` (`ghcr.io/astral-sh/uv:latest` inside Docker)
* **Database**: SQLite (`/app/data/autobet.db`)
* **Scheduler**: APScheduler (Background worker scraping Fonbet every 60 seconds)
* **AI Module**: WASM-based DeepSeek Web Client (`backend/ai/deepseek`) using `wasmtime` for SHA3 Proof-of-Work challenge solving.

### Frontend (`/frontend/autobet`)
* **Framework**: Next.js (App Router), React 19, TypeScript
* **Styling**: Vanilla CSS / TailwindCSS with standard dark gray/black neutral palette (`bg-neutral-950`, `bg-neutral-900`, `border-neutral-800`).
* **Accent Palette**: **Flat UI Colors US** palette ONLY for interactive status accents:
  * `#fdcb6e` / `#ffeaa7` (Bright Gold / Headers)
  * `#00b894` / `#55efc4` (Mint / Falling Odds - Green)
  * `#d63031` / `#ff7675` (Alizarin Red / Rising Odds - Red)
  * `#0984e3` / `#74b9ff` (Electron Blue / Stats & Icons)
* **Visualization**: Recharts interactive line graphs for odds history.
* **Component Positioning**: React `createPortal` to `document.body` for odds popovers to prevent clipping in scrollable containers.

---

## 🛠 Project Structure

```
autobet/
├── backend/
│   ├── ai/
│   │   └── deepseek/
│   │       ├── client.py                    # WASM PoW solver DeepSeek Web client
│   │       ├── __init__.py
│   │       └── wasm/
│   │           └── sha3_wasm_bg.7b9ca65ddd.wasm
│   ├── database.py                          # SQLite database schema, py_lower function, stats
│   ├── parser_service.py                    # Fonbet LIVE API catalog resolver & scraper
│   ├── main.py                              # FastAPI REST endpoints & background scheduler
│   ├── requirements.txt                     # Dependencies (FastAPI, uvicorn, httpx, wasmtime, etc.)
│   └── Dockerfile                           # Fast uv-based Docker image
├── frontend/
│   └── autobet/
│       ├── app/
│       │   ├── page.tsx                     # Main Dashboard UI (Filters, Stats, Search, Safe Mode)
│       │   ├── layout.tsx
│       │   └── globals.css
│       ├── components/
│       │   ├── MatchCard.tsx                # Event card with scores and main market odds
│       │   ├── OddsButton.tsx               # Odds button with trend color & portal graph popover
│       │   ├── OddsHistoryGraph.tsx         # Recharts line chart popover
│       │   └── SubMarketsDrawer.tsx         # Modal drawer listing all sub-events & markets
│       ├── package.json
│       └── Dockerfile                       # Multi-stage Node 22-alpine + pnpm@10 build
├── data/                                    # Persistent Docker volume for SQLite (autobet.db)
├── docker-compose.yml                       # Docker Compose orchestration (ports 8000 & 3000)
├── AGENTS.md                                # Agent guidelines (this file)
└── README.md                                # Project README
```

---

## 💡 Key Design & Implementation Rules

1. **Docker Compose Execution**: Always manage containers via Docker Compose (`docker compose up --build -d`).
2. **SQLite Cyrillic Support**: Standard SQLite `LOWER()` only handles ASCII. Python custom function `py_lower` is registered on every connection to allow proper Cyrillic case-insensitive SQL searches (`py_lower(sport_path) LIKE '%футбол%'`).
3. **Dynamic Initial Odds Subquery**: `initial_coefficient` is dynamically subqueried from `odds_history` (the first recorded entry for that exact factor/market) to guarantee 100% accurate trend calculation.
4. **Odds Color Dynamics**:
   * **Red** (`#d63031` / `#ff7675`): Coefficient **rose** compared to historical initial value.
   * **Green** (`#00b894` / `#55efc4`): Coefficient **dropped** compared to historical initial value.
   * **Gray**: Coefficient **unchanged** (`+-`).
5. **Safe Mode**: Toggle to hide dangerous odds (`< 1.1` or `> 2.1`). Enabled by default on frontend load.
6. **Popover Portals**: Popover tooltips must render via `createPortal(..., document.body)` with `position: fixed` and dynamic viewport collision calculation to prevent `overflow: hidden` clipping.

---

## 📡 Key REST API Endpoints (Backend `:8000`)

* `GET /api/matches?sport={sport}&search={search}`: Returns live events with sub-markets and latest odds.
* `GET /api/matches/{event_id}/odds-history?factor_id={fid}&parameter={p}&market_prefix={prefix}`: Returns chronological odds history for graph plotting.
* `GET /api/stats`: Returns live counts, total odds history records, database disk file size, and last update timestamp.
* `POST /api/trigger-scrape`: Triggers an instant manual scrape.

---

## 💻 Commands

```bash
# Start container stack
docker compose up --build -d

# View container logs
docker compose logs -f

# Stop container stack
docker compose down
```
