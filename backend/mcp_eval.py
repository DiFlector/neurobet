"""
Streamable HTTP MCP endpoint for Cursor (the agent is the client).

Mounted at POST /api/mcp so it rides the existing Next.js /api rewrite to backend —
no extra nginx port. Tools wrap build_eval_pack / a fresh backtest.
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
SERVER_INFO = {"name": "neurobet-eval", "version": "1.0.0"}

TOOLS = [
    {
        "name": "get_eval_pack",
        "description": (
            "Snapshot of the live NeuroBet model for evaluation: filters, ensemble "
            "weights, latest full backtest, ROI/stats, training health, recent training "
            "runs, bankroll, logs. Does not run a new backtest."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "run_eval_pack",
        "description": (
            "Run a fresh backtest (default 40000 samples, 15–60s) then return the same "
            "eval pack with current weights. Use when judging tonight's model."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 100,
                    "maximum": 50000,
                    "description": "Resolved bets to score. Default 40000.",
                }
            },
            "additionalProperties": False,
        },
    },
]


def _rpc(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_err(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _call_tool(name: str, arguments: Optional[dict]) -> dict:
    arguments = arguments or {}
    # Lazy import — mcp_eval is loaded from main.py, tools run after main is ready.
    import main as backend_main

    if name == "get_eval_pack":
        pack = backend_main.build_eval_pack()
    elif name == "run_eval_pack":
        limit = int(arguments.get("limit") or 40000)
        pack = backend_main.create_eval_pack(
            backend_main.EvalPackRequest(run_backtest=True, limit=limit)
        )
    else:
        raise RuntimeError(f"unknown tool {name}")
    return {"content": [{"type": "text", "text": json.dumps(pack, ensure_ascii=False, indent=2)}]}


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
                "NeuroBet eval pack. Call get_eval_pack to judge the live model "
                "(filters, ensemble, latest backtest, ROI). Call run_eval_pack only "
                "when a fresh backtest with current weights is required (15–60s)."
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

    # tools/call (especially run_eval_pack) is CPU/IO bound and can take 15–60s —
    # keep the asyncio loop free for the scraper.
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
