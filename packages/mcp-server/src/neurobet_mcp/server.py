"""
Main stdio JSON-RPC 2.0 server implementing Model Context Protocol (MCP 2024-11-05).
Compatible with Cursor, Antigravity, Claude Code, and Claude Desktop.
Zero external dependencies (pure Python 3 standard library).
"""

import json
import logging
import os
import sys
from typing import Any, Dict, Optional

try:
    from .client import NeurobetClient
    from .prompts import register_prompts
    from .resources import register_resources
    from .tools import register_tools
except (ImportError, ValueError):
    # Fallback when executed directly as script: python3 server.py
    _pkg_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _pkg_dir not in sys.path:
        sys.path.insert(0, _pkg_dir)
    from neurobet_mcp.client import NeurobetClient
    from neurobet_mcp.prompts import register_prompts
    from neurobet_mcp.resources import register_resources
    from neurobet_mcp.tools import register_tools


# Configure stderr logging so standard out remains strictly JSON-RPC messages
logging.basicConfig(
    stream=sys.stderr,
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] [neurobet-mcp] %(message)s",
)
logger = logging.getLogger("neurobet-mcp")

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "neurobet-mcp"
SERVER_VERSION = "1.0.0"


class MCPServer:
    def __init__(self, client: Optional[NeurobetClient] = None):
        self.client = client or NeurobetClient()
        self.tools_schema, self.tool_handlers = register_tools(self.client)
        self.resources_schema, self.resource_readers = register_resources(self.client)
        self.prompts_schema, self.prompt_generators = register_prompts()
        logger.info(
            "Initialized Neurobet MCP Server: %d tools, %d resources, %d prompts",
            len(self.tools_schema),
            len(self.resources_schema),
            len(self.prompts_schema),
        )

    def handle_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Processes a single JSON-RPC 2.0 request or notification."""
        msg_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        # Handle notifications (no id)
        if msg_id is None:
            if method == "notifications/initialized":
                logger.info("Client completed initialization handshake.")
            return None

        # Standard methods
        try:
            if method == "initialize":
                return self._response(msg_id, {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {
                        "tools": {"listChanged": False},
                        "resources": {"subscribe": False, "listChanged": False},
                        "prompts": {"listChanged": False},
                    },
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "version": SERVER_VERSION,
                    },
                })

            elif method == "ping":
                return self._response(msg_id, {})

            elif method == "tools/list":
                return self._response(msg_id, {"tools": self.tools_schema})

            elif method == "tools/call":
                tool_name = params.get("name")
                tool_args = params.get("arguments", {})
                if tool_name not in self.tool_handlers:
                    return self._error(msg_id, -32601, f"Unknown tool: '{tool_name}'")

                handler = self.tool_handlers[tool_name]
                try:
                    res_text = handler(tool_args)
                    return self._response(msg_id, {
                        "content": [{"type": "text", "text": res_text}],
                        "isError": False,
                    })
                except Exception as e:
                    logger.exception("Error executing tool %s", tool_name)
                    return self._response(msg_id, {
                        "content": [{"type": "text", "text": f"Tool execution failed: {str(e)}"}],
                        "isError": True,
                    })

            elif method == "resources/list":
                return self._response(msg_id, {"resources": self.resources_schema})

            elif method == "resources/read":
                uri = params.get("uri")
                if uri not in self.resource_readers:
                    return self._error(msg_id, -32602, f"Resource URI not found: '{uri}'")

                reader = self.resource_readers[uri]
                content_text = reader()
                return self._response(msg_id, {
                    "contents": [
                        {
                            "uri": uri,
                            "mimeType": "application/json",
                            "text": content_text,
                        }
                    ]
                })

            elif method == "prompts/list":
                return self._response(msg_id, {"prompts": self.prompts_schema})

            elif method == "prompts/get":
                prompt_name = params.get("name")
                prompt_args = params.get("arguments", {})
                if prompt_name not in self.prompt_generators:
                    return self._error(msg_id, -32602, f"Prompt not found: '{prompt_name}'")

                generator = self.prompt_generators[prompt_name]
                messages = generator(prompt_args)
                return self._response(msg_id, {
                    "description": f"Prompt template: {prompt_name}",
                    "messages": messages,
                })

            else:
                return self._error(msg_id, -32601, f"Method not supported: '{method}'")

        except Exception as e:
            logger.exception("Unexpected error processing JSON-RPC method %s", method)
            return self._error(msg_id, -32603, f"Internal error: {str(e)}")

    def _response(self, msg_id: Any, result: Any) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": result,
        }

    def _error(self, msg_id: Any, code: int, message: str) -> Dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {
                "code": code,
                "message": message,
            },
        }

    def run_stdio(self):
        """Standard line-delimited JSON-RPC loop over stdin / stdout."""
        logger.info("Neurobet MCP Server running on stdio...")
        while True:
            line = sys.stdin.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                request = json.loads(line)
            except json.JSONDecodeError as e:
                logger.error("Failed to parse JSON-RPC line: %s", e)
                err_resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()
                continue

            response = self.handle_request(request)
            if response is not None:
                sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
                sys.stdout.flush()


def main():
    server = MCPServer()
    server.run_stdio()


if __name__ == "__main__":
    main()
