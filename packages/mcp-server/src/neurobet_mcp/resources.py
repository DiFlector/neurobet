"""
Resource definitions and readers for Neurobet MCP Server.
Zero external dependencies.
"""

import json
from typing import Any, Callable, Dict, List, Tuple

try:
    from .client import NeurobetClient
except (ImportError, ValueError):
    from neurobet_mcp.client import NeurobetClient




def register_resources(client: NeurobetClient) -> Tuple[List[Dict[str, Any]], Dict[str, Callable[[], str]]]:
    """
    Returns MCP Resource schemas and read handlers.
    """
    resources = [
        {
            "uri": "neurobet://system/health",
            "name": "Cluster Health & Status",
            "description": "Real-time state snapshot of Neurobet microservices and infrastructure",
            "mimeType": "application/json",
        },
        {
            "uri": "neurobet://models/registry",
            "name": "ML Model Registry",
            "description": "Active, shadow, and retired ML models across all supported sports",
            "mimeType": "application/json",
        },
        {
            "uri": "neurobet://bankroll/status",
            "name": "Virtual Bankroll Status",
            "description": "Virtual account balances, exposure, and paper P&L",
            "mimeType": "application/json",
        },
        {
            "uri": "neurobet://metrics/prometheus",
            "name": "Prometheus Scrape Targets",
            "description": "Current status of Prometheus scrape targets across services",
            "mimeType": "application/json",
        },
    ]

    readers = {
        "neurobet://system/health": lambda: json.dumps(client.get_system_status(), indent=2),
        "neurobet://models/registry": lambda: json.dumps(client.list_models(), indent=2),
        "neurobet://bankroll/status": lambda: json.dumps(client.get_bankroll(), indent=2),
        "neurobet://metrics/prometheus": lambda: json.dumps(client.get_prometheus_targets(), indent=2),
    }

    return resources, readers
