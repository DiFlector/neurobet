# AGENTS.md - NeuroBet System Architecture & Agent Guidelines

Welcome to **NeuroBet** — a modern, containerized Fonbet LIVE parser and odds tracking platform built with FastAPI, Postgres, Next.js, and a PyTorch GRU + LightGBM ensemble.

**Public URL on this host:** `https://diflector.ru/neurobet` (and `/admin`, `/stats`, …). The frontend joins Docker network `nginx-master-network`; host nginx in `/home/diflector/nginx` proxies `/neurobet` to `neurobet_frontend:3000`. Do not publish `:80`/`:443` from this compose. See `/home/diflector/nginx/AGENTS.md`.

---

## 🚀 Technical Stack & Architecture

### Backend (`/backend`)
* **Framework**: FastAPI + Uvicorn
* **Package Manager**: `uv` (`ghcr.io/astral-sh/uv:latest` inside Docker)
* **Database**: Postgres (live + finished schemas)
* **Scheduler**: APScheduler (Background worker scraping Fonbet)
* **AI Module**: `ai_service` — PyTorch GRU + LightGBM blend, calibration, Kelly bankroll (no LLM)

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
│   ├── database.py                          # Postgres access, stats, live bets
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
7. **Neural verdict = EV, not a residual decision head**: the PyTorch GRU still has a 4-logit head for checkpoint compatibility, but live bankroll bets and the "Активные LIVE Прогнозы" tab only consider outcomes where calibrated `win_probability` implies `expected_roi ≥ MIN_BET_EDGE_PCT` (`predicted_win = 1`). Residual `decision_logit` training is off by default (`NEURALBET_DECISION_LOSS_WEIGHT=0`). Stake path: EV → live gates → quality gate → Kelly (no LLM). Overall `accuracy_pct` ≈ 50% is expected on 2-way lines and is not the edge KPI — use walk-forward ROI CI and Brier vs market. History outcomes are judged **guessed / not guessed** (`predicted_win` vs. `is_win`).

8. **Parity (always)**: features, gates, sibling coherence, and player/team KB must be identical on live UI, bot Kelly, training, and backtest. Source of truth: `shared/neurobet_features/` + `shared/neurobet_filters/`. See `.cursor/rules/neurobet-parity.mdc`.

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

* URL: `https://diflector.ru/neurobet/api/mcp` (local: `POST /api/mcp` on the backend)
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
| `get_training_health` | Overfitting traffic light (`ok` / `warning` / `danger` / `unknown`) + signals + live `quality_gate` |
| `get_training_runs` | Per-pass metrics for TrainingTrendChart (`val_loss`, `val_guess_rate`, `best_epoch`, …) |
| `get_backtest_history` | Condensed run trend for QualityTrendChart (not the full per-run JSON) |
| `get_backtest_review` | **Start here for model review**: edge verdict, quality_gate, walk-forward stability, funnel, head-alignment flags, delta vs previous run |
| `get_latest_backtest` | Full latest backtest JSON on disk (`overall`, `by_sport`, `by_market`, `walk_forward`, `agent_review`). No new run |
| `run_backtest` | Admin «Бэктест» button: run now (default 40000), return that result only. 15–60s |
| `get_ensemble` | Live weights: `blend_weight`, `market_weight`, `decision_threshold`, per-sport thresholds |
| `get_filters` | Live betting gates: allowed sports/factors, live stake sports/markets, coeff band, min EV, min market support |
| `get_bankroll` | Live + training accounts |
| `get_live_bets` | Simulated live bets. Optional `status` (`open` / `won` / `lost` / `void` / `cancelled`) |

### Дашборд нейроставок

| Tool | What it returns |
| :--- | :--- |
| `get_top_neurobets` | Active LIVE predictions (`verdict=win` by default — only what the bot would stake) |
| `get_neurobets_history` | Judged history (guessed / not guessed / push / pending) + summary |

---

## 🤖 Ревью модели: как пользоваться (обязательно для агента)

Когда пользователь просит **оценить / улучшить / ревью / backtestprompt** (или похожее —
«посмотри нейросеть», «есть ли edge», «что улучшить в модели», «прогони бэктest»), агент
**не отвечает из памяти** — сначала собирает данные по протоколу ниже.

