# BacktestPrompt — ревью NeuroBet (для AI-агента)

NeuroBet: GRU + LightGBM + market blend, калибровка по спорту/кэфу, decision-порог,
quarter-Kelly, live band 1.5–2.0. Код: `ai_service/app/neuralbet/`.

> **Агент:** триггеры и порядок MCP — [`AGENTS.md`](AGENTS.md) (§ «🤖 Ревью модели»),
> `.cursor/rules/neurobet-model-review.mdc`. **Первый вызов:** MCP `get_backtest_review`.
> Данные пользователь не прикладывает — собирай через MCP сам.

---

## 1. Порядок сбора данных (MCP)

| Шаг | Tool | Зачем |
| :--- | :--- | :--- |
| 1 | **`get_backtest_review`** | `agent_review`, `quality_gate`, flags, funnel |
| 2 | **`get_training_health`** | overfitting traffic light |
| 3 | **`get_ai_logs`** | `TRAINING`, `BANKROLL`, `INFERENCE` (limit 30–50) |
| 4 | **`get_ensemble`** + **`get_filters`** | blend/market/threshold, live gates |
| 5 | **`get_latest_backtest`** | детали: `by_sport`, `by_market`, `walk_forward_folds` |
| 6 | **`get_backtest_history`** | тренд ROI/Brier/accuracy |
| 7 | **`run_eval_pack`** / **`run_backtest`** | только если веса менялись, бэктest пустой/устарел (>6 ч), или пользователь просит свежий прогон |

Если `get_backtest_review` вернул `no_data` или нет `agent_review` — нужен rebuild + новый бэктest.

---

## 2. Чеклист: бэктest / `agent_review`

**Главный срез для edge — `walk_forward`**, не `overall`. Quality gate смотрит туда же.

| # | Что проверить | Где |
| :- | :--- | :--- |
| B1 | `edge_verdict`, `quality_gate_pass`, `one_liner` | `agent_review.summary` |
| B2 | ROI, **`roi_pct_lo`**, Brier vs market | `agent_review.slices.walk_forward` → fallback `oos_never_train` |
| B3 | Сколько фолдов с ROI ≤ 0 | `agent_review.walk_forward_stability` |
| B4 | Воронка: verdict → candidate → final bets | `agent_review.funnel` |
| B5 | Decision head vs EV (`head_alignment`) | `agent_review.head_alignment` |
| B6 | Готовые сигналы — **не дублировать** | `agent_review.flags` |
| B7 | Δ vs прошлый прогон | `agent_review.delta_vs_previous` |
| B8 | Brier current vs `market_brier` | `overall` / slices |
| B9 | ROI по кэфу 1.5–2.0, кап `MAX_BET_COEFF` | `by_coefficient` |
| B10 | `total_under` vs `total_over` | `by_market` |
| B11 | По спортам (live-лист) | `by_sport` или `agent_review.by_sport` |
| B12 | Тренд нескольких прогонов | `get_backtest_history` |

### `edge_verdict` (расшифровка)

| Значение | Смысл |
| :--- | :--- |
| `likely` | quality gate pass + устойчивый сигнал |
| `promising` | Brier < market, ROI > 0, но CI lo ≤ 0 или gate fail |
| `unproven` | смешанные сигналы |
| `calibration_only` | калибровка лучше рынка, ставочный edge не доказан |
| `none` | нет оснований для edge |

### Quality gate (live-ставки)

Проверяется на **`walk_forward`** (fallback: `oos_never_train` → `overall`). Pass, если **все**:

- ставок ≥ `NEURALBET_LIVE_QUALITY_MIN_BETS` (40)
- flat ROI > 0
- **`roi_pct_lo` > 0**
- win_rate > break-even
- Brier < market (market_brier из overall)

В логах: `Live bets skipped — quality gate: …` (`get_ai_logs` BANKROLL).

### Метрики — не путать

| Метрика | Смысл |
| :--- | :--- |
| `accuracy_pct` | угадано/не угадано по `current_pred` (включая «не ставить») |
| `verdict_accuracy_pct` | то же по decision head без live gates |
| `verdict.precision_pct` | доля win среди verdict=1 |
| `stake_policy.win_rate_pct` | только по реальным ставкам бэктestа |
| `bankroll_roi_pct` | Kelly-replay (compound), не flat ROI |
| **`roi_pct_lo` / `hi`** | bootstrap CI flat ROI — **главный критерий устойчивости** |

### Срезы бэктestа

| Срез | Назначение |
| :--- | :--- |
| `overall` | полная выборка, может быть оптимистичнее |
| `walk_forward` | **честный OOS**, temporal folds + no-leakage calib |
| `oos_never_train` | holdout events с `trained_count=0` |
| `in_sample` | обучаемая часть архива |

---

## 3. Чеклист: логи обучения / inference

