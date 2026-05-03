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
    """The mounted /api/mcp endpoint accepts an MCP initialize request and
    returns the server's `lfucg-meeting-archive` info."""
    with patch("rag.server.get_chroma_collection"), \
         patch("rag.server.load_clip_metadata", return_value={}), \
         patch("rag.server.get_openai"):
        from rag.server import app
        from fastapi.testclient import TestClient

        client = TestClient(app)
        # `with client:` triggers the FastAPI lifespan, which runs the
        # MCP session manager. Required for the streamable-HTTP transport
        # to accept requests.
        with client:
            response = client.post(
                "/api/mcp/",
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-06-18",
                        "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "1.0"},
                    },
                },
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                    "MCP-Protocol-Version": "2025-06-18",
                },
            )
            assert response.status_code == 200, response.text
            # Streamable-HTTP returns SSE on initialize; the body contains
            # an "event: message" + "data: {...}" with the server's
            # serverInfo + capabilities. The exact field name is "serverInfo"
            # under camelCase JSON-RPC.
            body = response.text
            assert "lfucg-meeting-archive" in body
            assert "serverInfo" in body
