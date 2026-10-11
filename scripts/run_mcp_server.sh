#!/usr/bin/env bash
# ==============================================================================
# Neurobet Model Context Protocol (MCP) Server Runner
# Starts the stdio JSON-RPC 2.0 MCP server for AI Agents in Cursor / Antigravity
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

export PYTHONPATH="${REPO_ROOT}/packages/mcp-server/src:${PYTHONPATH:-}"
export NEUROBET_API_URL="${NEUROBET_API_URL:-https://necrolich.ru/diflector/neurobet}"
export PROMETHEUS_URL="${PROMETHEUS_URL:-http://127.0.0.1:9095}"
export GRAFANA_URL="${GRAFANA_URL:-http://127.0.0.1:8086}"

exec python3 -u -m neurobet_mcp.server

