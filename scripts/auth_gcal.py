#!/usr/bin/env python3
"""One-time Google OAuth flow for EverStone.

The redirect_uri is set explicitly to <public_url>/oauth/google/callback,
which Caddy proxies to localhost:8081 (where this script briefly listens).
That means the operator just clicks the auth URL, consents in their
browser, and the redirect lands automatically — no URL copy-paste.

Requires the OAuth client to be of type "Web application" (Desktop-app
clients are restricted to localhost redirects by Google).

The resulting credentials are written as JSON to the es shared credential
store (es/google_auth.py: _DEFAULT_CREDS_PATH). Keep GOOGLE_SCOPES and
CREDS_PATH in sync with es/es/google_auth.py if you change either.
"""
import os
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from google_auth_oauthlib.flow import Flow


# Keep in sync with es/es/google_auth.py: GOOGLE_SCOPES / _DEFAULT_CREDS_PATH
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDS_PATH = Path(os.environ.get("ES_GOOGLE_CREDS_PATH", "/opt/data/hermes/es/google-credentials.json"))
CALLBACK_PATH = "/oauth/google/callback"
PORT = int(os.environ.get("EVERSTONE_GCAL_OAUTH_PORT", "8081"))


_SUCCESS_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>EverStone — Auth complete</title>
<style>
  body{font-family:system-ui,-apple-system,sans-serif;max-width:480px;margin:80px auto;padding:0 20px;color:#222}
  h1{color:#0a7}
  code{background:#f4f4f4;padding:2px 6px;border-radius:3px}
</style></head>
<body>
  <h1>&#x2713; EverStone authorized</h1>
  <p>You can close this tab. Google Calendar is now connected.</p>
  <p>Try it from your terminal:</p>
  <p><code>just es calendar list</code></p>
</body></html>
"""

_FAILURE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>EverStone — Auth failed</title></head>
<body><h1>Auth failed</h1><p>{}</p>
<p>Check the EverStone terminal for details, then retry.</p></body></html>
"""


def _build_flow(client_id: str, client_secret: str, redirect_uri: str) -> Flow:
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=GOOGLE_SCOPES,
    )
    flow.redirect_uri = redirect_uri
    return flow


def main() -> int:
    client_id = os.environ.get("GCALCLI_CLIENT_ID")
    client_secret = os.environ.get("GCALCLI_CLIENT_SECRET")
    public_url = os.environ.get("EVERSTONE_PUBLIC_URL", "").rstrip("/")
    if not client_id or not client_secret:
        print(
            "GCALCLI_CLIENT_ID / GCALCLI_CLIENT_SECRET not set.\n"
            "Configure gcalcli in config.yaml and restart, then re-run.",
            file=sys.stderr,
        )
        return 1
    if not public_url:
        print(
            "EVERSTONE_PUBLIC_URL not set — auth_gcal needs config.public_url\n"
            "so it can register a real HTTPS redirect_uri with Google.",
            file=sys.stderr,
        )
        return 1

    redirect_uri = f"{public_url}{CALLBACK_PATH}"
    flow = _build_flow(client_id, client_secret, redirect_uri)
    auth_url, expected_state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )

    print(
        f"""
EverStone — Google Calendar OAuth
=================================

Open this URL in your browser and consent:

{auth_url}

First time only, you'll see "Google hasn't verified this app" — click
Advanced, then "Go to <app name> (unsafe)" to continue.

After you authorize, Google redirects to:
  {redirect_uri}
which Caddy forwards to this listener — the flow completes automatically
and this command exits.

Listening on port {PORT} for the callback...
"""
    )
    sys.stdout.flush()

    state = {"code": None, "error": None}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            params = parse_qs(urlparse(self.path).query)
            error = params.get("error", [None])[0]
            code = params.get("code", [None])[0]
            received_state = params.get("state", [None])[0]
            if error:
                state["error"] = error
                self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(_FAILURE_HTML.format(error).encode("utf-8"))
                return
            # CSRF protection: the `state` param we sent to Google must
            # come back unchanged. Without this check, an attacker could
            # race a malicious link into our callback during the auth
            # window and cause our flow to exchange THEIR code, storing
            # their tokens instead of the operator's. Reject and keep
            # listening (don't terminate the flow over a stray request).
            if received_state != expected_state or not code:
                self.send_response(400)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(b"Bad request (state mismatch or missing code).")
                return
            state["code"] = code
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_SUCCESS_HTML.encode("utf-8"))

        def log_message(self, *args):  # silence default access log
            return

    try:
        server = HTTPServer(("0.0.0.0", PORT), CallbackHandler)
    except OSError as e:
        if getattr(e, "errno", None) == 98:
            print(
                f"\nPort {PORT} is already in use inside the container.\n"
                "Either another auth flow is running, or override:\n"
                "    EVERSTONE_GCAL_OAUTH_PORT=8082 esadmin auth google",
                file=sys.stderr,
            )
            return 1
        raise

    # Loop until we either get a valid code or a Google-reported error.
    # If a stray / malformed request arrives (wrong state, missing code,
    # random probe), CallbackHandler responds 400 and we keep listening.
    # That way one bad request doesn't break the real Google redirect.
    while state["code"] is None and state["error"] is None:
        server.handle_request()

    if state["error"]:
        print(f"\nOAuth error: {state['error']}", file=sys.stderr)
        return 1

    print("\nCode received. Exchanging for token...")
    flow.fetch_token(code=state["code"])
    credentials = flow.credentials

    CREDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    CREDS_PATH.write_text(credentials.to_json())
    CREDS_PATH.chmod(0o600)

    print(f"\n✓ Auth complete. Credentials saved to {CREDS_PATH}")
    print("Next: discover what calendars are available with:")
    print("    just es calendar list")
    print("Then add the IDs you want to config.yaml under gcalcli.calendars.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
