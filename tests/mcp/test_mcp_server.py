"""
Comprehensive test suite for Neurobet MCP Server.
Verifies JSON-RPC 2.0 protocol adherence, tools discovery, resource readers,
prompts, and live integration with Neurobet backend and Prometheus.
"""

import json
import os
import sys

# Add packages/mcp-server/src to path
sys.path.insert(0, "/srv/neurobet/packages/mcp-server/src")

from neurobet_mcp.server import MCPServer
from neurobet_mcp.client import NeurobetClient


def run_tests():
    print("======================================================================")
    print("Starting Neurobet MCP Server Validation Suite")
    print("======================================================================")

    client = NeurobetClient(
        base_url=os.getenv("NEUROBET_API_URL", "https://necrolich.ru/diflector/neurobet"),
        prometheus_url=os.getenv("PROMETHEUS_URL", "http://127.0.0.1:9095"),
    )

    server = MCPServer(client=client)

    # 1. Test Initialize
    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test-agent", "version": "1.0.0"},
        },
    }
    init_res = server.handle_request(init_req)
    assert init_res is not None, "Initialize returned None"
    assert init_res["result"]["protocolVersion"] == "2024-11-05", "Unexpected protocol version"
    assert init_res["result"]["serverInfo"]["name"] == "neurobet-mcp", "Unexpected server name"
    assert "tools" in init_res["result"]["capabilities"], "Missing tools capability"
    print("✓ 1. MCP Initialization handshake passed")

    # 2. Test Tools Discovery
    tools_req = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    tools_res = server.handle_request(tools_req)
    tools = tools_res["result"]["tools"]
    tool_names = [t["name"] for t in tools]
    expected_tools = [
        "neurobet_get_system_health",
        "neurobet_query_prometheus",
        "neurobet_get_prometheus_targets",
        "neurobet_list_models",
        "neurobet_get_active_model",
        "neurobet_get_shadow_report",
        "neurobet_register_shadow_model",
        "neurobet_promote_model",
        "neurobet_rollback_model",
        "neurobet_simulate_inference",
        "neurobet_trigger_training",
        "neurobet_list_training_runs",
        "neurobet_list_experiments",
        "neurobet_compare_experiments",
        "neurobet_list_live_events",
        "neurobet_get_event_timeline",
        "neurobet_get_event_odds",
        "neurobet_get_predictions",
        "neurobet_get_research_packet",
        "neurobet_get_bankroll_summary",
        "neurobet_list_virtual_bets",
        "neurobet_list_bet_proposals",
        "neurobet_sync_settlements",
        "neurobet_get_data_quality_report",
        "neurobet_get_scheduler_stats",
    ]
    for exp in expected_tools:
        assert exp in tool_names, f"Missing tool in schema: {exp}"
    print(f"✓ 2. Tools discovery passed ({len(tools)} tools registered)")

    # 3. Test Resources Listing & Reading
    res_list_req = {"jsonrpc": "2.0", "id": 3, "method": "resources/list", "params": {}}
    res_list = server.handle_request(res_list_req)["result"]["resources"]
    uris = [r["uri"] for r in res_list]
    assert "neurobet://system/health" in uris, "Missing system/health resource"
    assert "neurobet://models/registry" in uris, "Missing models/registry resource"

    # Read system/health resource
    read_req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "resources/read",
        "params": {"uri": "neurobet://system/health"},
    }
    read_res = server.handle_request(read_req)
    assert len(read_res["result"]["contents"]) > 0, "Resource contents empty"
    content_json = json.loads(read_res["result"]["contents"][0]["text"])
    assert content_json.get("status") in ["healthy", "degraded"], "Invalid cluster health content"
    print("✓ 3. Resources listing and reading passed")

    # 4. Test Prompts Listing & Get
    prompt_list_req = {"jsonrpc": "2.0", "id": 5, "method": "prompts/list", "params": {}}
    prompts = server.handle_request(prompt_list_req)["result"]["prompts"]
    p_names = [p["name"] for p in prompts]
    assert "analyze_model_drift" in p_names, "Missing analyze_model_drift prompt"
    assert "audit_bet_decision" in p_names, "Missing audit_bet_decision prompt"

    get_prompt_req = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "prompts/get",
        "params": {"name": "analyze_model_drift", "arguments": {"sport_code": "tennis"}},
    }
    prompt_res = server.handle_request(get_prompt_req)
    assert len(prompt_res["result"]["messages"]) > 0, "Prompt messages empty"
    print("✓ 4. Prompts templates passed")

    # 5. Call Tool: System Health
    call_health = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": "neurobet_get_system_health", "arguments": {}},
    }
    health_res = server.handle_request(call_health)
    assert not health_res["result"]["isError"], "Health tool returned error"
    print("✓ 5. Tool call: neurobet_get_system_health passed")

    # 6. Call Tool: List Models
    call_models = {
        "jsonrpc": "2.0",
        "id": 8,
        "method": "tools/call",
        "params": {"name": "neurobet_list_models", "arguments": {}},
    }
    models_res = server.handle_request(call_models)
    assert not models_res["result"]["isError"], "List models tool returned error"
    print("✓ 6. Tool call: neurobet_list_models passed")

    # 7. Call Tool: Simulate Inference
    call_sim = {
        "jsonrpc": "2.0",
        "id": 9,
        "method": "tools/call",
        "params": {
            "name": "neurobet_simulate_inference",
            "arguments": {
                "sport_code": "tennis",
                "features": {"elo_diff": 85.0, "h2h_win_rate_diff": 0.2},
                "odds": {"outcome_a": 1.70, "outcome_b": 2.20},
            },
        },
    }
    sim_res = server.handle_request(call_sim)
    assert not sim_res["result"]["isError"], "Simulate inference returned error"
    sim_data = json.loads(sim_res["result"]["content"][0]["text"])
    assert "predicted_probabilities" in sim_data, "Missing predicted probabilities"
    print("✓ 7. Tool call: neurobet_simulate_inference passed")

    # 8. Call Tool: Bankroll Summary
    call_bank = {
        "jsonrpc": "2.0",
        "id": 10,
        "method": "tools/call",
        "params": {"name": "neurobet_get_bankroll_summary", "arguments": {}},
    }
    bank_res = server.handle_request(call_bank)
    assert not bank_res["result"]["isError"], "Bankroll tool returned error"
    print("✓ 8. Tool call: neurobet_get_bankroll_summary passed")

    # 9. Call Tool: Shadow Report
    call_shadow = {
        "jsonrpc": "2.0",
        "id": 11,
        "method": "tools/call",
        "params": {"name": "neurobet_get_shadow_report", "arguments": {"sport_code": "tennis"}},
    }
    shadow_res = server.handle_request(call_shadow)
    assert not shadow_res["result"]["isError"], "Shadow report returned error"
    print("✓ 9. Tool call: neurobet_get_shadow_report passed")

    # 10. Call Tool: Query Prometheus
    call_prom = {
        "jsonrpc": "2.0",
        "id": 12,
        "method": "tools/call",
        "params": {"name": "neurobet_query_prometheus", "arguments": {"query": "sum(up)"}},
    }
    prom_res = server.handle_request(call_prom)
    assert not prom_res["result"]["isError"], "Prometheus query returned error"
    print("✓ 10. Tool call: neurobet_query_prometheus passed")

    print("======================================================================")
    print("ALL 10 MCP SERVER VALIDATION CHECKS PASSED SUCCESSFULLY!")
    print("======================================================================")


if __name__ == "__main__":
    run_tests()