| # | Что искать | Сигнал проблемы |
| :- | :--- | :--- |
| L1 | `best_epoch` ≤ 2 на проходах ≥ `MIN_TRAIN_SAMPLES` | заучивание батча |
| L2 | `blend_weight` / `market_weight` / `decision_threshold` в «Ensemble tuned» | скачки; `market_weight` → 1.0 = модель не даёт сигнала |
| L3 | `val Brier … vs market-only` | доля циклов «market beats model» |
| L4 | Число verdict «ставить» между циклами | скачки в разы |
| L5 | `Skipping training` / `MIN_FRESH_SAMPLES` / replay-only | обучение простаивает или крутит replay |
| L6 | `checkpoint rejected` / «Модель заморожена» | GRU frozen после 10 reject |
| L7 | `Cold-start` / streaming epoch | val/checkpoint только в конце epoch |
| L8 | `Live bets skipped — quality gate` | см. §2 |

---

## 4. Формат ответа пользователю

1. **Вердикт одним абзацем:** улучшается / стоит / деградирует; edge есть / нет; gate pass/fail.
2. **Конкретика** — только по находкам (B1–B12, L1–L8, flags).
3. **Приорitized правки** с файлами/env; мелкие — можно сразу; спорные — предложить.
4. **Честно:** нет edge — не предлагать бесконечный тюнинг и cold-start без причины.

Язык: **русский**, если пользователь пишет по-русски.

---

## 5. Уже сделано (не предлагать заново)

- Пол вероятности 1–99% (не 12% floor).
- `NEURALBET_DECISION_POS_WEIGHT_CAP=1.0` (cost-sensitive decision в live band).
- `market_weight` в blend, тюнинг по Brier, `NEURALBET_MARKET_WEIGHT_FLOOR=0.5`.
- Калибровка с учётом кэфа; **no-leakage calib** в бэктest (`calibration_cutoff`).
- Live gates: `MIN_BET_COEFF=1.5`, `MAX_BET_COEFF=2.0`, `MIN_BET_EDGE_PCT=3%`, `MIN_MARKET_SUPPORT=150`.
- Live stake markets: `NEURALBET_LIVE_STAKE_MARKETS=totals` (w1/w2 excluded from staking; still in training universe).
- Online GRU LR: `NEURALBET_LEARNING_RATE=5e-5` (was 1e-4; reduces best_epoch=1 / checkpoint reject).
- Тюнер: EMA 0.3, min val bets, sample-size penalty; threshold sweep по **ROI CI lo**.
- Обучение: `TRAIN_EVERY_CYCLES=20`, `MIN_TRAIN_SAMPLES=2000`, batch 10k, val 2k, `MIN_FRESH_SAMPLES=500`.
- Cold-start streaming (chunk pass, val/checkpoint после полного epoch).
- `NEURALBET_LIVE_QUALITY_GATE` + walk-forward OOS в бэктest.
- Team form: as-of + правильная атрибуция P1/P2; overround/no-vig fix.
- Per-sport `decision_threshold`; `NEURALBET_LIVE_STAKE_SPORTS` (default: НТ).
- `trained_count` при rollback; GRU cooldown после `CHECKPOINT_REJECT_STREAK_ALERT=10` с probe раз в `CHECKPOINT_REJECT_PROBE_EVERY_CYCLES` (default 20).
- `agent_review` + `quality_gate` в JSON бэктestа; `get_backtest_review` MCP.
- Consecutive gate: история считается по **core-метрикам** (не по итоговому
  `quality_gate.pass`), иначе серия 1<2 никогда не закрывается; live re-eval
  пропускает тот же `generated_at` в history.
- DeepSeek shadow (2026-08-20): web-search **после** place (async), не блокирует Kelly;
  `NEURALBET_LLM_VETO=0`; `NEURALBET_LLM_MATCH_CONTEXT_SPORTS=теннис,футбол`;
  shadow JSON + MCP `get_llm_shadow`; auto-veto только если shadow докажет edge
  (≥150 settled, with_veto ROI/WR лучше, CI lo>0, vetoed ROI<0).
- DeepSeek match-context cache (2026-08-21): ключ event/factor/parameter/prefix;
  re-fetch при Δcoeff ≥ `NEURALBET_LLM_REANALYZE_COEFF_DELTA` (0.05) или
  Δprob ≥ `NEURALBET_LLM_REANALYZE_PROB_DELTA` (3 п.п.).
- Digest prompt: запрет советовать ослаблять gate без устойчивого walk-forward;
  возраст снимка + раздельно model-only vs with_veto.
- Training diagnostics: `val_pin` age в логах/history; `last_tune.json`;
  `train_*_attempted` при reject; flag `fixed_val_tuner_vs_walk_forward`.
- OOS ablation в бэктest/`agent_review`: `oos_ablation.table_tennis_x_total_over`
  + `total_over` (не путать с overall by_sport).
- **Objective B (2026-08-21):** live/backtest verdict = EV
  (`calibrated_p * c - 1 ≥ MIN_BET_EDGE`); residual decision-head loss default **0**;
  bankroll train mask тоже EV. `accuracy_pct` ~50% — не KPI.
