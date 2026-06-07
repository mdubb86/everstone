import importlib.util
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("assert_telegram", ROOT / "scripts" / "assert_telegram.py")
at = importlib.util.module_from_spec(spec); spec.loader.exec_module(at)

import pytest


class FakeHermes:
    """Stand-in for `hermes -p everstone config get/set`."""
    def __init__(self, current):
        self.current = dict(current)      # key -> value already in the Hermes config
        self.sets = []                    # records (key, value) writes

    def get(self, key):
        return self.current.get(key, "")

    def set(self, key, value):
        self.sets.append((key, value))
        self.current[key] = value


def test_asserts_values_when_absent():
    h = FakeHermes(current={})
    at.assert_telegram(token="TKN", allowed="111", hermes=h)
    assert ("TELEGRAM_BOT_TOKEN", "TKN") in h.sets
    assert ("TELEGRAM_ALLOWED_USERS", "111") in h.sets


def test_idempotent_when_matching():
    h = FakeHermes(current={"TELEGRAM_BOT_TOKEN": "TKN", "TELEGRAM_ALLOWED_USERS": "111"})
    at.assert_telegram(token="TKN", allowed="111", hermes=h)
    # already correct -> no exception is the check
    # enforcement-every-boot invariant: set() must still fire for both keys
    assert h.sets == [("TELEGRAM_BOT_TOKEN", "TKN"), ("TELEGRAM_ALLOWED_USERS", "111")]


def test_loud_fail_on_token_discrepancy():
    h = FakeHermes(current={"TELEGRAM_BOT_TOKEN": "OTHER", "TELEGRAM_ALLOWED_USERS": "111"})
    with pytest.raises(at.TelegramDrift) as e:
        at.assert_telegram(token="TKN", allowed="111", hermes=h)
    assert "TELEGRAM_BOT_TOKEN" in str(e.value)


def test_loud_fail_on_allowlist_discrepancy():
    h = FakeHermes(current={"TELEGRAM_BOT_TOKEN": "TKN", "TELEGRAM_ALLOWED_USERS": "111,999"})
    with pytest.raises(at.TelegramDrift) as e:
        at.assert_telegram(token="TKN", allowed="111", hermes=h)
    assert "TELEGRAM_ALLOWED_USERS" in str(e.value)


def test_loud_fail_lists_both_keys_when_both_drift():
    h = FakeHermes(current={"TELEGRAM_BOT_TOKEN": "WRONG_TOKEN", "TELEGRAM_ALLOWED_USERS": "999"})
    with pytest.raises(at.TelegramDrift) as e:
        at.assert_telegram(token="TKN", allowed="111", hermes=h)
    msg = str(e.value)
    assert "TELEGRAM_BOT_TOKEN" in msg
    assert "TELEGRAM_ALLOWED_USERS" in msg
