"""Expose the es_* MCP tools over plain streamable HTTP.

This is a bare FastMCP ``streamable-http`` server. It carries **no auth code**:
the endpoint is private (LAN + Tailscale) and the only client that can reach it
is the Claude Code CLI, which sends a static bearer via ``--header``. **Caddy is
the auth wall** — it 401s any request to ``/mcp`` whose Authorization header is
not exactly ``Bearer <mcp_http.token>`` before anything reaches this process.

The server binds loopback only (``127.0.0.1:9400``) behind Caddy. ``main()``
reads ``/opt/config.yaml`` and refuses to start unless ``mcp_http.enabled``.
"""
from urllib.parse import urlparse

from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette

from es import config
from es.mcp_server import mcp

PORT = 9400
HOST = "127.0.0.1"


def _public_host(cfg: dict) -> str:
    """Hostname from the top-level public_url (empty string if unset)."""
    public_url = (cfg.get("public_url") or "").rstrip("/")
    return urlparse(public_url).hostname or ""


def build_app(cfg: dict) -> Starlette:
    """Starlette app serving the FastMCP streamable-http endpoint at /mcp.

    No auth is wired here (Caddy enforces the bearer). We only keep the
    DNS-rebinding host allowlist: FastMCP's streamable-http transport rejects
    (421) any Host it wasn't told to allow, and we sit behind Caddy, which
    forwards the public Host — so allow loopback plus the public_url host (bare
    and with any port).
    """
    host = _public_host(cfg)
    allowed_hosts = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"]
    if host:
        allowed_hosts += [host, f"{host}:*"]
    mcp.settings.transport_security = TransportSecuritySettings(
        allowed_hosts=allowed_hosts,
    )
    return mcp.streamable_http_app()


def main() -> None:
    import uvicorn

    cfg = config.load_config()
    block = cfg.get("mcp_http") or {}
    if not block.get("enabled"):
        raise SystemExit("es mcp-http: mcp_http.enabled is false; refusing to start")

    app = build_app(cfg)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
