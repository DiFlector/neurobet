"""
Streamable HTTP MCP endpoint for Cursor (the agent is the client).

Mounted at POST /api/mcp so it rides the existing Next.js /api rewrite to backend —
no extra nginx port.

Read-only tools expose the same slices as the Stats page, the admin panel, and the
eval pack. Destructive admin actions (reset DB / model / bankroll, cancel bets,
toggle inference) are intentionally not exposed.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Optional
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger("mcp_eval")

router = APIRouter()

PROTOCOL = "2025-03-26"
SERVER_INFO = {"name": "neurobet-eval", "version": "1.1.0"}


def _tool(name: str, description: str, properties: Optional[dict] = None) -> dict:
    return {
        "name": name,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": properties or {},
            "additionalProperties": False,
        },
    }


_LIMIT = {
    "type": "integer",
    "minimum": 100,
    "maximum": 100000,
    "description": "Resolved bets to score. Default 80000.",
}

_BET_TYPES_LIMIT = {
    "type": "integer",
    "minimum": 0,
    "maximum": 500,
    "description": (
        "Max bet-type rows per sport (0 = sport totals only). "
        "Omit for the full breakdown."
    ),
}

_LOGS_LIMIT = {
    "type": "integer",
    "minimum": 0,
    "maximum": 300,
    "description": "Max recent AI log entries, newest first. Default 80.",
}

_TRAINING_RUNS_LIMIT = {
    "type": "integer",
    "minimum": 0,
    "maximum": 200,
    "description": "Max recent training-pass rows. Default 40.",
}

_BACKTEST_RUNS = {
    "type": "integer",
    "minimum": 0,
    "maximum": 50,
    "description": "Max recent condensed backtest-history rows. Default 15.",
}

_LOG_CATEGORY = {
    "type": "string",
    "description": (
        "Filter logs by category: TRAINING, INFERENCE, BANKROLL, SYSTEM, ALL. "
        "Default ALL."
    ),
}

TOOLS = [
    # --- composite ---
    _tool(
        "get_eval_pack",
        (
            "Full NeuroBet eval pack (same JSON as the admin «пакет для агента»): "
            "filters, ensemble weights, latest full backtest, ROI/stats, training "
            "health, recent training runs, bankroll, logs. Does not run a new backtest. "
            "Prefer a granular tool when you only need one slice."
        ),
        {
            "bet_types_limit": {**_BET_TYPES_LIMIT, "description": "Default 40."},
            "training_runs_limit": _TRAINING_RUNS_LIMIT,
            "logs_limit": _LOGS_LIMIT,
            "backtest_runs": _BACKTEST_RUNS,
        },
    ),
    _tool(
        "run_eval_pack",
        (
            "Run a fresh backtest (default 40000 samples, 15–60s) then return the same "
            "eval pack with current weights. Use when judging tonight's model."
        ),
        {"limit": _LIMIT},
    ),
    _tool(
        "get_overview",
        (
            "Lighter consolidated snapshot than get_eval_pack: db_stats, bet_type_stats, "
            "roi_stats, bankroll, ai_settings, training_health, backtest_history, "
            "recent_ai_logs. No ensemble, no full latest_backtest JSON."
        ),
        {
            "bet_types_limit": {**_BET_TYPES_LIMIT, "description": "Default 5."},
            "logs_limit": {**_LOGS_LIMIT, "description": "Default 60."},
            "backtest_runs": {**_BACKTEST_RUNS, "description": "Default 10."},
        },
    ),
    _tool(
        "get_admin",
        (
            "Everything the admin panel polls (read-only): AI settings, training health, "
            "training-run trend, backtest history, DB stats, bankroll, live bets, "
            "recent AI logs. No destructive actions. No full latest_backtest JSON."
        ),
        {
            "logs_limit": _LOGS_LIMIT,
            "training_runs_limit": _TRAINING_RUNS_LIMIT,
            "backtest_runs": _BACKTEST_RUNS,
            "live_bets_limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "description": "Max live-bet rows. Default 200.",
            },
        },
    ),
    _tool(
        "get_stats",
        (
            "Everything on the «Статистика» page: db_stats (archive size / live counts), "
            "bet_type_stats (guess-rate by sport and market), roi_stats (ROI and Brier "
            "by coefficient band)."
        ),
        {"bet_types_limit": _BET_TYPES_LIMIT},
    ),
    # --- stats page slices ---
    _tool(
        "get_db_stats",
        (
            "Archive / header stats: live events, odds-history count, DB size, "
            "finished-bet counts, headline guess-rate."
        ),
    ),
    _tool(
        "get_bet_type_stats",
        (
            "Guess-rate breakdown by sport and bet type — the «Разбивка угадывания» "
            "table on «Статистика». Same universe the bot actually stakes."
        ),
        {
            "sport": {
                "type": "string",
                "description": "Optional sport name filter (e.g. «Футбол»). Case-insensitive substring.",
            },
            "bet_types_limit": _BET_TYPES_LIMIT,
        },
    ),
    _tool(
        "get_roi_stats",
        (
            "Flat-stake ROI and Brier vs bookmaker-implied baseline, bucketed by "
            "coefficient — the headline ROI table on «Статистика»."
        ),
    ),
    # --- admin panel slices ---
    _tool(
        "get_ai_settings",
        "Whether inference (ai_enabled), online training (training_enabled), "
        "and quality_gate_bypass are on.",
    ),
    _tool(
        "get_ai_logs",
        (
            "Live admin log feed (TRAINING / INFERENCE / BANKROLL / SYSTEM), newest first. "
            "Same rows as the admin terminal."
        ),
        {"limit": _LOGS_LIMIT, "category": _LOG_CATEGORY},
    ),
    _tool(
        "get_training_health",
        (
            "Overfitting traffic light from the admin status block: ok / warning / danger "
            "/ unknown, plus the individual signals (best_epoch streak, backtest Brier, "
            "ROI trend, val_loss trend) and the live quality_gate that can block virtual "
            "live bets."
        ),
    ),
    _tool(
        "get_training_runs",
        (
            "Per-training-pass metrics that feed the admin TrainingTrendChart: "
            "val_loss, val_guess_rate, train_loss, best_epoch. Newest first."
        ),
        {"limit": _TRAINING_RUNS_LIMIT},
    ),
    _tool(
        "get_backtest_history",
        (
            "Condensed backtest-run trend (ROI / accuracy / Brier vs market / bets placed) "
            "that feeds the admin QualityTrendChart. Not the full per-run JSON."
        ),
        {"limit": _BACKTEST_RUNS},
    ),
    _tool(
        "get_backtest_review",
        (
            "Agent review of the latest backtest: edge verdict, quality_gate, walk-forward "
            "stability, live funnel, head-alignment, flags, delta vs previous run. "
            "Prefer this over parsing the full backtest JSON manually."
        ),
    ),
    _tool(
        "get_latest_backtest",
        (
            "Full latest backtest JSON on disk (overall, by_sport, by_coefficient, "
            "current vs historical vs market). Same payload the admin «Бэктест» download "
            "saves. Does not start a new run."
        ),
    ),
    _tool(
        "run_backtest",
        (
            "Run a fresh backtest only (admin «Бэктест» button) and return that result. "
            "Does not assemble the rest of the eval pack. 15–60s. Default 40000 samples."
        ),
        {"limit": _LIMIT},
    ),
    _tool(
        "get_ensemble",
        (
            "Current live ensemble weights: blend_weight, market_weight, "
            "decision_threshold, sport_decision_thresholds."
        ),
    ),
    _tool(
        "get_filters",
        (
            "Live betting gates: allowed sports / factor IDs, live stake sports/markets, "
            "total-line ranges, min/max coefficient, min EV, min market support."
        ),
    ),
    _tool(
        "get_bankroll",
        "Live and training bankroll accounts (balances, stakes, P/L) from the admin wallet block.",
    ),
    _tool(
        "get_live_bets",
        (
            "The bot's simulated live bets (open + settled), newest first — same list "
            "the admin panel uses for the open-bet count."
        ),
        {
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 500,
                "description": "Default 100.",
            },
            "offset": {"type": "integer", "minimum": 0, "description": "Default 0."},
            "status": {
                "type": "string",
                "description": "Optional status filter: open, settled, won, lost, void, cancelled.",
            },
        },
    ),
    # --- dashboard ---
    _tool(
        "get_top_neurobets",
        (
            "Active LIVE predictions («Нейроставки AI TOP» / «Активные LIVE Прогнозы»). "
            "Default verdict=win — only outcomes the model wants to bet."
        ),
        {
            "sport": {"type": "string", "description": "Filter by sport path."},
            "sort": {
                "type": "string",
                "enum": ["best", "safe"],
                "description": "Sort mode. Default best.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "description": "Default 50.",
            },
            "offset": {"type": "integer", "minimum": 0, "description": "Default 0."},
            "verdict": {
                "type": "string",
                "enum": ["win", "loss", "all"],
                "description": "Model verdict filter. Default win.",
            },
            "search": {"type": "string", "description": "Search by team, match, or bet type."},
        },
    ),
    _tool(
        "get_neurobets_history",
        (
            "Judged prediction history (guessed / not guessed / push / pending) with "
            "summary counts — the dashboard history tab."
        ),
        {
            "sport": {"type": "string", "description": "Filter by sport path."},
            "search": {"type": "string", "description": "Search by team or match name."},
            "outcome": {
                "type": "string",
                "enum": ["correct", "incorrect", "push", "pending"],
                "description": "Filter by judged outcome.",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "description": "Default 50.",
            },
            "offset": {"type": "integer", "minimum": 0, "description": "Default 0."},
        },
    ),
]

TOOL_NAMES = {t["name"] for t in TOOLS}


def _rpc(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_err(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _ok(payload: Any) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, indent=2)}]}


def _clamp_int(arguments: dict, key: str, default: int, lo: int, hi: int) -> int:
    raw = arguments.get(key)
    if raw is None or raw == "":
        return default
    return max(lo, min(int(raw), hi))


def _opt_int(arguments: dict, key: str) -> Optional[int]:
    raw = arguments.get(key)
    if raw is None or raw == "":
        return None
    return int(raw)


def _opt_str(arguments: dict, key: str) -> Optional[str]:
    raw = arguments.get(key)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _trim_bet_types(bt_stats: dict, limit: Optional[int]) -> dict:
    if limit is None:
        return bt_stats
    for sport in bt_stats.get("sports", []):
        sport["bet_types"] = sport.get("bet_types", [])[:limit]
    return bt_stats


def _filter_sports(bt_stats: dict, sport: Optional[str]) -> dict:
    if not sport:
        return bt_stats
    needle = sport.lower()
    bt_stats["sports"] = [
        row for row in bt_stats.get("sports", [])
        if needle in (row.get("sport") or "").lower()
    ]
    return bt_stats


def _filter_logs(logs: list, category: Optional[str], limit: Optional[int]) -> list:
    if category and category.upper() != "ALL":
        cat = category.upper()
        logs = [row for row in logs if (row.get("category") or "").upper() == cat]
    if limit is not None:
        logs = logs[:limit]
    return logs


def _call_tool(name: str, arguments: Optional[dict]) -> dict:
    arguments = arguments or {}
    if name not in TOOL_NAMES:
        raise RuntimeError(f"unknown tool {name}")

    # Lazy import — mcp_eval is loaded from main.py, tools run after main is ready.
    import main as m

    if name == "get_eval_pack":
        return _ok(m.build_eval_pack(
            bet_types_limit=_clamp_int(arguments, "bet_types_limit", 40, 0, 500),
            training_runs_limit=_clamp_int(arguments, "training_runs_limit", 40, 0, 200),
            logs_limit=_clamp_int(arguments, "logs_limit", 80, 0, 300),
            backtest_runs=_clamp_int(arguments, "backtest_runs", 15, 0, 50),
        ))

    if name == "run_eval_pack":
        limit = _clamp_int(arguments, "limit", 80000, 100, 100000)
        return _ok(m.create_eval_pack(m.EvalPackRequest(run_backtest=True, limit=limit)))

    if name == "get_overview":
        return _ok(m.read_ai_overview(
            bet_types_limit=_clamp_int(arguments, "bet_types_limit", 5, 0, 200),
            logs_limit=_clamp_int(arguments, "logs_limit", 60, 0, 300),
            backtest_runs=_clamp_int(arguments, "backtest_runs", 10, 0, 50),
        ))

    if name == "get_admin":
        logs_limit = _clamp_int(arguments, "logs_limit", 80, 0, 300)
        training_runs_limit = _clamp_int(arguments, "training_runs_limit", 40, 0, 200)
        backtest_runs = _clamp_int(arguments, "backtest_runs", 15, 0, 50)
        live_bets_limit = _clamp_int(arguments, "live_bets_limit", 200, 1, 500)
        live = m.get_live_bets(limit=live_bets_limit, offset=0)
        open_count = sum(1 for b in live.get("items", []) if b.get("status") == "open")
        return _ok({
            "generated_at": m.now_moscow().strftime("%Y-%m-%d %H:%M:%S"),
            "ai_settings": m.read_admin_ai_settings().get("settings"),
            "training_health": m.admin_training_health().get("health"),
            "training_runs": (m.admin_training_runs().get("runs") or [])[:training_runs_limit],
            "backtest_history": (m.admin_backtest_history().get("runs") or [])[:backtest_runs],
            "db_stats": m.get_db_stats(),
            "bankroll": m.get_bankroll_state(),
            "live_bets": {"total": live.get("total"), "open_count": open_count, "items": live.get("items")},
            "recent_ai_logs": (m.read_admin_ai_logs().get("logs") or [])[:logs_limit],
        })

    if name == "get_stats":
        bt = m.get_bet_type_stats()
        return _ok({
            "generated_at": m.now_moscow().strftime("%Y-%m-%d %H:%M:%S"),
            "db_stats": m.get_db_stats(),
            "bet_type_stats": _trim_bet_types(bt, _opt_int(arguments, "bet_types_limit")),
            "roi_stats": m.get_roi_stats(),
        })

    if name == "get_db_stats":
        return _ok(m.get_db_stats())

    if name == "get_bet_type_stats":
        bt = m.get_bet_type_stats()
        bt = _filter_sports(bt, _opt_str(arguments, "sport"))
        return _ok(_trim_bet_types(bt, _opt_int(arguments, "bet_types_limit")))

    if name == "get_roi_stats":
        return _ok(m.get_roi_stats())

    if name == "get_ai_settings":
        return _ok(m.read_admin_ai_settings())

    if name == "get_ai_logs":
        logs = m.read_admin_ai_logs().get("logs") or []
        return _ok({
            "logs": _filter_logs(logs, _opt_str(arguments, "category"), _opt_int(arguments, "limit")),
        })

    if name == "get_training_health":
        return _ok(m.admin_training_health())

    if name == "get_training_runs":
        runs = m.admin_training_runs().get("runs") or []
        limit = _opt_int(arguments, "limit")
        return _ok({"runs": runs if limit is None else runs[: max(0, min(limit, 200))]})

    if name == "get_backtest_history":
        runs = m.admin_backtest_history().get("runs") or []
        limit = _opt_int(arguments, "limit")
        return _ok({"runs": runs if limit is None else runs[: max(0, min(limit, 50))]})

    if name == "get_backtest_review":
        return _ok(m.admin_backtest_review())

    if name == "get_latest_backtest":
        snap = m._ai_eval_snapshot(training_runs_limit=0, logs_limit=0, backtest_runs=0)
        return _ok({"backtest": snap.get("latest_backtest")})

    if name == "run_backtest":
        limit = _clamp_int(arguments, "limit", 80000, 100, 100000)
        return _ok(m.admin_run_backtest({"limit": limit}))

    if name == "get_ensemble":
        snap = m._ai_eval_snapshot(training_runs_limit=0, logs_limit=0, backtest_runs=0)
        return _ok({"ensemble": snap.get("ensemble")})

    if name == "get_filters":
        return _ok(m._filters_snapshot())

    if name == "get_bankroll":
        return _ok(m.get_bankroll_state())

    if name == "get_live_bets":
        res = m.get_live_bets(
            limit=_clamp_int(arguments, "limit", 100, 1, 500),
            offset=_clamp_int(arguments, "offset", 0, 0, 1_000_000),
            status=_opt_str(arguments, "status"),
        )
        return _ok({"total": res.get("total"), "count": len(res.get("items") or []), "items": res.get("items") or []})

    if name == "get_top_neurobets":
        res = m.get_top_neurobets(
            sport_filter=_opt_str(arguments, "sport"),
            sort_mode=_opt_str(arguments, "sort") or "best",
            limit=_clamp_int(arguments, "limit", 50, 1, 200),
            offset=_clamp_int(arguments, "offset", 0, 0, 1_000_000),
            verdict=_opt_str(arguments, "verdict") or "win",
            search=_opt_str(arguments, "search"),
        )
        return _ok({"total": res["total"], "count": len(res["items"]), "bets": res["items"]})

    if name == "get_neurobets_history":
        res = m.get_neurobets_history(
            sport_filter=_opt_str(arguments, "sport"),
            search=_opt_str(arguments, "search"),
            outcome_filter=_opt_str(arguments, "outcome"),
            limit=_clamp_int(arguments, "limit", 50, 1, 200),
            offset=_clamp_int(arguments, "offset", 0, 0, 1_000_000),
        )
        return _ok({
            "summary": res["summary"],
            "count": len(res["history"]),
            "history": res["history"],
        })

    raise RuntimeError(f"unhandled tool {name}")


def _handle_message(msg: dict) -> Optional[dict]:
    method = msg.get("method")
    msg_id = msg.get("id")
    if method == "initialize":
        client_ver = ((msg.get("params") or {}).get("protocolVersion")) or PROTOCOL
        return _rpc(msg_id, {
            "protocolVersion": client_ver or PROTOCOL,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": (
                "NeuroBet MCP. Prefer a granular tool when you only need one slice "
                "(get_roi_stats, get_training_health, get_ai_logs, …). "
                "Use get_stats for the «Статистика» page, get_admin for the admin panel "
                "reads, get_overview for a light all-in-one, get_eval_pack for a full "
                "model-review JSON (filters + ensemble + latest backtest). "
                "Call run_eval_pack or run_backtest only when a fresh backtest with "
                "current weights is required (15–60s). Read-only: no DB/model/bankroll resets."
            ),
        })
    if method in ("notifications/initialized", "notifications/cancelled") or msg_id is None:
        return None
    if method == "tools/list":
        return _rpc(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        try:
            return _rpc(msg_id, _call_tool(params.get("name"), params.get("arguments")))
        except RuntimeError as e:
            logger.warning("MCP tool %s: %s", params.get("name"), e)
            return _rpc(msg_id, {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            })
        except Exception as e:
            logger.exception("MCP tool %s failed", params.get("name"))
            return _rpc(msg_id, {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            })
    if method == "ping":
        return _rpc(msg_id, {})
    if method == "resources/list":
        return _rpc(msg_id, {"resources": []})
    if method == "prompts/list":
        return _rpc(msg_id, {"prompts": []})
    return _rpc_err(msg_id, -32601, f"Method not found: {method}")


def _json_response(payload: Any, session_id: str, status: int = 200) -> JSONResponse:
    return JSONResponse(
        payload,
        status_code=status,
        headers={
            "MCP-Protocol-Version": PROTOCOL,
            "Mcp-Session-Id": session_id,
            "Access-Control-Expose-Headers": "Mcp-Session-Id, MCP-Protocol-Version",
        },
    )


@router.api_route("/api/mcp", methods=["POST", "GET", "DELETE", "OPTIONS"])
async def mcp_endpoint(request: Request):
    session_id = request.headers.get("mcp-session-id") or str(uuid4())
    if request.method == "OPTIONS":
        return Response(status_code=204, headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "*",
            "Access-Control-Expose-Headers": "Mcp-Session-Id, MCP-Protocol-Version",
        })
    if request.method == "DELETE":
        return Response(status_code=204, headers={"Mcp-Session-Id": session_id})
    if request.method == "GET":
        # Streamable HTTP optional GET (server-to-client SSE). Stateless: nothing to stream.
        return Response(status_code=405, headers={"Allow": "POST", "Mcp-Session-Id": session_id})

    try:
        body = await request.json()
    except Exception:
        return _json_response(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
            session_id,
            status=400,
        )

    # tools/call (especially run_eval_pack / run_backtest) is CPU/IO bound and can
    # take 15–60s — keep the asyncio loop free for the scraper.
    if isinstance(body, list):
        replies = []
        for m in body:
            r = await asyncio.to_thread(_handle_message, m)
            if r is not None:
                replies.append(r)
        return _json_response(replies, session_id)

    if not isinstance(body, dict):
        return _json_response(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}},
            session_id,
            status=400,
        )

    reply = await asyncio.to_thread(_handle_message, body)
    if reply is None:
        return Response(status_code=202, headers={"Mcp-Session-Id": session_id})
    return _json_response(reply, session_id)
