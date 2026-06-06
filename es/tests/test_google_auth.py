from unittest.mock import MagicMock, patch
from es import google_auth


def test_load_credentials_refreshes_when_expired(tmp_path, monkeypatch):
    creds = MagicMock()
    creds.valid = False
    monkeypatch.setenv("ES_GOOGLE_CREDS_PATH", str(tmp_path / "oauth"))
    (tmp_path / "oauth").write_bytes(b"x")
    with patch("es.google_auth.pickle.load", return_value=creds), \
         patch("es.google_auth.Request"):
        out = google_auth.load_credentials()
    creds.refresh.assert_called_once()
    assert out is creds


def test_load_credentials_no_refresh_when_valid(tmp_path, monkeypatch):
    creds = MagicMock()
    creds.valid = True
    monkeypatch.setenv("ES_GOOGLE_CREDS_PATH", str(tmp_path / "oauth"))
    (tmp_path / "oauth").write_bytes(b"x")
    with patch("es.google_auth.pickle.load", return_value=creds):
        google_auth.load_credentials()
    creds.refresh.assert_not_called()


def test_missing_creds_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("ES_GOOGLE_CREDS_PATH", str(tmp_path / "nope"))
    import pytest
    with pytest.raises(FileNotFoundError):
        google_auth.load_credentials()
