"""Protocol-level tests for the Streamable HTTP MCP handler — no DB required."""
from __future__ import annotations

from mcp_eval import TOOLS, TOOL_NAMES, _handle_message, _call_tool, SERVER_INFO


EXPECTED = {
    "get_eval_pack",
    "run_eval_pack",
    "get_overview",
    "get_admin",
    "get_stats",
    "get_db_stats",
    "get_bet_type_stats",
    "get_roi_stats",
    "get_ai_settings",
    "get_ai_logs",
    "get_training_health",
    "get_hardware",
    "get_training_runs",
    "get_backtest_history",
    "get_backtest_review",
    "get_latest_backtest",
    "run_backtest",
    "get_ensemble",
    "get_filters",
    "get_bankroll",
    "get_live_bets",
    "get_top_neurobets",
    "get_neurobets_history",
}


def test_tool_list_matches_expected():
    names = {t["name"] for t in TOOLS}
    assert names == EXPECTED
    assert names == TOOL_NAMES
    for tool in TOOLS:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"
        assert tool["inputSchema"]["additionalProperties"] is False


def test_initialize_and_ping():
    init = _handle_message({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2025-03-26"},
    })
    assert init["result"]["serverInfo"] == SERVER_INFO
    assert init["result"]["capabilities"]["tools"]["listChanged"] is False
    assert "granular" in init["result"]["instructions"]

    listed = _handle_message({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(listed["result"]["tools"]) == len(EXPECTED)

    pong = _handle_message({"jsonrpc": "2.0", "id": 3, "method": "ping"})
    assert pong["result"] == {}

    unknown = _handle_message({"jsonrpc": "2.0", "id": 4, "method": "nope"})
    assert unknown["error"]["code"] == -32601


def test_unknown_tool_does_not_need_backend():
    try:
        _call_tool("not_a_real_tool", {})
        assert False, "expected RuntimeError"
    except RuntimeError as e:
        assert "unknown tool" in str(e)

    reply = _handle_message({
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {"name": "not_a_real_tool", "arguments": {}},
    })
    assert reply["result"]["isError"] is True


def test_notification_has_no_reply():
    assert _handle_message({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


if __name__ == "__main__":
    test_tool_list_matches_expected()
    test_initialize_and_ping()
    test_unknown_tool_does_not_need_backend()
    test_notification_has_no_reply()
    print(f"ok — {len(EXPECTED)} tools")
