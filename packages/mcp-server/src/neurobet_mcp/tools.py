"""
Tool definitions and handlers for Neurobet Model Context Protocol (MCP) Server.
Zero external dependencies.
"""

import json
from typing import Any, Callable, Dict, List, Tuple

try:
    from .client import NeurobetClient
except (ImportError, ValueError):
    from neurobet_mcp.client import NeurobetClient




def format_json(obj: Any) -> str:
    """Pretty formats response for LLM readability."""
    return json.dumps(obj, indent=2, ensure_ascii=False)


def register_tools(client: NeurobetClient) -> Tuple[List[Dict[str, Any]], Dict[str, Callable[[Dict[str, Any]], str]]]:
    """
    Returns tool specifications (JSON Schema) and callable execution handlers.
    """
    tools: List[Dict[str, Any]] = []
    handlers: Dict[str, Callable[[Dict[str, Any]], str]] = {}

    def add_tool(name: str, description: str, parameters: Dict[str, Any], handler: Callable[[Dict[str, Any]], str]):
        tools.append({
            "name": name,
            "description": description,
            "inputSchema": parameters,
        })
        handlers[name] = handler

    # 1. System Health
    add_tool(
        name="neurobet_get_system_health",
        description="Returns real-time health status of all Neurobet cluster components (Postgres, Redis, MinIO, Backend, Collector, Bet-Manager, Neural, LLM, Research, Prometheus, Grafana).",
        parameters={
            "type": "object",
            "properties": {},
        },
        handler=lambda args: format_json(client.get_system_status()),
    )

    # 2. Prometheus Query
    add_tool(
        name="neurobet_query_prometheus",
        description="Executes a PromQL instant query against the live Prometheus server to inspect latencies, errors, RPS, drift, and system metrics.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The PromQL expression to evaluate (e.g. 'rate(collector_polls_total[1m])', 'virtual_bankroll_balance_rub', 'sum(ml_predictions_total)')",
                },
            },
            "required": ["query"],
        },
        handler=lambda args: format_json(client.query_prometheus(args["query"])),
    )

    # 3. Prometheus Targets
    add_tool(
        name="neurobet_get_prometheus_targets",
        description="Retrieves active scrape targets in Prometheus and their current scrape health.",
        parameters={"type": "object", "properties": {}},
        handler=lambda args: format_json(client.get_prometheus_targets()),
    )

    # 4. List Models
    add_tool(
        name="neurobet_list_models",
        description="Lists all ML models in the model registry across all sports (tennis, football, hockey, etc.), including versions, statuses (ACTIVE, SHADOW, RETIRED), and metrics (ROC-AUC, Brier score).",
        parameters={"type": "object", "properties": {}},
        handler=lambda args: format_json(client.list_models()),
    )

    # 5. Get Active Model
    add_tool(
        name="neurobet_get_active_model",
        description="Returns detailed information on the currently active production ML model for a specific sport.",
        parameters={
            "type": "object",
            "properties": {
                "sport_code": {
                    "type": "string",
                    "description": "Sport code (e.g. 'tennis', 'football', 'hockey', 'basketball', 'volleyball')",
                    "default": "tennis",
                },
            },
        },
        handler=lambda args: format_json(client.get_shadow_models(args.get("sport_code", "tennis"))),
    )

    # 6. Get Shadow Report & Drift
    add_tool(
        name="neurobet_get_shadow_report",
        description="Retrieves the complete Shadow Mode drift analysis report: Population Stability Index (PSI across 10 bins), Wasserstein distance, counterfactual PnL, Win rate delta, and empirical promotion gate eligibility.",
        parameters={
            "type": "object",
            "properties": {
                "sport_code": {
                    "type": "string",
                    "description": "Sport code to evaluate (default: 'tennis')",
                    "default": "tennis",
                },
                "min_days": {
                    "type": "number",
                    "description": "Minimum required shadow observation days threshold (default: 7.0)",
                    "default": 7.0,
                },
            },
        },
        handler=lambda args: format_json(client.get_shadow_report(
            sport_code=args.get("sport_code", "tennis"),
            min_days=float(args.get("min_days", 7.0)),
        )),
    )

    # 7. Register Shadow Model
    add_tool(
        name="neurobet_register_shadow_model",
        description="Places a trained model candidate into SHADOW mode for live parallel counterfactual evaluation alongside the active model.",
        parameters={
            "type": "object",
            "properties": {
                "candidate_version_tag": {
                    "type": "string",
                    "description": "Version tag of the candidate model (e.g. 'v1.0.0_gbdt_20261011')",
                },
                "sport_code": {
                    "type": "string",
                    "description": "Sport code (default: 'tennis')",
                    "default": "tennis",
                },
            },
            "required": ["candidate_version_tag"],
        },
        handler=lambda args: format_json(client.register_shadow_model(
            candidate_tag=args["candidate_version_tag"],
            sport_code=args.get("sport_code", "tennis"),
        )),
    )

    # 8. Promote Model
    add_tool(
        name="neurobet_promote_model",
        description="Promotes a SHADOW model to ACTIVE status if empirical safety gates pass (or with explicit override force=True). Automatically retires the previous active model.",
        parameters={
            "type": "object",
            "properties": {
                "shadow_version_tag": {
                    "type": "string",
                    "description": "Version tag of the shadow model to promote",
                },
                "sport_code": {
                    "type": "string",
                    "description": "Sport code (default: 'tennis')",
                    "default": "tennis",
                },
                "force": {
                    "type": "boolean",
                    "description": "Force promotion even if statistical validation gates have warnings (requires caution)",
                    "default": False,
                },
            },
            "required": ["shadow_version_tag"],
        },
        handler=lambda args: format_json(client.promote_shadow_model(
            shadow_tag=args["shadow_version_tag"],
            sport_code=args.get("sport_code", "tennis"),
            force=bool(args.get("force", False)),
        )),
    )

    # 9. Rollback Model
    add_tool(
        name="neurobet_rollback_model",
        description="Instantly rolls back the current ACTIVE model for a sport to a previous stable version upon detecting distribution drift or degradation.",
        parameters={
            "type": "object",
            "properties": {
                "sport_code": {
                    "type": "string",
                    "description": "Sport code (default: 'tennis')",
                    "default": "tennis",
                },
                "target_version_tag": {
                    "type": "string",
                    "description": "Optional specific version tag to restore. If omitted, restores the most recent previous stable model.",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for model rollback (logged in audit trail)",
                    "default": "Rollback triggered via MCP Agent",
                },
            },
        },
        handler=lambda args: format_json(client.rollback_model(
            sport_code=args.get("sport_code", "tennis"),
            target_tag=args.get("target_version_tag"),
            reason=args.get("reason", "Rollback triggered via MCP Agent"),
        )),
    )

    # 10. Simulate Inference
    add_tool(
        name="neurobet_simulate_inference",
        description="Executes a test ML inference on custom features and odds in a sandbox environment without altering the paper bankroll or placing bets. Returns probabilities, fair odds, edges, and actions.",
        parameters={
            "type": "object",
            "properties": {
                "sport_code": {
                    "type": "string",
                    "description": "Sport code (default: 'tennis')",
                    "default": "tennis",
                },
                "features": {
                    "type": "object",
                    "description": "Feature dictionary (e.g. {'elo_diff': 120.0, 'h2h_win_rate_diff': 0.25, 'recent_form_diff': 0.1})",
                },
                "odds": {
                    "type": "object",
                    "description": "Optional bookmaker odds (e.g. {'outcome_a': 1.75, 'outcome_b': 2.15}) to compute edge",
                },
            },
            "required": ["features"],
        },
        handler=lambda args: format_json(client.simulate_inference(
            features=args["features"],
            odds=args.get("odds"),
            sport_code=args.get("sport_code", "tennis"),
        )),
    )

    # 11. Trigger Training
    add_tool(
        name="neurobet_trigger_training",
        description="Triggers a new model training run with specified algorithm (e.g. 'gbdt', 'logistic_regression') and hyperparameters.",
        parameters={
            "type": "object",
            "properties": {
                "sport_code": {
                    "type": "string",
                    "description": "Sport code (default: 'tennis')",
                    "default": "tennis",
                },
                "model_type": {
                    "type": "string",
                    "description": "Algorithm type ('gbdt', 'logistic_regression')",
                    "default": "gbdt",
                },
                "hyperparameters": {
                    "type": "object",
                    "description": "Hyperparameter dictionary (e.g. {'max_depth': 5, 'learning_rate': 0.05, 'n_estimators': 100})",
                },
            },
        },
        handler=lambda args: format_json(client.trigger_training(
            sport_code=args.get("sport_code", "tennis"),
            model_type=args.get("model_type", "gbdt"),
            hyperparameters=args.get("hyperparameters"),
        )),
    )

    # 12. List Training Runs
    add_tool(
        name="neurobet_list_training_runs",
        description="Lists past and running model training jobs, validation metrics, and dataset metadata.",
        parameters={
            "type": "object",
            "properties": {
                "sport_code": {"type": "string", "description": "Optional sport filter"},
            },
        },
        handler=lambda args: format_json(client.list_training_runs(sport_code=args.get("sport_code"))),
    )

    # 13. List Experiments
    add_tool(
        name="neurobet_list_experiments",
        description="Lists scientific backtesting experiments and benchmarks.",
        parameters={"type": "object", "properties": {}},
        handler=lambda args: format_json(client.list_experiments()),
    )

    # 14. Compare Experiments
    add_tool(
        name="neurobet_compare_experiments",
        description="Statistically compares two backtest experiments (baseline vs candidate) on ROI, P&L, Brier score, and max drawdown.",
        parameters={
            "type": "object",
            "properties": {
                "baseline_id": {"type": "string", "description": "Baseline experiment ID"},
                "candidate_id": {"type": "string", "description": "Candidate experiment ID"},
            },
            "required": ["baseline_id", "candidate_id"],
        },
        handler=lambda args: format_json(client.compare_experiments(args["baseline_id"], args["candidate_id"])),
    )

    # 15. List Live Events
    add_tool(
        name="neurobet_list_live_events",
        description="Lists real-time live and upcoming sporting events tracked by the Fonbet collector.",
        parameters={
            "type": "object",
            "properties": {
                "sport_code": {"type": "string", "description": "Sport filter ('tennis', 'football', 'hockey', 'basketball', 'volleyball')"},
                "status": {"type": "string", "description": "Status filter ('live', 'finished', 'not_started')"},
                "limit": {"type": "integer", "description": "Max events to return", "default": 25},
            },
        },
        handler=lambda args: format_json(client.get_events(
            sport_code=args.get("sport_code"),
            status=args.get("status"),
            limit=int(args.get("limit", 25)),
        )),
    )

    # 16. Get Event Timeline
    add_tool(
        name="neurobet_get_event_timeline",
        description="Returns score snapshots and state history for a specific sporting event.",
        parameters={
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Unique event ID"},
            },
            "required": ["event_id"],
        },
        handler=lambda args: format_json(client.get_event_timeline(args["event_id"])),
    )

    # 17. Get Event Odds
    add_tool(
        name="neurobet_get_event_odds",
        description="Returns bookmaker odds snapshots and historical line movements for an event.",
        parameters={
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Unique event ID"},
            },
            "required": ["event_id"],
        },
        handler=lambda args: format_json(client.get_event_odds(args["event_id"])),
    )

    # 18. Get Predictions
    add_tool(
        name="neurobet_get_predictions",
        description="Retrieves recent ML predictions, calculated model probabilities, fair odds, and expected values.",
        parameters={
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Optional event ID filter"},
                "sport_code": {"type": "string", "description": "Optional sport filter"},
                "limit": {"type": "integer", "default": 25},
            },
        },
        handler=lambda args: format_json(client.get_predictions(
            event_id=args.get("event_id"),
            sport_code=args.get("sport_code"),
            limit=int(args.get("limit", 25)),
        )),
    )

    # 19. Get Research Packet
    add_tool(
        name="neurobet_get_research_packet",
        description="Retrieves web research context, news, team sentiment, and injury reports gathered for an event.",
        parameters={
            "type": "object",
            "properties": {
                "event_id": {"type": "string", "description": "Optional event ID"},
            },
        },
        handler=lambda args: format_json(client.get_research(args.get("event_id"))),
    )

    # 20. Bankroll Summary
    add_tool(
        name="neurobet_get_bankroll_summary",
        description="Returns the current paper bankroll status: available cash, active exposure, total equity, P&L, and currency.",
        parameters={"type": "object", "properties": {}},
        handler=lambda args: format_json(client.get_bankroll()),
    )

    # 21. List Virtual Bets
    add_tool(
        name="neurobet_list_virtual_bets",
        description="Lists simulated virtual bets in the immutable paper ledger with status (ACCEPTED, WON, LOST, VOID).",
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Optional filter: 'ACCEPTED', 'WON', 'LOST', 'VOID'"},
                "limit": {"type": "integer", "default": 50},
            },
        },
        handler=lambda args: format_json(client.list_bets(status=args.get("status"), limit=int(args.get("limit", 50)))),
    )

    # 22. List Bet Proposals
    add_tool(
        name="neurobet_list_bet_proposals",
        description="Lists bet proposals evaluated by the Bet-Manager and the results of the 20 risk gate checks.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 50},
            },
        },
        handler=lambda args: format_json(client.list_proposals(limit=int(args.get("limit", 50)))),
    )

    # 23. Sync Settlements
    add_tool(
        name="neurobet_sync_settlements",
        description="Forces a settlement sweep across finished events to resolve open bets and update bankroll balance.",
        parameters={"type": "object", "properties": {}},
        handler=lambda args: format_json(client.sync_settlements()),
    )

    # 24. Data Quality Report
    add_tool(
        name="neurobet_get_data_quality_report",
        description="Audits historical timeline monotonicity, missing snapshots, and verifies absence of temporal data leakage.",
        parameters={"type": "object", "properties": {}},
        handler=lambda args: format_json(client.get_data_quality_report()),
    )

    # 25. Scheduler Stats
    add_tool(
        name="neurobet_get_scheduler_stats",
        description="Returns priority queue length, candidate dispatch rates, and cooldown cache statistics.",
        parameters={"type": "object", "properties": {}},
        handler=lambda args: format_json(client.get_scheduler_stats()),
    )

    return tools, handlers
