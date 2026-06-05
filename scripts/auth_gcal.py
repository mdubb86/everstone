#!/usr/bin/env python3
"""One-time Google Calendar OAuth flow for EverStone.

gcalcli 4.x dropped the OOB "paste-back-the-code" flow Google deprecated.
Its built-in flow now requires a local HTTP callback the browser can
reach — which doesn't work cleanly when the container is on a remote
VM. We replicate the OAuth ourselves on a fixed port (default 8081) and
print explicit instructions for the URL-paste fallback in case the
operator's browser can't reach the listening port directly.

The resulting credentials are pickled to `<config>/oauth`, which is
exactly the format gcalcli's `_load_credentials` reads on every command.
"""
import os
import pickle
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow


SCOPES = ["https://www.googleapis.com/auth/calendar"]
CONFIG_FOLDER = Path(os.environ.get("EVERSTONE_GCAL_CONFIG_FOLDER", "/opt/data/hermes/gcalcli"))
PORT = int(os.environ.get("EVERSTONE_GCAL_OAUTH_PORT", "8081"))


def main() -> int:
    client_id = os.environ.get("GCALCLI_CLIENT_ID")
    client_secret = os.environ.get("GCALCLI_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            "GCALCLI_CLIENT_ID / GCALCLI_CLIENT_SECRET not set.\n"
            "Configure gcalcli in config.yaml and restart, then re-run.",
            file=sys.stderr,
        )
        return 1

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
    )

    # Banner with explicit "URL is going to fail; here's how to complete"
    # instructions BEFORE flow.run_local_server() takes over and prints
    # its own (much terser) URL line.
    container = os.environ.get("EVERSTONE_CONTAINER_NAME", "everstone")
    print(
        f"""
EverStone — Google Calendar OAuth
=================================

Step 1: Open the authorization URL below in your browser.

        First time only, you'll see "Google hasn't verified this app"
        → click Advanced → Continue.

Step 2: After consent, your browser tries to redirect to
        http://localhost:{PORT}/?code=...  and shows "site can't be
        reached". That's expected.

Step 3: Copy the FULL failed URL from your browser's address bar
        (the entire thing, including code= and state=).

Step 4: In a second terminal, run:

            docker exec {container} curl -s '<paste the URL here>'

        That delivers the code to this command, which will then
        complete and exit.

------------------------------------------------------------
"""
    )
    sys.stdout.flush()

    try:
        credentials = flow.run_local_server(open_browser=False, port=PORT)
    except OSError as e:
        if getattr(e, "errno", None) == 98:
            print(
                f"\nPort {PORT} is already in use inside the container.\n"
                "Either another auth flow is running, or override the port:\n"
                "    EVERSTONE_GCAL_OAUTH_PORT=8082 everstone auth gcal",
                file=sys.stderr,
            )
            return 1
        raise

    CONFIG_FOLDER.mkdir(parents=True, exist_ok=True)
    oauth_path = CONFIG_FOLDER / "oauth"
    with oauth_path.open("wb") as f:
        pickle.dump(credentials, f)
    oauth_path.chmod(0o600)

    print(f"\n✓ Auth complete. Credentials saved to {oauth_path}")
    print("You can now use the agent's calendar tools, or test with:")
    print("    docker exec everstone gcal list")
    return 0


if __name__ == "__main__":
    sys.exit(main())
