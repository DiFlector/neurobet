# AGENTS.md - NeuroBet System Architecture & Agent Guidelines

Welcome to **NeuroBet** — a modern, containerized Fonbet LIVE parser and odds tracking platform built with FastAPI, SQLite, Next.js, and DeepSeek Web WASM integration.

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
│   ├── mcp_eval.py                          # Streamable HTTP MCP at POST /api/mcp
│   ├── requirements.txt                     # Dependencies (FastAPI, uvicorn, httpx, wasmtime, etc.)
│   └── Dockerfile                           # Fast uv-based Docker image
├── mcp/
│   └── neurobet_eval.py                     # Stdio MCP fallback (proxies to /api/mcp)
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
7. **Neural verdict, not a probability cutoff**: the PyTorch GRU (`ai_service/app/neuralbet/model.py`) has a dedicated `decision_logit` output — its own learned bet/no-bet verdict (`finished_bets.predicted_win` / `ai_predictions.predicted_win`), trained with a cost-sensitive loss rather than copying the win-probability head's 0.5 threshold. Live bankroll bets and the "Активные LIVE Прогнозы" tab only ever consider outcomes with `predicted_win = 1`; the win-probability percentage shown everywhere is calibrated once in `ai_service/app/neuralbet/calibration.py` before being saved — backend reads it as-is, it never recalibrates a second time. History outcomes are judged **guessed / not guessed** (`predicted_win` vs. `is_win`), not win/loss — a verdict of "will lose" that turns out correct still counts as guessed.

---

## 📡 Key REST API Endpoints (Backend `:8000`)

* `GET /api/matches?sport={sport}&search={search}`: Returns live events with sub-markets and latest odds.
* `GET /api/matches/{event_id}/odds-history?factor_id={fid}&parameter={p}&market_prefix={prefix}`: Returns chronological odds history for graph plotting.
* `GET /api/stats`: Returns live counts, total odds history records, database disk file size, and last update timestamp.
* `POST /api/trigger-scrape`: Triggers an instant manual scrape.
* `POST /api/mcp`: Streamable HTTP MCP (tools for stats, admin reads, eval pack). See below.

---

## 🔌 MCP (Model Context Protocol)

Cursor connects as the **client**; NeuroBet is the **server**. Production endpoint is Streamable HTTP — no extra port, it rides the existing `/api` rewrite:

* URL: `https://necrolich.ru/neurobet/api/mcp` (local: `POST /api/mcp` on the backend)
* Cursor config: `.cursor/mcp.json` (`neurobet-eval`, `"type": "http"`)
* Implementation: `backend/mcp_eval.py` (source of truth for the tool list)
* Stdio fallback for offline/dev: `mcp/neurobet_eval.py` — forwards JSON-RPC to `{NEUROBET_API_URL}/api/mcp`

All tools are **read-only** except running a backtest (CPU, 15–60s, does not mutate the model). Destructive admin actions are **not** exposed: reset DB / model / bankroll, cancel live bets, toggle inference/training.

Prefer a **granular** tool when you only need one slice. Use a composite when reviewing the whole picture.

### Composite

| Tool | What it returns | When to use |
| :--- | :--- | :--- |
| `get_eval_pack` | Full agent pack: filters, ensemble, latest full backtest JSON, ROI/stats, training health, training runs, bankroll, logs. No new backtest. | Model review / attaching one JSON |
| `run_eval_pack` | Fresh backtest (default 40000 samples) then the same pack | Judging **current** weights |
| `get_overview` | Lighter all-in-one: db/ROI/bet-type stats, bankroll, settings, health, backtest history, logs. No ensemble, no full backtest JSON | Quick health check |
| `get_admin` | Everything the admin panel polls: settings, health, training-run trend, backtest history, DB stats, bankroll, live bets, logs | Admin-page snapshot |
| `get_stats` | Everything on «Статистика»: `db_stats`, `bet_type_stats`, `roi_stats` | Stats-page snapshot |

### Страница «Статистика»

| Tool | What it returns |
| :--- | :--- |
| `get_db_stats` | Live events, odds-history count, DB size, finished-bet counts, headline guess-rate |
| `get_bet_type_stats` | Guess-rate by sport and market («Разбивка угадывания»). Optional `sport`, `bet_types_limit` |
| `get_roi_stats` | ROI and Brier vs bookmaker baseline, by coefficient band |

### Админка (read-only)

| Tool | What it returns |
| :--- | :--- |
| `get_ai_settings` | `ai_enabled` / `training_enabled` toggles |
| `get_ai_logs` | TRAINING / INFERENCE / BANKROLL / SYSTEM feed. Optional `category`, `limit` |
| `get_training_health` | Overfitting traffic light (`ok` / `warning` / `danger` / `unknown`) + signals |
| `get_training_runs` | Per-pass metrics for TrainingTrendChart (`val_loss`, `val_guess_rate`, `best_epoch`, …) |
| `get_backtest_history` | Condensed run trend for QualityTrendChart (not the full per-run JSON) |
| `get_latest_backtest` | Full latest backtest JSON on disk (`overall`, `by_sport`, `by_coefficient`). No new run |
| `run_backtest` | Admin «Бэктест» button: run now (default 40000), return that result only. 15–60s |
| `get_ensemble` | Live weights: `blend_weight`, `market_weight`, `decision_threshold`, per-sport thresholds |
| `get_filters` | Live betting gates: allowed sports/factors, coeff band, min EV, min market support |
| `get_bankroll` | Live + training accounts |
| `get_live_bets` | Simulated live bets. Optional `status` (`open` / `won` / `lost` / `void` / `cancelled`) |

### Дашборд нейроставок

| Tool | What it returns |
| :--- | :--- |
| `get_top_neurobets` | Active LIVE predictions (`verdict=win` by default — only what the bot would stake) |
| `get_neurobets_history` | Judged history (guessed / not guessed / push / pending) + summary |

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
