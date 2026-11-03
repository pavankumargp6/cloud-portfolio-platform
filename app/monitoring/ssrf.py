"""SSRF Gates 1-4: URL syntax, DNS resolution, IP classification.

Gate 5 (redirect revalidation) lives in `app.monitoring.probe`; it calls
back into `resolve_and_validate` on every redirect hop.

The contract is: `resolve_and_validate(url)` either returns a ProbeTarget
that is safe to connect to, or raises SSRFError with a `.code`.
"""

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import urlparse

from app.monitoring.errors import SSRFError
from app.monitoring.resolver import default_resolver

ALLOWED_SCHEMES = frozenset({"http", "https"})
ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
DEFAULT_PORT_BY_SCHEME = {"http": 80, "https": 443}
CGNAT = ipaddress.IPv4Network("100.64.0.0/10")
THIS_NETWORK = ipaddress.IPv4Network("0.0.0.0/8")


@dataclass
class ProbeTarget:
    ip: str
    port: int
    hostname: str
    scheme: str
    path: str
    original_url: str


def _try_parse_ip_literal(hostname: str):
    """Return an ipaddress object if hostname is an IP in any form, else None.

    Handles standard dotted-quad, IPv6, and legacy IPv4 forms (decimal,
    hex, short) that `socket.inet_aton` accepts on Linux.
    """
    if not hostname:
        return None
    try:
        return ipaddress.ip_address(hostname)
    except ValueError:
        pass
    try:
        packed = socket.inet_aton(hostname)
        return ipaddress.ip_address(socket.inet_ntoa(packed))
    except (OSError, ValueError):
        return None


def _is_blocked_ip(ip_obj) -> bool:
    """True if the address is loopback/private/reserved/etc."""
    if isinstance(ip_obj, ipaddress.IPv6Address) and ip_obj.ipv4_mapped is not None:
        ip_obj = ip_obj.ipv4_mapped

    if ip_obj.is_loopback or ip_obj.is_link_local:
        return True
    if ip_obj.is_private or ip_obj.is_multicast:
        return True
    if ip_obj.is_reserved or ip_obj.is_unspecified:
        return True

    if isinstance(ip_obj, ipaddress.IPv4Address):
        if ip_obj in CGNAT:
            return True
        if ip_obj in THIS_NETWORK:
            return True

    return False


def resolve_and_validate(url: str, resolver=None) -> ProbeTarget:
    """Apply Gates 1-4 to `url`. Return ProbeTarget or raise SSRFError."""
    if resolver is None:
        resolver = default_resolver

    parsed = urlparse(url)

    # Gate 1a — scheme
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise SSRFError(
            "blocked_scheme", f"scheme {parsed.scheme!r} not allowed", url
        )

    # Gate 1b — credentials
    if parsed.username is not None or parsed.password is not None:
        raise SSRFError("blocked_credentials", "URL contains credentials", url)

    # Gate 1c — port
    try:
        port = parsed.port
    except ValueError:
        raise SSRFError("blocked_port", "invalid port", url)
    if port is None:
        port = DEFAULT_PORT_BY_SCHEME[parsed.scheme]
    if port not in ALLOWED_PORTS:
        raise SSRFError("blocked_port", f"port {port} not allowed", url)

    hostname = parsed.hostname
    if not hostname:
        raise SSRFError("dns_failure", "URL has no hostname", url)

    # Gates 2-3 — resolve, or short-circuit for IP literals (Gates 1a-c
    # still apply to IP hosts; skipping resolution is required so that
    # `http://127.1/` does not hit the DNS server at all).
    direct = _try_parse_ip_literal(hostname)
    if direct is not None:
        if _is_blocked_ip(direct):
            raise SSRFError("blocked_ip", f"IP {direct} not allowed", url)
        chosen_ip = str(direct)
    else:
        try:
            ips = resolver(hostname)
        except (socket.gaierror, socket.timeout, OSError) as exc:
            raise SSRFError("dns_failure", f"DNS failure: {exc}", url) from exc
        except Exception as exc:  # noqa: BLE001 — resolver is caller-supplied
            raise SSRFError("dns_failure", f"DNS failure: {exc}", url) from exc

        if not ips:
            raise SSRFError("dns_failure", f"no IPs resolved for {hostname}", url)

        chosen_ip = None
        for ip_str in ips:
            try:
                ip_obj = ipaddress.ip_address(ip_str)
            except ValueError as exc:
                raise SSRFError("blocked_ip", f"unparseable IP {ip_str!r}", url) from exc
            if _is_blocked_ip(ip_obj):
                raise SSRFError("blocked_ip", f"IP {ip_obj} not allowed", url)
            if chosen_ip is None:
                chosen_ip = ip_str

        if chosen_ip is None:
            raise SSRFError("dns_failure", f"no usable IPs for {hostname}", url)

    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"

    return ProbeTarget(
        ip=chosen_ip,
        port=port,
        hostname=hostname,
        scheme=parsed.scheme,
        path=path,
        original_url=url,
    )
