"""
Prompt templates for Neurobet MCP Server.
Provides structured workflows for AI Agents (model drift analysis, bet auditing, experiment review).
Zero external dependencies.
"""

from typing import Any, Callable, Dict, List, Tuple


def register_prompts() -> Tuple[List[Dict[str, Any]], Dict[str, Callable[[Dict[str, Any]], List[Dict[str, Any]]]]]:
    """
    Returns MCP Prompt definitions and message generators.
    """
    prompts = [
        {
            "name": "analyze_model_drift",
            "description": "Guides the agent through evaluating PSI drift, Wasserstein distance, and shadow model promotion eligibility.",
            "arguments": [
                {
                    "name": "sport_code",
                    "description": "Sport code to analyze (e.g. 'tennis')",
                    "required": False,
                },
            ],
        },
        {
            "name": "audit_bet_decision",
            "description": "Guides a retrospective audit of a specific virtual bet or rejected proposal (features, odds, ML prediction, LLM verdict, bet-manager risk gates).",
            "arguments": [
                {
                    "name": "bet_or_proposal_id",
                    "description": "UUID of the bet or bet proposal to inspect",
                    "required": True,
                },
            ],
        },
        {
            "name": "evaluate_experiment_results",
            "description": "Guides rigorous statistical evaluation of backtest experiments against baseline models.",
            "arguments": [
                {
                    "name": "experiment_id",
                    "description": "ID of the experiment to audit",
                    "required": True,
                },
            ],
        },
    ]

    generators = {
        "analyze_model_drift": lambda args: [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Please analyze the ML model drift for sport '{args.get('sport_code', 'tennis')}'.\n"
                        "Steps to follow:\n"
                        "1. Call `neurobet_get_shadow_report` to get Population Stability Index (PSI) and Wasserstein distance.\n"
                        "2. Inspect whether PSI exceeds 0.2 (significant distribution shift) or 0.1 (moderate shift).\n"
                        "3. Check counterfactual win rate and ROI delta.\n"
                        "4. Check all promotion criteria gates and provide a clear recommendation (Promote, Continue Observing, or Rollback)."
                    ),
                },
            }
        ],
        "audit_bet_decision": lambda args: [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Please audit the betting decision for ID '{args.get('bet_or_proposal_id')}'.\n"
                        "Steps to follow:\n"
                        "1. Query recent bet proposals using `neurobet_list_bet_proposals`.\n"
                        "2. Check if any of the 20 Bet-Manager risk gates failed (stale odds, high exposure, insufficient edge, bankroll limits).\n"
                        "3. Inspect the ML prediction and compare fair odds against bookmaker odds.\n"
                        "4. Formulate an audit conclusion with full timestamp trace."
                    ),
                },
            }
        ],
        "evaluate_experiment_results": lambda args: [
            {
                "role": "user",
                "content": {
                    "type": "text",
                    "text": (
                        f"Please review the scientific experiment results for '{args.get('experiment_id')}'.\n"
                        "Steps to follow:\n"
                        "1. Call `neurobet_list_experiments` to locate the baseline and candidate metrics.\n"
                        "2. Compare ROI, Win Rate, Brier score, and Max Drawdown.\n"
                        "3. Evaluate if Kelly fractional sizing or value thresholding yielded statistically sound improvements."
                    ),
                },
            }
        ],
    }

    return prompts, generators
