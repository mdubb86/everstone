"""Expose the es_* MCP tools over OAuth-protected streamable HTTP.

Assembles one Starlette app that is simultaneously:
  * an OAuth *resource server* — the FastMCP ``streamable-http`` app mounted at
    ``/mcp``, protected by a bearer-token check backed by the OAuth store, and
    advertising RFC 9728 protected-resource metadata; and
  * the OAuth *authorization server* — the Task-3 routes (``/authorize``,
    ``/token``, ``/register``, ``/revoke`` and AS metadata) mounted at the root.

``main()`` reads ``/opt/config.yaml`` and serves this on 127.0.0.1:9400.
"""
import os
from urllib.parse import urlparse

from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.routing import Mount

from es import config
from es.mcp_server import mcp
from es.oauth_server import oauth_routes
from es.oauth_store import OAuthStore

PORT = 9400
HOST = "127.0.0.1"
DB_PATH = "/opt/data/mcp-oauth/oauth.db"
SCOPE = "es"


class StoreTokenVerifier(TokenVerifier):
    """Resource-server bearer verification backed by the OAuth store."""

    def __init__(self, store: OAuthStore):
        self._store = store

    async def verify_token(self, token: str):
        info = self._store.verify_access(token)
        if not info:
            return None
        return AccessToken(
            token=token,
            client_id=info["client_id"],
            scopes=[s for s in (info.get("scope") or "").split() if s],
        )


def build_app(store: OAuthStore, issuer_url: str, consent_password: str) -> Starlette:
    """Combined ASGI app: FastMCP resource server at /mcp + OAuth AS at root.

    The protected-resource metadata route is derived by the SDK from the
    resource URL's path (RFC 9728 §3.1). We use the issuer root (no path) so
    the metadata is served at ``/.well-known/oauth-protected-resource`` and the
    MCP endpoint keeps its default ``/mcp`` path.
    """
    issuer = issuer_url.rstrip("/")

    # FastMCP's streamable_http_app() reads settings.auth and _token_verifier
    # at call time, so mutating them here before constructing the app is the
    # supported wiring for the installed mcp (1.28) version.
    mcp.settings.auth = AuthSettings(
        issuer_url=issuer,
        resource_server_url=issuer,
        required_scopes=[SCOPE],
    )
    mcp._token_verifier = StoreTokenVerifier(store)

    # FastMCP's streamable-http transport applies DNS-rebinding protection,
    # which rejects (421) any Host it hasn't been told to allow. We bind on
    # loopback behind Caddy, which forwards the public Host, so allow the
    # issuer host (bare and with any port) plus loopback.
    host = urlparse(issuer).hostname or ""
    allowed_hosts = ["127.0.0.1", "127.0.0.1:*", "localhost", "localhost:*"]
    if host:
        allowed_hosts += [host, f"{host}:*"]
    mcp.settings.transport_security = TransportSecuritySettings(
        allowed_hosts=allowed_hosts,
        allowed_origins=[issuer, f"{issuer}:*"],
    )

    mcp_app = mcp.streamable_http_app()

    routes = list(oauth_routes(store, issuer, consent_password, SCOPE))
    routes.append(Mount("/", app=mcp_app))
    return Starlette(routes=routes, lifespan=mcp_app.router.lifespan_context)


def main() -> None:
    import uvicorn

    cfg = config.load_config()
    block = cfg.get("mcp_http") or {}
    if not block.get("enabled"):
        raise SystemExit("es mcp-http: mcp_http.enabled is false; refusing to start")
    issuer = (block.get("issuer_url") or cfg.get("public_url") or "").rstrip("/")
    if not issuer:
        raise SystemExit("es mcp-http: no issuer_url / public_url configured")
    consent_password = (block.get("consent_password") or "").strip()
    if not consent_password:
        raise SystemExit(
            "mcp_http enabled but consent_password is empty — refusing to start "
            "(would be an open auth surface)"
        )

    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    store = OAuthStore(DB_PATH)
    app = build_app(store, issuer, consent_password)
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
