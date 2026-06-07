"""Shared Google credential consumer for es. Reads the JSON credential store,
refreshes if expired, builds API services. The OAuth flow is operator-run
(scripts/auth_gcal.py); es only consumes."""
import os
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

# Union of scopes for all enabled Google capabilities. Append here when adding
# a new Google capability (e.g. gmail.readonly for es mail) — then re-consent.
GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar"]

_DEFAULT_CREDS_PATH = "/opt/data/hermes/es/google-credentials.json"


def _creds_path() -> Path:
    return Path(os.environ.get("ES_GOOGLE_CREDS_PATH", _DEFAULT_CREDS_PATH))


def load_credentials():
    path = _creds_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"es: Google not authorized (no creds at {path}). "
            f"Operator: run `esadmin auth google`."
        )
    creds = Credentials.from_authorized_user_file(str(path), GOOGLE_SCOPES)
    if not creds.valid:
        creds.refresh(Request())
    return creds


def calendar_service():
    return build("calendar", "v3", credentials=load_credentials(), cache_discovery=False)
