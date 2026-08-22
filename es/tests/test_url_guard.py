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


@pytest.mark.parametrize("addr", ["192.0.2.1", "198.51.100.7", "203.0.113.42"])
def test_allows_test_net_documentation_addresses(addr, monkeypatch):
    """Regression test: this repo's dev environment (sandbox + the devm VM's
    transparent-egress DNS) resolves EVERY public hostname to 192.0.2.1
    (TEST-NET-1). `ipaddress.ip_address(...).is_private` is True for the whole
    192.0.2.0/24, 198.51.100.0/24 and 203.0.113.0/24 documentation ranges, so a
    guard built on `is_private` blocks all real fetching in dev while gaining
    nothing in prod — nothing listens on documentation space. Must be allowed."""
    monkeypatch.setattr(url_guard, "_resolve", lambda host: [addr])
    url_guard.check_url("https://example.com/a")   # must not raise


def test_allows_benchmark_range(monkeypatch):
    """198.18.0.0/15 is the RFC 2544 benchmark range — also caught by
    `is_private` but not actually internal to anything. Must be allowed."""
    monkeypatch.setattr(url_guard, "_resolve", lambda host: ["198.18.0.1"])
    url_guard.check_url("https://example.com/a")   # must not raise
