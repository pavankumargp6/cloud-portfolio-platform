"""Gate 5: send the HTTP probe.

Revalidates every redirect hop through `resolve_and_validate`. Enforces
TLS verification, body cap, connect/read timeouts. Returns ProbeOutcome.

Raises SSRFError if a redirect target fails SSRF. The worker catches
SSRFError and records the result as status='blocked_ssrf'.

Test-only switches (keyword-only, prefixed with `_test_`):
  _test_bypass_initial_ssrf : skip SSRF for the initial URL and any
                              same-origin redirect, so integration tests
                              can target a local 127.0.0.1 server.
                              Cross-origin redirects still go through SSRF.
  _test_ssl_ca              : path to a CA bundle to trust (replaces the
                              system trust store for this probe).
Production code MUST NOT set these.
"""

import socket
import ssl
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse

from app.monitoring.errors import SSRFError
from app.monitoring.ssrf import ProbeTarget, resolve_and_validate

DEFAULT_TIMEOUT = 10.0
BODY_CAP = 65536
MAX_REDIRECTS = 3
USER_AGENT = (
    "AgencyReliabilityDesk/0.1 "
    "(+https://github.com/pavankumargp6/cloud-portfolio-platform)"
)


@dataclass
class ProbeOutcome:
    status: str
    http_status: Optional[int] = None
    latency_ms: Optional[int] = None
    failure_reason: Optional[str] = None
    ssl_expires_at: Optional[datetime] = None
    ssl_issuer: Optional[str] = None
    final_url: Optional[str] = None
    redirect_count: int = 0
    body_bytes: int = 0


def _origin(url: str):
    p = urlparse(url)
    port = p.port or (443 if p.scheme == "https" else 80)
    return (p.scheme, p.hostname, port)


def _build_test_target(url: str) -> ProbeTarget:
    """Test-only. Build a ProbeTarget without SSRF validation."""
    p = urlparse(url)
    if not p.hostname:
        raise SSRFError("dns_failure", "no hostname in URL", url)
    port = p.port or (443 if p.scheme == "https" else 80)
    path = p.path or "/"
    if p.query:
        path = f"{path}?{p.query}"
    try:
        ip = socket.gethostbyname(p.hostname)
    except socket.gaierror:
        ip = p.hostname
    return ProbeTarget(
        ip=ip,
        port=port,
        hostname=p.hostname,
        scheme=p.scheme,
        path=path,
        original_url=url,
    )


def _parse_headers(head: bytes):
    text = head.decode("latin-1", errors="replace")
    lines = text.split("\r\n")
    if not lines or not lines[0].strip():
        raise ValueError("empty status line")
    parts = lines[0].split()
    if len(parts) < 2:
        raise ValueError(f"bad status line: {lines[0]!r}")
    status_code = int(parts[1])
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return status_code, headers


