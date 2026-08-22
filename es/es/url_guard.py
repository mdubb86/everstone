"""Refuse to fetch internal addresses.

es_web_fetch returns response bodies to the agent. Before text passthrough that
was inert for internal services — non-HTML bodies were discarded — but once any
body can come back, an unguarded fetch is a read primitive for everything
listening inside the container (Radicale, the OAuth helper, /version, CouchDB).

The check resolves the host and inspects the ADDRESSES, not the string: a
public-looking name whose A record is 127.0.0.1 must be blocked too. Fails
closed — a host that will not resolve is refused rather than attempted.
"""
import ipaddress
import socket
from typing import List
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}


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
    return (ip.is_loopback or ip.is_link_local or ip.is_private
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


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
