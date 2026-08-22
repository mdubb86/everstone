"""Refuse to fetch internal addresses.

es_web_fetch returns response bodies to the agent. Before text passthrough that
was inert for internal services — non-HTML bodies were discarded — but once any
body can come back, an unguarded fetch is a read primitive for everything
listening inside the container (Radicale, the OAuth helper, /version, CouchDB).

The check resolves the host and inspects the ADDRESSES, not the string: a
public-looking name whose A record is 127.0.0.1 must be blocked too. Fails
closed — a host that will not resolve is refused rather than attempted.

Blocked ranges are an explicit list, not `ipaddress`'s `is_private`/`is_reserved`
predicates. Those predicates are broader than "internal network": they also
cover documentation ranges (192.0.2.0/24 TEST-NET-1, 198.51.100.0/24, 203.0.113.0/24)
and the benchmark range (198.18.0.0/15), where nothing listens and blocking buys
no security. That distinction is not academic here: the dev environment's
resolver (both the sandbox and the devm VM's transparent-egress DNS) hands back
192.0.2.1 for EVERY public hostname — using `is_private` made the guard block
every real fetch in dev while doing nothing extra for prod. Enumerating the
actual internal ranges avoids that trap.

TOCTOU / DNS-rebinding note: `check_url` resolves and validates the host at
check time. Whatever performs the actual HTTP request re-resolves the same
hostname independently at connect time. A host with a short TTL could answer
the first lookup with a public address and the second with 127.0.0.1 (or an
RFC1918 address) — this guard narrows the SSRF surface but does not close that
race. Closing it fully needs the fetch path itself to connect to the specific
address this check validated (e.g. resolve once, connect to the literal IP,
set the Host header), not just calling `check_url` beforehand.
"""
import ipaddress
import socket
from typing import List
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}

# Networks we actually mean by "internal". Deliberately NOT `is_private`/
# `is_reserved` — see module docstring for why (dev's resolver returns
# TEST-NET-1 for every public hostname; those predicates would swallow it).
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),        # RFC1918
    ipaddress.ip_network("172.16.0.0/12"),      # RFC1918
    ipaddress.ip_network("192.168.0.0/16"),     # RFC1918
    ipaddress.ip_network("127.0.0.0/8"),        # loopback (IPv4)
    ipaddress.ip_network("::1/128"),            # loopback (IPv6)
    ipaddress.ip_network("169.254.0.0/16"),     # link-local, incl. cloud metadata
    ipaddress.ip_network("fe80::/10"),          # link-local (IPv6)
    ipaddress.ip_network("fc00::/7"),           # unique-local (IPv6)
    ipaddress.ip_network("0.0.0.0/8"),          # "this network" / unspecified
]


class BlockedAddress(Exception):
    es_code = "url_blocked"


def _resolve(host: str) -> List[str]:
    """Every address the host resolves to. Seam for tests."""
    infos = socket.getaddrinfo(host, None)
    return [info[4][0] for info in infos]


def _is_internal(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True   # unparseable → fail closed
    if ip.is_multicast or ip.is_unspecified:
        return True
    return any(ip in net for net in _BLOCKED_NETWORKS)


def check_url(url: str) -> None:
    """Raise BlockedAddress if `url` is not a public http(s) target."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise BlockedAddress(
            f"cannot fetch {parsed.scheme or 'that'} URLs — only http and https")
    host = parsed.hostname
    if not host:
        raise BlockedAddress(f"no host in URL: {url!r}")
    try:
        addrs = _resolve(host)
    except OSError as e:
        raise BlockedAddress(f"could not resolve {host!r}") from e
    if any(_is_internal(a) for a in addrs):
        raise BlockedAddress(
            f"{host!r} is an internal address — es_web_fetch only reaches "
            "public sites; tell the user this address isn't reachable")
