#!/usr/bin/env python3
"""
Local stdio fallback. Production is Streamable HTTP on the backend:

  https://diflector.ru/neurobet/api/mcp

Cursor is the client — see .cursor/mcp.json. This script is only for offline/dev:
it speaks MCP over stdin/stdout and forwards JSON-RPC to POST {NEUROBET_API_URL}/api/mcp
so the tool list always matches the live server (eval pack, stats, admin reads).

Env:
  NEUROBET_API_URL  origin with /api (default https://diflector.ru/neurobet)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Optional

API = os.environ.get("NEUROBET_API_URL", "https://diflector.ru/neurobet").rstrip("/")


def _rpc_err(msg_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _forward(msg: dict) -> Optional[dict]:
    """POST the JSON-RPC message to the Streamable HTTP MCP endpoint as-is."""
    method = msg.get("method") or ""
    timeout = 300.0 if method == "tools/call" else 60.0
    url = f"{API}/api/mcp"
    data = json.dumps(msg).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status == 202:
                return None
            raw = resp.read()
            if not raw:
                return None
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read()[:400]
        raise RuntimeError(f"HTTP {e.code} {url}: {body!r}") from e
    except Exception as e:
        raise RuntimeError(f"POST {url}: {e}") from e


def _handle(msg: dict) -> Optional[dict]:
    method = msg.get("method")
    msg_id = msg.get("id")
    try:
        return _forward(msg)
    except Exception as e:
        if msg_id is None or method in ("notifications/initialized", "notifications/cancelled"):
            return None
        return _rpc_err(msg_id, -32000, str(e))


def _read() -> Optional[dict]:
    """Stdio MCP is newline-delimited JSON. Also accept LSP Content-Length framing."""
    first = sys.stdin.buffer.readline()
    if not first:
        return None
    stripped = first.strip()
    if not stripped:
        return _read()
    if stripped[:1] == b"{":
        return json.loads(stripped.decode("utf-8"))
    header = first
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
    sys.stdout.buffer.write(data + b"\n")
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
