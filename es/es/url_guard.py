"""Refuse to fetch internal addresses.

es_web_fetch returns response bodies to the agent. Before text passthrough that
was inert for internal services — non-HTML bodies were discarded — but once any
body can come back, an unguarded fetch is a read primitive for everything
listening inside the container (Radicale, the OAuth helper, /version, CouchDB)
and, if the host is on a tailnet or home LAN, everything reachable from it.

The check resolves the host and inspects the ADDRESSES, not the string: a
public-looking name whose A record is 127.0.0.1 must be blocked too. Fails
closed — a host that will not resolve, that resolves to nothing, or whose
address can't be parsed is refused rather than attempted.

Blocked ranges are an explicit list, not `ipaddress`'s `is_private`/`is_reserved`
predicates. Those predicates are broader than "internal network": they also
cover documentation ranges (192.0.2.0/24 TEST-NET-1, 198.51.100.0/24, 203.0.113.0/24)
and the benchmark range (198.18.0.0/15), where nothing listens and blocking buys
no security. That distinction is not academic here: the dev environment's
resolver (both the sandbox and the devm VM's transparent-egress DNS) hands back
192.0.2.1 for EVERY public hostname — using `is_private` made the guard block
every real fetch in dev while doing nothing extra for prod. Enumerating the
actual internal ranges avoids that trap.

IPv4-mapped/translated IPv6 addresses are normalized to their embedded IPv4
address BEFORE matching. `::ffff:127.0.0.1` (IPv4-mapped), `2002:7f00:1::1`
(6to4) and Teredo addresses all encode an IPv4 address inside an IPv6 literal;
checking the IPv6 form against IPv4 ranges (or vice versa) silently passes
them through — a verified live bypass of an earlier version of this guard,
which let `http://[::ffff:127.0.0.1]/...` reach a loopback listener that the
equivalent `http://127.0.0.1/...` correctly blocked. NAT64 (64:ff9b::/96) has
no unmapping helper in the stdlib, so it's listed directly in
`_BLOCKED_NETWORKS` instead.

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
from typing import List, Union
from urllib.parse import urlparse

ALLOWED_SCHEMES = {"http", "https"}

# Networks we actually mean by "internal". Deliberately NOT `is_private`/
# `is_reserved` — see module docstring for why (dev's resolver returns
# TEST-NET-1 for every public hostname; those predicates would swallow it).
_BLOCKED_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),        # RFC1918
    ipaddress.ip_network("172.16.0.0/12"),      # RFC1918
    ipaddress.ip_network("192.168.0.0/16"),     # RFC1918
    ipaddress.ip_network("100.64.0.0/10"),      # CGNAT (also Tailscale's range)
    ipaddress.ip_network("127.0.0.0/8"),        # loopback (IPv4)
    ipaddress.ip_network("::1/128"),            # loopback (IPv6)
    ipaddress.ip_network("169.254.0.0/16"),     # link-local, incl. cloud metadata
    ipaddress.ip_network("fe80::/10"),          # link-local (IPv6)
    ipaddress.ip_network("fc00::/7"),           # unique-local (IPv6)
    ipaddress.ip_network("fec0::/10"),          # deprecated site-local (IPv6)
    ipaddress.ip_network("0.0.0.0/8"),          # "this network" / unspecified
    ipaddress.ip_network("192.0.0.0/24"),       # IETF protocol assignments
    ipaddress.ip_network("240.0.0.0/4"),        # reserved for future use
    ipaddress.ip_network("255.255.255.255/32"),  # limited broadcast
    ipaddress.ip_network("2002::/16"),          # 6to4 (also normalized directly)
    ipaddress.ip_network("64:ff9b::/96"),       # NAT64 — no stdlib unmapping helper
]


class BlockedAddress(Exception):
    es_code = "url_blocked"


def _resolve(host: str) -> List[str]:
    """Every address the host resolves to. Seam for tests."""
    infos = socket.getaddrinfo(host, None)
    return [info[4][0] for info in infos]


def _unmap(ip: Union[ipaddress.IPv4Address, ipaddress.IPv6Address]):
    """Return the IPv4 address embedded in an IPv4-mapped/6to4/Teredo IPv6
    literal, or `ip` unchanged if it isn't one of those encodings.

    Matching the IPv6 wrapper against the IPv4 ranges in _BLOCKED_NETWORKS
    (or vice versa) never matches — `in` on mismatched versions is just
    False — so an address like `::ffff:127.0.0.1` sailed through unless it's
    unwrapped first. NAT64 (64:ff9b::/96) has no stdlib accessor for this, so
    it's blocked as a literal IPv6 network instead of being unmapped here.
    """
    if isinstance(ip, ipaddress.IPv6Address):
        return ip.ipv4_mapped or ip.sixtofour or (ip.teredo[1] if ip.teredo else ip)
    return ip


def _is_internal(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return True   # unparseable → fail closed
    ip = _unmap(ip)
    if ip.is_multicast or ip.is_unspecified:
        return True
    return any(ip in net for net in _BLOCKED_NETWORKS)


def check_url(url: str) -> None:
    """Raise BlockedAddress if `url` is not a public http(s) target."""
    if not isinstance(url, str):
        raise BlockedAddress(f"not a URL: {url!r}")
    try:
        parsed = urlparse(url)
        host = parsed.hostname
    except (ValueError, UnicodeError) as e:
        raise BlockedAddress(f"could not parse URL: {url!r}") from e
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise BlockedAddress(
            f"cannot fetch {parsed.scheme or 'that'} URLs — only http and https")
    if not host:
        raise BlockedAddress(f"no host in URL: {url!r}")
    try:
        addrs = _resolve(host)
    except (OSError, UnicodeError) as e:
        raise BlockedAddress(f"could not resolve {host!r}") from e
    if not addrs:
        # Real getaddrinfo raises rather than returning empty, so this is
        # unreached today — but "fails closed" is this module's whole
        # contract, so an empty result must not read as "nothing internal".
        raise BlockedAddress(f"{host!r} did not resolve to any address")
    if any(_is_internal(a) for a in addrs):
        raise BlockedAddress(
            f"{host!r} is an internal address — es_web_fetch only reaches "
            "public sites; tell the user this address isn't reachable")
