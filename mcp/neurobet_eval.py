#!/usr/bin/env python3
"""
Minimal MCP stdio server: fetch NeuroBet's eval pack so an agent can judge the
live model without a manual JSON download.

Env:
  NEUROBET_API_URL  origin with /api (default https://necrolich.ru/neurobet)

Tools:
  get_eval_pack   — GET snapshot (last backtest on disk, no new run)
  run_eval_pack   — POST: run a backtest then return the full pack (~15–60s)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Optional

API = os.environ.get("NEUROBET_API_URL", "https://necrolich.ru/neurobet").rstrip("/")
PROTOCOL = "2024-11-05"


def _rpc(msg_id: Any, result: Any) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _rpc_err(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _http(method: str, path: str, body: Optional[dict] = None, timeout: float = 30.0) -> dict:
    url = f"{API}{path}"
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code} {url}: {e.read()[:400]!r}") from e
    except Exception as e:
        raise RuntimeError(f"{method} {url}: {e}") from e


TOOLS = [
    {
        "name": "get_eval_pack",
        "description": (
            "Snapshot of NeuroBet for model evaluation: filters, ensemble weights, "
            "latest full backtest, ROI/stats, training health, recent training runs, "
            "bankroll, logs. Does not run a new backtest."
        ),
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "run_eval_pack",
        "description": (
            "Run a fresh backtest (default 40000 samples) then return the same eval pack. "
            "Takes 15–60 seconds. Use when the user wants a current-weights judgment."
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


def _call_tool(name: str, arguments: dict) -> dict:
    if name == "get_eval_pack":
        pack = _http("GET", "/api/ai/eval-pack", timeout=60.0)
    elif name == "run_eval_pack":
        limit = int(arguments.get("limit") or 40000)
        pack = _http(
            "POST",
            "/api/ai/eval-pack",
            {"run_backtest": True, "limit": limit},
            timeout=300.0,
        )
    else:
        raise RuntimeError(f"unknown tool {name}")
    text = json.dumps(pack, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}]}


def _handle(msg: dict) -> Optional[dict]:
    method = msg.get("method")
    msg_id = msg.get("id")
    if method == "initialize":
        return _rpc(msg_id, {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "neurobet-eval", "version": "1.0.0"},
        })
    if method == "notifications/initialized" or method is None or msg_id is None:
        return None
    if method == "tools/list":
        return _rpc(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        params = msg.get("params") or {}
        try:
            return _rpc(msg_id, _call_tool(params.get("name"), params.get("arguments") or {}))
        except Exception as e:
            return _rpc(msg_id, {
                "content": [{"type": "text", "text": f"Error: {e}"}],
                "isError": True,
            })
    if method == "ping":
        return _rpc(msg_id, {})
    return _rpc_err(msg_id, -32601, f"Method not found: {method}")


def _read() -> Optional[dict]:
    header = b""
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        if line in (b"\r\n", b"\n"):
            break
        header += line
    length = 0
    for raw in header.split(b"\r\n"):
        if raw.lower().startswith(b"content-length:"):
            length = int(raw.split(b":", 1)[1].strip())
    if length <= 0:
        return None
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


def _write(msg: dict) -> None:
    data = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(data)}\r\n\r\n".encode("ascii") + data)
    sys.stdout.buffer.flush()


def main() -> None:
    while True:
        msg = _read()
        if msg is None:
            break
        reply = _handle(msg)
        if reply is not None:
            _write(reply)


if __name__ == "__main__":
    main()
