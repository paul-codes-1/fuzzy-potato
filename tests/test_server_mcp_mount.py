"""Test that the MCP HTTP transport is mounted on the FastAPI app.

Verifies the FastAPI app mounts the streamable-HTTP transport at
/api/mcp (the CloudFront-routable path) and that the JSON-RPC
initialize handshake works end-to-end. Tool execution itself is
covered in test_mcp_server.py against the implementations directly.

Note: only ONE test in this module exercises the lifespan, because the
MCP `StreamableHTTPSessionManager.run()` can only be called once per
instance and the FastMCP server is a module-level singleton.
"""

from __future__ import annotations

from unittest.mock import patch


def test_mcp_endpoint_handles_initialize():
    """The mounted MCP endpoint accepts an MCP initialize request at all four
    public spellings (/api/mcp, /api/mcp/, /mcp, /mcp/) and returns the
    server's `lfucg-meeting-archive` info.

    Without `_MCPTrailingSlashMiddleware` the no-trailing-slash variants 307
    to an absolute URL built from App Runner's internal scheme + host —
    which CloudFront passes through, breaking real clients (claude.ai's MCP
    connector and several others POST without the trailing slash). All four
    paths are exercised in one test because the FastMCP session manager is
    a module-level singleton and `StreamableHTTPSessionManager.run()` can
    only be called once per instance — sharing a single TestClient lifespan
    across the assertions sidesteps that.
    """
    with patch("rag.server.get_vecstore"), \
         patch("rag.server.load_clip_metadata", return_value={}), \
         patch("rag.server.get_openai"):
        from rag.server import app
        from fastapi.testclient import TestClient

        client = TestClient(app, follow_redirects=False)
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "1.0"},
            },
        }
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": "2025-06-18",
        }
        # `with client:` triggers the FastAPI lifespan, which runs the
        # MCP session manager. Required for the streamable-HTTP transport
        # to accept requests.
        with client:
            for path in ("/api/mcp", "/api/mcp/", "/mcp", "/mcp/"):
                response = client.post(path, json=payload, headers=headers)
                assert response.status_code == 200, f"{path}: {response.status_code} {response.text!r}"
                # Streamable-HTTP returns SSE on initialize; the body contains
                # an "event: message" + "data: {...}" with the server's
                # serverInfo + capabilities. The exact field name is "serverInfo"
                # under camelCase JSON-RPC.
                body = response.text
                assert "lfucg-meeting-archive" in body, f"{path}: missing server name in {body!r}"
                assert "serverInfo" in body, f"{path}: missing serverInfo in {body!r}"
