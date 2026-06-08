from unittest.mock import MagicMock, patch
from es import google_auth


def test_load_credentials_reads_json_and_refreshes(tmp_path, monkeypatch):
    p = tmp_path / "google-credentials.json"
    p.write_text("{}")
    monkeypatch.setenv("ES_GOOGLE_CREDS_PATH", str(p))
    creds = MagicMock(); creds.valid = False
    with patch("es.google_auth.Credentials.from_authorized_user_file", return_value=creds), \
         patch("es.google_auth.Request"):
        out = google_auth.load_credentials()
    creds.refresh.assert_called_once()
    assert out is creds


def test_load_credentials_no_refresh_when_valid(tmp_path, monkeypatch):
    p = tmp_path / "google-credentials.json"
    p.write_text("{}")
    monkeypatch.setenv("ES_GOOGLE_CREDS_PATH", str(p))
    creds = MagicMock(); creds.valid = True
    with patch("es.google_auth.Credentials.from_authorized_user_file", return_value=creds):
        google_auth.load_credentials()
    creds.refresh.assert_not_called()


def test_missing_creds_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("ES_GOOGLE_CREDS_PATH", str(tmp_path / "nope.json"))
    import pytest
    with pytest.raises(FileNotFoundError):
        google_auth.load_credentials()


def test_scopes_is_calendar_only_for_now():
    assert google_auth.GOOGLE_SCOPES == ["https://www.googleapis.com/auth/calendar"]
