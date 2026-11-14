import base64, hashlib, html, re, secrets
from urllib.parse import urlencode
from starlette.responses import JSONResponse, HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

ALLOWED_REDIRECT_PATTERNS = [
    re.compile(r"^https://claude\.ai/api/mcp/auth_callback$"),
    re.compile(r"^http://localhost(:\d+)?/callback$"),
    re.compile(r"^http://127\.0\.0\.1(:\d+)?/callback$"),
]


def is_allowed_redirect(uri: str) -> bool:
    return any(p.fullmatch(uri or "") for p in ALLOWED_REDIRECT_PATTERNS)


def verify_pkce(verifier: str, challenge: str) -> bool:
    if not verifier or not challenge:
        return False
    digest = hashlib.sha256(verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return secrets.compare_digest(expected, challenge)


_FORM = """<!doctype html><meta charset=utf-8><title>EverStone — authorize</title>
<body style="font-family:system-ui;max-width:24rem;margin:14vh auto;padding:0 1.5rem">
<h1 style="font-size:1.2rem">Authorize Claude to use EverStone tools</h1>
<form method=post>
<input type=password name=password placeholder="Consent password" autofocus
 style="width:100%;padding:.6rem;font-size:1rem">
{hidden}
<button style="margin-top:1rem;padding:.6rem 1rem">Authorize</button>
</form></body>"""


def oauth_routes(store, issuer_url, consent_password, scope="es"):
    issuer = issuer_url.rstrip("/")

    async def metadata(request):
        return JSONResponse({
            "issuer": issuer,
            "authorization_endpoint": issuer + "/authorize",
            "token_endpoint": issuer + "/token",
            "registration_endpoint": issuer + "/register",
            "revocation_endpoint": issuer + "/revoke",
            "scopes_supported": [scope],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"],
            "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["none"],
        })

    async def register(request):
        body = await request.json()
        uris = body.get("redirect_uris") or []
        cid = store.register_client(uris)
        return JSONResponse({"client_id": cid, "redirect_uris": uris,
                             "token_endpoint_auth_method": "none"}, status_code=201)

    def _hidden(params):
        keep = ["client_id", "redirect_uri", "code_challenge", "code_challenge_method", "state", "scope"]
        return "".join(
            f'<input type=hidden name="{k}" value="{html.escape(params.get(k) or "", quote=True)}">'
            for k in keep)

    async def authorize_get(request):
        return HTMLResponse(_FORM.format(hidden=_hidden(request.query_params)))

    async def authorize_post(request):
        form = await request.form()
        if not secrets.compare_digest(form.get("password", ""), consent_password):
            return HTMLResponse("Forbidden", status_code=403)
        client = store.get_client(form.get("client_id", ""))
        redirect_uri = form.get("redirect_uri", "")
        if not client or redirect_uri not in client["redirect_uris"] or not is_allowed_redirect(redirect_uri):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        if form.get("code_challenge_method") != "S256" or not form.get("code_challenge"):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        code = "ac-" + secrets.token_urlsafe(24)
        store.save_code(code, client["client_id"], redirect_uri,
                        form.get("code_challenge"), form.get("scope") or scope)
        q = urlencode({"code": code, "state": form.get("state", "")})
        return RedirectResponse(f"{redirect_uri}?{q}", status_code=302)

    async def token(request):
        form = await request.form()
        grant = form.get("grant_type")
        if grant == "authorization_code":
            data = store.take_code(form.get("code", ""))
            if not data or data["client_id"] != form.get("client_id") \
               or data["redirect_uri"] != form.get("redirect_uri") \
               or not verify_pkce(form.get("code_verifier", ""), data["code_challenge"]):
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return JSONResponse(store.issue_tokens(data["client_id"], data["scope"]))
        if grant == "refresh_token":
            tok = store.rotate_refresh(form.get("refresh_token", ""))
            if not tok:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            return JSONResponse(tok)
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    async def revoke(request):
        form = await request.form()
        store.revoke(form.get("token", ""))
        return Response(status_code=200)

    return [
        Route("/.well-known/oauth-authorization-server", metadata, methods=["GET"]),
        Route("/register", register, methods=["POST"]),
        Route("/authorize", authorize_get, methods=["GET"]),
        Route("/authorize", authorize_post, methods=["POST"]),
        Route("/token", token, methods=["POST"]),
        Route("/revoke", revoke, methods=["POST"]),
    ]
