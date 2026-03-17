"""Mock MCP server for switchboard routing benchmarks.

Implements ``route_to_butler`` and ``notify`` as real MCP tools served via
FastMCP over streamable HTTP. Captures all calls for later assertion without
actually dispatching to any butler.
"""

from __future__ import annotations

import socket
import threading
from typing import Any

import uvicorn
from fastmcp import FastMCP


class MockMCPServer:
    """Lightweight MCP server that captures route_to_butler / notify calls.

    Usage::

        server = MockMCPServer()
        server.start()          # blocks until server is ready
        # ... run benchmark, pointing MCP config at server.url ...
        calls = server.get_captured_calls()
        server.stop()
    """

    def __init__(self) -> None:
        self._captures: list[dict[str, Any]] = []
        self._mcp = FastMCP("benchmark-switchboard")
        self._port = _find_free_port()
        self._thread: threading.Thread | None = None
        self._server: uvicorn.Server | None = None
        self._register_tools()

    def _register_tools(self) -> None:
        @self._mcp.tool()
        def route_to_butler(
            butler: str,
            prompt: str,
            context: str | None = None,
            complexity: str | None = None,
        ) -> dict:
            """Route a message to a specialist butler."""
            self._captures.append(
                {
                    "tool": "route_to_butler",
                    "butler": butler,
                    "prompt": prompt,
                    "complexity": complexity,
                }
            )
            return {"status": "ok", "butler": butler}

        @self._mcp.tool()
        def notify(
            channel: str,
            message: str,
            recipient: str | None = None,
            subject: str | None = None,
            intent: str | None = None,
        ) -> dict:
            """Send an outbound notification."""
            self._captures.append(
                {
                    "tool": "notify",
                    "channel": channel,
                    "recipient": recipient,
                }
            )
            return {"status": "ok"}

    @property
    def port(self) -> int:
        return self._port

    @property
    def url(self) -> str:
        return f"http://localhost:{self._port}/mcp"

    def reset_captures(self) -> None:
        self._captures.clear()

    def get_captured_calls(self) -> list[dict[str, Any]]:
        return list(self._captures)

    def start(self) -> None:
        """Start the server in a background daemon thread."""
        app = self._mcp.http_app()
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self._port,
            log_level="warning",
        )
        self._server = uvicorn.Server(config)

        self._thread = threading.Thread(target=self._server.run, daemon=True)
        self._thread.start()

        # Wait for server to be ready
        import time

        for _ in range(50):
            try:
                import httpx

                httpx.get(f"http://localhost:{self._port}/mcp", timeout=0.5)
                return
            except Exception:
                time.sleep(0.1)

    def stop(self) -> None:
        """Signal the server to shut down."""
        if self._server:
            self._server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]