def _do_one_request(target: ProbeTarget, timeout: float, body_cap: int, ssl_ca):
    ssl_info = {}

    raw_sock = socket.create_connection((target.ip, target.port), timeout=timeout)
    try:
        raw_sock.settimeout(timeout)
        if target.scheme == "https":
            ctx = ssl.create_default_context()
            if ssl_ca:
                ctx.load_verify_locations(cafile=ssl_ca)
            sock = ctx.wrap_socket(raw_sock, server_hostname=target.hostname)
            raw_sock = sock
            cert = sock.getpeercert() or {}
            try:
                if "notAfter" in cert:
                    exp = datetime.strptime(
                        cert["notAfter"], "%b %d %H:%M:%S %Y %Z"
                    ).replace(tzinfo=timezone.utc)
                    ssl_info["expires_at"] = exp
            except (KeyError, ValueError):
                pass
            issuer_parts = []
            for rdn in cert.get("issuer", []):
                for k, v in rdn:
                    issuer_parts.append(f"{k}={v}")
            if issuer_parts:
                ssl_info["issuer"] = ", ".join(issuer_parts)[:255]
        else:
            sock = raw_sock

        host_header = target.hostname
        default_port = 443 if target.scheme == "https" else 80
        if target.port != default_port:
            host_header = f"{host_header}:{target.port}"

        req = (
            f"GET {target.path} HTTP/1.1\r\n"
            f"Host: {host_header}\r\n"
            f"User-Agent: {USER_AGENT}\r\n"
            f"Accept: */*\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        )
        sock.sendall(req.encode("latin-1"))

        buf = b""
        headers_done = False
        status_code = None
        headers = {}
        body_len = 0

        while True:
            chunk = sock.recv(8192)
            if not chunk:
                break
            if not headers_done:
                buf += chunk
                sep = b"\r\n\r\n"
                if sep in buf:
                    head, _, remainder = buf.partition(sep)
                    status_code, headers = _parse_headers(head)
                    headers_done = True
                    body_len = len(remainder)
                    if body_len >= body_cap:
                        body_len = body_cap
                        break
                    buf = b""
                elif len(buf) > 16 * 1024:
                    raise ValueError("response headers too large")
            else:
                body_len += len(chunk)
                if body_len >= body_cap:
                    body_len = body_cap
                    break

        if not headers_done:
            raise ValueError("connection closed before headers")
        return status_code, headers, min(body_len, body_cap), ssl_info
    finally:
        try:
            raw_sock.close()
        except Exception:
            pass


def probe(
    url: str,
    *,
    resolver=None,
    timeout: float = DEFAULT_TIMEOUT,
    body_cap: int = BODY_CAP,
    max_redirects: int = MAX_REDIRECTS,
    _test_bypass_initial_ssrf: bool = False,
    _test_ssl_ca: Optional[str] = None,
) -> ProbeOutcome:
    started = time.monotonic()
    current_url = url
    redirect_count = 0
    initial_origin = _origin(url) if _test_bypass_initial_ssrf else None

    while True:
        cur_origin = _origin(current_url)
        if _test_bypass_initial_ssrf and cur_origin == initial_origin:
            target = _build_test_target(current_url)
        else:
            target = resolve_and_validate(current_url, resolver=resolver)

        try:
            status_code, headers, body_len, ssl_info = _do_one_request(
                target, timeout=timeout, body_cap=body_cap, ssl_ca=_test_ssl_ca
            )
        except socket.timeout:
            return ProbeOutcome(
                status="timeout",
                failure_reason="timeout",
                latency_ms=int((time.monotonic() - started) * 1000),
                final_url=current_url,
                redirect_count=redirect_count,
            )
        except ssl.SSLError:
            return ProbeOutcome(
                status="tls_error",
                failure_reason="tls_verify_failed",
                latency_ms=int((time.monotonic() - started) * 1000),
                final_url=current_url,
                redirect_count=redirect_count,
            )
        except (ConnectionRefusedError, ConnectionResetError, OSError):
            return ProbeOutcome(
                status="down",
                failure_reason="connect_refused",
                latency_ms=int((time.monotonic() - started) * 1000),
                final_url=current_url,
                redirect_count=redirect_count,
            )
        except ValueError:
            return ProbeOutcome(
                status="down",
                failure_reason="bad_response",
                latency_ms=int((time.monotonic() - started) * 1000),
                final_url=current_url,
                redirect_count=redirect_count,
            )

        if 300 <= status_code < 400 and "location" in headers:
            if redirect_count >= max_redirects:
                return ProbeOutcome(
                    status="down",
                    failure_reason="too_many_redirects",
                    http_status=status_code,
                    latency_ms=int((time.monotonic() - started) * 1000),
                    final_url=current_url,
                    redirect_count=redirect_count,
                )
            current_url = urljoin(current_url, headers["location"])
            redirect_count += 1
            continue

        latency = int((time.monotonic() - started) * 1000)
        if 500 <= status_code < 600:
            return ProbeOutcome(
                status="down",
                http_status=status_code,
                latency_ms=latency,
                failure_reason=f"http_{status_code}",
                ssl_expires_at=ssl_info.get("expires_at"),
                ssl_issuer=ssl_info.get("issuer"),
                final_url=current_url,
                redirect_count=redirect_count,
                body_bytes=body_len,
            )

        return ProbeOutcome(
            status="up",
            http_status=status_code,
            latency_ms=latency,
            ssl_expires_at=ssl_info.get("expires_at"),
            ssl_issuer=ssl_info.get("issuer"),
            final_url=current_url,
            redirect_count=redirect_count,
            body_bytes=body_len,
        )