**Источник правды по формату ответа и чеклисту:** [`BacktestPrompt.md`](BacktestPrompt.md) —
прочитать файл целиком в начале такой задачи.

**Cursor rule (дублирует триггеры):** `.cursor/rules/neurobet-model-review.mdc`

### Триггеры (любой из списка → включить протокол)

- Явно: `BacktestPrompt.md`, «backtest prompt», «по backtestprompt»
- Ревью: «оцени», «посмотри», «улучши модель», «есть ли edge», «как модель», «ревью нейросети»
- Бэктest: «проверь бэктest», «свежий бэктest», «run eval pack»
- Здоровье: «training health», «почему gate блокирует», «ROI упал»

### Порядок MCP (NeuroBet eval server)

1. **`get_backtest_review`** — **всегда первым** (сжатый `agent_review`: edge, gate, flags, funnel).
   Если `review` пустой или нет `agent_review` в JSON — бэктest старый или не запускался;
   перейти к шагу 2 или попросить пользователя прогнать бэктest.
2. **`get_training_health`** + **`get_ai_logs`** (`TRAINING`, `BANKROLL`, limit 30–50) —
   пункты 1–5 из BacktestPrompt (best_epoch, тюнер, val Brier, skipping training, quality gate).
3. **`get_ensemble`** + **`get_filters`** — текущие веса и live gates.
4. При необходимости деталей:
   - **`get_latest_backtest`** — полный JSON (`by_sport`, `by_market`, `walk_forward_folds`, …)
   - **`get_backtest_history`** — тренд последних прогонов
   - **`get_eval_pack`** — всё в одном JSON (без нового бэктestа)
5. **Свежие веса обязательны** → **`run_eval_pack`** или **`run_backtest`** (15–60 с, CPU).
   Не вызывать без запроса пользователя, если `get_backtest_review` моложе ~6 ч и веса не менялись.

### На что смотреть в `agent_review` (приоритет)

| Поле | Вопрос |
| :--- | :--- |
| `summary.edge_verdict` | `likely` / `promising` / `unproven` / `calibration_only` / `none` |
| `summary.quality_gate_pass` | Можно ли снимать блок live-ставок |
| `slices.walk_forward` | **OOS-главное** (model-only): ROI, `roi_pct_lo`, Brier vs market |
| `walk_forward_stability` | Сколько фолдов с ROI ≤ 0 |
| `funnel` | verdict → candidate → final bets |
| `head_alignment` | decision head vs EV (пункт 9 улучшений) |
| `flags` | Готовые сигналы — не дублировать вручную |
| `delta_vs_previous` | Улучшение vs прошлый прогон |

**Не путать:** `overall` ROI может быть оптимистичнее `walk_forward` — для вердикта edge
ориентир на **walk_forward** (так же настроен `quality_gate` в pipeline).

### Формат ответа пользователю

Как в [`BacktestPrompt.md`](BacktestPrompt.md):

1. Вердикт одним абзацем (улучшается / стоит / деградирует; edge есть / нет).
2. Конкретика только по находкам (логи + бэктest slices + flags).
3. Приорitized правки с файлами/env; мелкие — можно сразу; спорные — предложить.
4. Честно: нет edge — не предлагать бесконечный тюнинг и cold-start без причины.

Отвечать **на русском**, если пользователь пишет по-русски.

### Ключевые файлы для правок

| Область | Файлы |
| :--- | :--- |
| Live gates, sports whitelist | `shared/neurobet_filters/__init__.py`, `.env` |
| Бэктest, agent_review, quality_gate | `ai_service/app/neuralbet/backtest.py`, `review.py` |
| Обучение, checkpoint, тюнер | `ai_service/app/neuralbet/model.py`, `pipeline.py` |
| Калибровка | `ai_service/app/neuralbet/calibration.py` |
| Фичи / KB игроков·команд | `shared/neurobet_features/` |
| MCP tools | `backend/mcp_eval.py` |
| Промпт ревью | `BacktestPrompt.md` |

### Чего не предлагать без данных

- Cold-start / reset модели — только если архитектура/loss изменились или пользователь явно просит.
- Снять `quality_gate` — только если `walk_forward` стабильно pass + `roi_pct_lo` > 0 в нескольких прогонах.
- Пункты из «Контекст прошлых решений» в BacktestPrompt — уже сделаны.

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
