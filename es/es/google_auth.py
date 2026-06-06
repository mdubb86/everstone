"""Shared Google credential consumer for es. Loads the stored OAuth
credential, refreshes if expired, builds API service objects. The OAuth flow
is operator-run elsewhere; es only consumes."""
import os
import pickle
from pathlib import Path

from google.auth.transport.requests import Request
from googleapiclient.discovery import build

# Current store is gcalcli's pickle file; Plan 3 relocates it. Override for tests.
_DEFAULT_CREDS_PATH = "/opt/data/hermes/gcalcli/oauth"


def _creds_path() -> Path:
    return Path(os.environ.get("ES_GOOGLE_CREDS_PATH", _DEFAULT_CREDS_PATH))


def load_credentials():
    path = _creds_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"es: Google not authorized (no creds at {path}). "
            f"Run the operator auth flow first."
        )
    with open(path, "rb") as fh:
        creds = pickle.load(fh)
    if not creds.valid:
        creds.refresh(Request())
    return creds


def calendar_service():
    return build("calendar", "v3", credentials=load_credentials(),
                 cache_discovery=False)
