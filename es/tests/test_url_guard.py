import pytest

from es import url_guard


@pytest.mark.parametrize("url", [
    "http://localhost/x",
    "http://127.0.0.1:5984/_all_dbs",
    "http://127.1.2.3/x",
    "https://[::1]/x",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/x",
    "http://192.168.1.1/x",
    "http://172.16.0.1/x",
    "http://0.0.0.0/x",
])
def test_rejects_internal_targets(url, monkeypatch):
    monkeypatch.setattr(url_guard, "_resolve", lambda host: [host])
    with pytest.raises(url_guard.BlockedAddress):
        url_guard.check_url(url)


def test_allows_public_address(monkeypatch):
    monkeypatch.setattr(url_guard, "_resolve", lambda host: ["93.184.216.34"])
    url_guard.check_url("https://example.com/a")   # must not raise


def test_rejects_hostname_that_resolves_to_loopback(monkeypatch):
    """A public-looking name pointing at 127.0.0.1 must be blocked — checking
    the literal string is not enough."""
    monkeypatch.setattr(url_guard, "_resolve", lambda host: ["127.0.0.1"])
    with pytest.raises(url_guard.BlockedAddress):
        url_guard.check_url("https://evil.example.com/a")


def test_rejects_when_any_resolved_address_is_internal(monkeypatch):
    """Multi-homed name: one public A record and one loopback. Fail closed."""
    monkeypatch.setattr(url_guard, "_resolve", lambda host: ["93.184.216.34", "127.0.0.1"])
    with pytest.raises(url_guard.BlockedAddress):
        url_guard.check_url("https://mixed.example.com/a")


def test_rejects_unresolvable_host(monkeypatch):
    def boom(host):
        raise OSError("nope")
    monkeypatch.setattr(url_guard, "_resolve", boom)
    with pytest.raises(url_guard.BlockedAddress):
        url_guard.check_url("https://nx.example.com/a")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x/y", "gopher://x/y", "not-a-url"])
def test_rejects_non_http_schemes(url):
    with pytest.raises(url_guard.BlockedAddress):
        url_guard.check_url(url)


def test_error_message_says_internal_without_leaking_the_ip(monkeypatch):
    monkeypatch.setattr(url_guard, "_resolve", lambda host: ["127.0.0.1"])
    with pytest.raises(url_guard.BlockedAddress) as e:
        url_guard.check_url("http://localhost:5984/_all_dbs")
    assert "internal" in str(e.value).lower()
