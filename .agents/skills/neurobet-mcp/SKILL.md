---
name: neurobet-mcp
description: "Procedure and cheat sheet for interacting with the Neurobet MCP server, tools, model control, metrics, and paper bankroll."
---

# Neurobet MCP Server Skill

This skill explains how to use and verify the Neurobet Model Context Protocol (MCP) server for monitoring the cluster, evaluating ML models, and auditing betting operations.

## MCP Server Locations & Transport

- Protocol: JSON-RPC 2.0 over standard I/O (stdio), conforming to MCP 2024-11-05
- Entrypoint: `/srv/neurobet/packages/mcp-server/src/neurobet_mcp/server.py`
- Runner script: `/srv/neurobet/scripts/run_mcp_server.sh`
- Configuration files:
  - Antigravity IDE: `.agents/mcp_config.json` & `.agents/plugins/neurobet/mcp_config.json`
  - Cursor IDE: `.cursor/mcp.json`
  - Root: `mcp.json`

## Available Tools (29)

1. **Cluster Health & Observability**:
   - `neurobet_get_system_health`: Real-time health of 13 microservices, database, and Redis.
   - `neurobet_query_prometheus`: PromQL queries (e.g. `sum(up)`, `rate(collector_polls_total[1m])`).
   - `neurobet_get_prometheus_targets`: Scrape targets status.
2. **ML Models & Shadow Mode**:
   - `neurobet_list_models`: Registry of models (ACTIVE, SHADOW, RETIRED) and metrics (ROC-AUC, Brier).
   - `neurobet_get_active_model`: Production model details by sport code.
   - `neurobet_get_shadow_report`: Population Stability Index (PSI), Wasserstein distance, counterfactual PnL.
   - `neurobet_register_shadow_model`: Enrolls a candidate model into Shadow Mode.
   - `neurobet_promote_model`: Promotes shadow model to active if promotion gates pass.
   - `neurobet_rollback_model`: Instant rollback of active model to previous stable version.
   - `neurobet_simulate_inference`: Runs sandbox inference on custom feature vector and odds.
3. **Training & Experiments**:
   - `neurobet_trigger_training`: Triggers model training run.
   - `neurobet_list_training_runs`: Lists past and current training jobs.
   - `neurobet_list_experiments`: Lists scientific backtests and baseline experiments.
   - `neurobet_compare_experiments`: Statistical comparison of experiments.
   - `neurobet_trigger_backtest`: Replay one ACTIVE/SHADOW model or all ACTIVE/SHADOW models of a sport. See `docs/historical-backtest.md`.
   - `neurobet_get_backtest_run`: Status and full paper report for a replay.
   - `neurobet_list_backtest_runs`: Recent replays without individual bet rows.
4. **Events, Lines & Context**:
   - `neurobet_list_live_events`: Live events tracked by Fonbet collector.
   - `neurobet_get_event_timeline`: Score snapshots and states.
   - `neurobet_get_event_odds`: Real-time and historical odds snapshots.
   - `neurobet_get_predictions`: Recent ML predictions and expected value/edge.
   - `neurobet_list_model_bets`: Statistical sides of active and shadow models, including decisions with no stake. Not the bankroll.
   - `neurobet_get_research_packet`: Web research evidence and team news.
5. **Virtual Bankroll & Risk Management**:
   - `neurobet_get_bankroll_summary`: Virtual balance, available cash, exposure, total equity.
   - `neurobet_list_virtual_bets`: History of paper bets and settlements.
   - `neurobet_list_bet_proposals`: Bet proposals and 20 risk gate evaluation results.
   - `neurobet_sync_settlements`: Sweeps finished matches and settles open bets.
   - `neurobet_get_data_quality_report`: Audits timeline monotonicity and absence of data leakage.
   - `neurobet_get_scheduler_stats`: Queue length and candidate dispatch rate.

## Dynamic Resources

- `neurobet://system/health`
- `neurobet://models/registry`
- `neurobet://bankroll/status`
- `neurobet://metrics/prometheus`

## Verification

Run the test suite:
```bash
python3 tests/mcp/test_mcp_server.py
```
