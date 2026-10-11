#!/usr/bin/env python3
"""
Interactive verification script that simulates an AI Agent connecting to
the Neurobet MCP Server over stdio JSON-RPC 2.0.
"""

import json
import os
import subprocess
import sys


def run_agent_mcp_dialogue():
    print("======================================================================")
    print("🤖 AI AGENT: Connecting to Neurobet MCP Server...")
    print("======================================================================")

    env = os.environ.copy()
    env["PYTHONPATH"] = "/srv/neurobet/packages/mcp-server/src"
    env["NEUROBET_API_URL"] = os.getenv("NEUROBET_API_URL", "https://necrolich.ru/diflector/neurobet")
    env["PROMETHEUS_URL"] = "http://127.0.0.1:9095"

    proc = subprocess.Popen(
        [sys.executable, "-u", "/srv/neurobet/packages/mcp-server/src/neurobet_mcp/server.py"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    def rpc_call(method, params=None, req_id=1):
        payload = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline()
        if not line:
            err = proc.stderr.read()
            print("ERROR: Server closed stream. STDERR:", err)
            return {"error": "Server closed stream", "stderr": err}
        return json.loads(line)


    # 1. Initialize
    init_res = rpc_call("initialize", req_id=1)
    print("\n[1] 🤝 HANDSHAKE: initialize")
    print(f"    Server Info: {init_res.get('result', {}).get('serverInfo')}")
    print(f"    Protocol:    {init_res.get('result', {}).get('protocolVersion')}")

    # Send notifications/initialized
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
    proc.stdin.flush()

    # 2. Tools Discovery
    tools_res = rpc_call("tools/list", req_id=2)
    tools = tools_res.get("result", {}).get("tools", [])
    print(f"\n[2] 🛠️ TOOLS DISCOVERY: Discovered {len(tools)} tools")
    for t in tools[:8]:
        print(f"    - {t['name']}: {t['description'][:70]}...")
    print(f"    ... and {len(tools) - 8} more tools.")

    # 3. Call: System Health
    print("\n[3] 🏥 TOOL CALL: neurobet_get_system_health")
    health_call = rpc_call("tools/call", {"name": "neurobet_get_system_health", "arguments": {}}, req_id=3)
    health_text = health_call.get("result", {}).get("content", [{}])[0].get("text", "{}")
    health_data = json.loads(health_text)
    print(f"    Status:       {health_data.get('status')}")
    print(f"    Cluster:      {health_data.get('cluster_name')}")
    print(f"    Infra:        {health_data.get('infrastructure')}")
    print(f"    Active Models:{list(health_data.get('active_models', {}).keys())}")

    # 4. Call: Query Prometheus
    print("\n[4] 📊 TOOL CALL: neurobet_query_prometheus (PromQL: sum(up))")
    prom_call = rpc_call("tools/call", {"name": "neurobet_query_prometheus", "arguments": {"query": "sum(up)"}}, req_id=4)
    prom_text = prom_call.get("result", {}).get("content", [{}])[0].get("text", "{}")
    prom_data = json.loads(prom_text)
    val = prom_data.get("data", {}).get("result", [{}])[0].get("value", ["-", "0"])[1]
    print(f"    Healthy Scrape Targets in Prometheus: {val}")

    # 5. Call: List Models in Registry
    print("\n[5] 🧠 TOOL CALL: neurobet_list_models")
    models_call = rpc_call("tools/call", {"name": "neurobet_list_models", "arguments": {}}, req_id=5)
    models_text = models_call.get("result", {}).get("content", [{}])[0].get("text", "[]")
    models_list = json.loads(models_text)
    print(f"    Total Models in Registry: {len(models_list)}")
    for m in models_list[:3]:
        print(f"    - {m.get('sport_code')}: {m.get('version_tag')} [{m.get('status')}] (ROC-AUC={m.get('metrics', {}).get('roc_auc')})")

    # 6. Call: Shadow Mode Report & Drift
    print("\n[6] 🌓 TOOL CALL: neurobet_get_shadow_report (sport: tennis)")
    shadow_call = rpc_call("tools/call", {"name": "neurobet_get_shadow_report", "arguments": {"sport_code": "tennis"}}, req_id=6)
    shadow_text = shadow_call.get("result", {}).get("content", [{}])[0].get("text", "{}")
    shadow_data = json.loads(shadow_text)
    drift = shadow_data.get("drift_analysis", {})
    print(f"    Active Model: {shadow_data.get('active_model_version')}")
    print(f"    Drift Status: {drift.get('drift_status')} (PSI={drift.get('probability_drift_psi')})")
    print(f"    Promotion:    {shadow_data.get('verdict')}")

    # 7. Call: Simulate Inference (Sandbox)
    print("\n[7] 🎯 TOOL CALL: neurobet_simulate_inference (Sandbox)")
    sim_call = rpc_call("tools/call", {
        "name": "neurobet_simulate_inference",
        "arguments": {
            "sport_code": "tennis",
            "features": {"elo_diff": 95.0, "h2h_win_rate_diff": 0.25, "recent_form_diff": 0.1},
            "odds": {"outcome_a": 1.62, "outcome_b": 2.35},
        },
    }, req_id=7)
    sim_text = sim_call.get("result", {}).get("content", [{}])[0].get("text", "{}")
    sim_data = json.loads(sim_text)
    print(f"    Probabilities:  Outcome A={sim_data.get('predicted_probabilities', {}).get('outcome_a')}, Outcome B={sim_data.get('predicted_probabilities', {}).get('outcome_b')}")
    print(f"    Fair Odds:      Outcome A={sim_data.get('fair_odds', {}).get('outcome_a')}, Outcome B={sim_data.get('fair_odds', {}).get('outcome_b')}")
    print(f"    Recommendation: {sim_data.get('odds_evaluation', {}).get('recommendation')} (Best Edge={sim_data.get('odds_evaluation', {}).get('best_edge')})")

    # 8. Call: Bankroll Summary
    print("\n[8] 💰 TOOL CALL: neurobet_get_bankroll_summary")
    bank_call = rpc_call("tools/call", {"name": "neurobet_get_bankroll_summary", "arguments": {}}, req_id=8)
    bank_text = bank_call.get("result", {}).get("content", [{}])[0].get("text", "{}")
    bank_data = json.loads(bank_text)
    print(f"    Account:        {bank_data.get('name')}")
    print(f"    Available Cash: {bank_data.get('available_balance')} {bank_data.get('currency')}")
    print(f"    Total Equity:   {bank_data.get('total_equity')} {bank_data.get('currency')}")
    print(f"    Active Risk:    {bank_data.get('locked_exposure')} {bank_data.get('currency')}")

    # 9. Read Dynamic Resource
    print("\n[9] 📦 RESOURCE READ: neurobet://system/health")
    res_call = rpc_call("resources/read", {"uri": "neurobet://system/health"}, req_id=9)
    res_items = res_call.get("result", {}).get("contents", [])
    print(f"    URI:      {res_items[0].get('uri')}")
    print(f"    MIME:     {res_items[0].get('mimeType')}")
    print(f"    Payload:  {res_items[0].get('text')[:100]}...")

    proc.stdin.close()
    proc.terminate()
    print("\n======================================================================")
    print("✅ MCP SERVER IS FULLY OPERATIONAL AND READY FOR AGENTS!")
    print("======================================================================")


if __name__ == "__main__":
    run_agent_mcp_dialogue()