- **DeepSeek batch decide:** `NEURALBET_LLM_BATCH_DECIDE=1`,
  `NEURALBET_LLM_BATCH_REQUIRED=1`, top `NEURALBET_LLM_BATCH_MAX=16` по EV,
  JSON `{"0":1,"1":0,…}` с web-search **до** Kelly; fail-closed.
- **DeepSeek в бэктestе:** `NEURALBET_LLM_BACKTEST=1`, до
  `NEURALBET_LLM_BACKTEST_MAX_CALLS` web-search батчей по top stake-кандидатам;
  результат в `llm_web_search_ablation` / flag (не меняет quality_gate).
  Архивный поиск может утекать итогом матча — diagnostic, не чистый OOS.
- Live defaults: sports `НТ,теннис,баскетбол,футбол`; markets `totals,w1,w2`
  (волейбол / draw stake — нет). Смена objective → cold-start после деплоя.

**Cold-start / reset** — только при смене архитектуры/loss или явной просьбе пользователя.

---

## 6. Терминология: «live»

**Live** — simulated-банк (`bankroll.accounts.live`): реальные **virtual** ставки каждый цикл inference (~60 с).

Цепочка:

1. **INFERENCE** — прогнозы на universe (GRU + LGBM + blend + calib).
2. **Decision `predicted_win=1`** — decision head (≠ win-probability % в UI).
3. Live gates — coeff 1.5–2.0, EV ≥ 3%, support ≥ 150, `NEURALBET_LIVE_STAKE_SPORTS`,
   `NEURALBET_LIVE_STAKE_MARKETS` (default: totals).
4. **Quality gate** — последний бэктest `walk_forward` (см. §2); иначе ставки не открываются.
5. **BANKROLL** — Kelly `allocate()`, запись в `live_bets`.

**Не путать с:**

- training bankroll — только для loss в `train_online`;
- бэктest — replay архива с текущими весами;
- `get_roi_stats` / «Статистика» — archived judged outcomes, **≠** live ROI.

MCP live: `get_live_bets`, `get_bankroll`, `get_top_neurobets`.  
MCP archive stats: `get_stats`, `get_roi_stats`.

---

## 7. Live по спортам (`NEURALBET_LIVE_STAKE_SPORTS`)

**Live sports:** default `NEURALBET_LIVE_STAKE_SPORTS=настольный теннис,теннис,баскетбол,футбол`
при **рынках = totals + w1 + w2** (волейбол и draw stake исключены). Inference/обучение и так на всём
`ALLOWED_SPORTS`; список влияет только на stake / «win» UI / бэктест «would bet».

| Спорт | Ориентир |
| :--- | :--- |
| **Настольный теннис** | единственный карман с CI lo > 0 на totals (ROI ~24%, lo ~+13) |
| Теннис, баскетбол | ROI > 0, но CI lo ≤ 0 — оставлены в stake, наблюдать |
| **Волейбол** | исключён: ROI −12.7%, CI lo −40 на totals (прогон 20.08) |
| Футбол | 0 stake-ставок на totals — в списке без вреда |

Env: `NEURALBET_LIVE_STAKE_SPORTS=*` | `настольный теннис` | список.
Рынки: `NEURALBET_LIVE_STAKE_MARKETS=totals` (default) | `*` | `total_over,total_under,w1,…`.

Критерий отката: если walk_forward ROI/CI lo **хуже** без волейбола — сузить до
`настольный теннис`. Не возвращать волейбол / w1/w2 в live без устойчивого OOS.
Gate не снимать, пока WF CI lo ≤ 0.

### Сделано (2026-08-20) — totals-only → sports=`*` → без волейбола

- `NEURALBET_LIVE_STAKE_MARKETS=totals` — w1/w2/draw не ставятся.
- `NEURALBET_LEARNING_RATE=5e-5`.
- `NEURALBET_LIVE_STAKE_SPORTS=*` — диагностическое расширение; gate тогда ещё fail.
- После gate pass + `by_sport`: волейбол в минусе →
  `NEURALBET_LIVE_STAKE_SPORTS=настольный теннис,теннис,баскетбол,футбол`.

---

## 8. Файлы для правок (шпаргалка)

| Область | Путь |
| :--- | :--- |
| Live gates, sports, markets | `shared/neurobet_filters/__init__.py`, `.env` |
| Бэктest, review, gate | `ai_service/app/neuralbet/backtest.py`, `quality_gate.py`, `review.py` |
| Обучение, тюнер | `ai_service/app/neuralbet/model.py`, `pipeline.py` |
| Калибровка | `ai_service/app/neuralbet/calibration.py` |
| DeepSeek LLM / shadow | `ai_service/app/deepseek/insights.py`, `.env` (`NEURALBET_LLM_*`) |
| Фичи | `shared/neurobet_features/` |
| MCP | `backend/mcp_eval.py` |
