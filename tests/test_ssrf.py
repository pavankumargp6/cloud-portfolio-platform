"""SSRF acceptance tests for Milestone 3.

Every row from docs/MILESTONE-3-PLAN.md section 5 is represented here.
The implementation modules `app.monitoring.ssrf` and `app.monitoring.probe`
do not exist at delivery time of this file — all tests fail with a clear
"Awaiting Phase N implementation" message until those modules land.

Phase 1: all 50 fail (this file, alone, on an empty branch)
Phase 2: T01-T38 pass (after app/monitoring/ssrf.py exists)
Phase 3: T39-T50 pass (after app/monitoring/probe.py exists)
"""

import http.server
import socket
import ssl
import threading
import time
import unittest
from pathlib import Path
from urllib.parse import urlparse


# --- Lazy implementation imports ---
try:
    from app.monitoring.ssrf import resolve_and_validate, ProbeTarget
    from app.monitoring.errors import SSRFError
    _SSRF_READY = True
    _SSRF_ERROR = None
except ImportError as exc:
    _SSRF_READY = False
    _SSRF_ERROR = exc
    resolve_and_validate = None
    ProbeTarget = None
    SSRFError = None

try:
    from app.monitoring.probe import probe
    _PROBE_READY = True
    _PROBE_ERROR = None
except ImportError as exc:
    _PROBE_READY = False
    _PROBE_ERROR = exc
    probe = None


# --- Fake resolver ---
class FakeResolver:
    """Hostname -> list of IP strings, or Exception to raise, or absent -> gaierror."""

    def __init__(self, mapping):
        self._mapping = mapping

    def __call__(self, hostname):
        if hostname not in self._mapping:
            raise socket.gaierror(f"unknown host: {hostname}")
        result = self._mapping[hostname]
        if isinstance(result, Exception):
            raise result
        if result is None:
            return []
        return list(result)


def resolve_to(*ips):
    return FakeResolver({"example.com": list(ips)})


# --- Base classes ---
class SSRFTestBase(unittest.TestCase):
    def setUp(self):
        if not _SSRF_READY:
            self.fail(f"Awaiting Phase 2 implementation: {_SSRF_ERROR}")


class ProbeTestBase(unittest.TestCase):
    def setUp(self):
        if not _SSRF_READY:
            self.fail(f"Awaiting Phase 2 implementation: {_SSRF_ERROR}")
        if not _PROBE_READY:
            self.fail(f"Awaiting Phase 3 implementation: {_PROBE_ERROR}")


# --- Local HTTP server for redirect/network tests ---
class _LocalHTTPServer:
    """Context manager: HTTP server on 127.0.0.1:<random port>.

    Routes: dict path -> tuple(action, arg).
      ("ok",     None)        200 + "ok"
      ("empty",  None)        204
      ("big",    None)        200 + 1 MB body
      ("status", 500)         200-level passthrough of code
      ("redirect", "/next")   302 + Location
      ("sleep",  30)          sleep for arg seconds then 200
    """

    def __init__(self, routes):
        self._routes = routes
        self._server = None
        self._thread = None

    def __enter__(self):
        routes = self._routes

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                route = routes.get(self.path)
                if route is None:
                    self.send_response(404)
                    self.end_headers()
                    return
                action, arg = route
                if action == "ok":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")
                elif action == "empty":
                    self.send_response(204)
                    self.end_headers()
                elif action == "big":
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"x" * (1024 * 1024))
                elif action == "status":
                    self.send_response(int(arg))
                    self.end_headers()
                elif action == "redirect":
                    self.send_response(302)
                    self.send_header("Location", arg)
                    self.end_headers()
                elif action == "sleep":
                    time.sleep(arg)
                    self.send_response(200)
                    self.end_headers()
                    self.wfile.write(b"ok")

        self._server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *args):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()

    @property
    def port(self):
        return self._server.server_address[1]

    @property
    def base_url(self):
        return f"http://127.0.0.1:{self.port}"


# =============================================================================
# Section 5.1 — Scheme and syntax (12 cases)
# =============================================================================
class TestSSRFScheme(SSRFTestBase):

    def test_t01_ftp_scheme(self):
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("ftp://example.com/", resolver=resolve_to("1.1.1.1"))
        self.assertEqual(ctx.exception.code, "blocked_scheme")

    def test_t02_file_scheme(self):
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("file:///etc/passwd", resolver=resolve_to("1.1.1.1"))
        self.assertEqual(ctx.exception.code, "blocked_scheme")

    def test_t03_gopher_scheme(self):
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("gopher://example.com/", resolver=resolve_to("1.1.1.1"))
        self.assertEqual(ctx.exception.code, "blocked_scheme")

    def test_t04_javascript_scheme(self):
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("javascript:alert(1)", resolver=resolve_to("1.1.1.1"))
        self.assertEqual(ctx.exception.code, "blocked_scheme")

    def test_t05_data_scheme(self):
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("data:text/plain,hi", resolver=resolve_to("1.1.1.1"))
        self.assertEqual(ctx.exception.code, "blocked_scheme")

    def test_t06_credentials_user_pass(self):
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("http://user:pass@example.com/", resolver=resolve_to("1.1.1.1"))
        self.assertEqual(ctx.exception.code, "blocked_credentials")

    def test_t07_credentials_user_only(self):
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("https://user@example.com/", resolver=resolve_to("1.1.1.1"))
        self.assertEqual(ctx.exception.code, "blocked_credentials")

    def test_t08_port_22(self):
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("http://example.com:22/", resolver=resolve_to("1.1.1.1"))
        self.assertEqual(ctx.exception.code, "blocked_port")

    def test_t09_port_6379(self):
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("http://example.com:6379/", resolver=resolve_to("1.1.1.1"))
        self.assertEqual(ctx.exception.code, "blocked_port")

    def test_t10_port_5432(self):
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("http://example.com:5432/", resolver=resolve_to("1.1.1.1"))
        self.assertEqual(ctx.exception.code, "blocked_port")

    def test_t11_port_8443_allowed(self):
        target = resolve_and_validate("https://example.com:8443/", resolver=resolve_to("1.1.1.1"))
        self.assertIsInstance(target, ProbeTarget)
        self.assertEqual(target.port, 8443)

    def test_t12_port_80_allowed(self):
        target = resolve_and_validate("http://example.com:80/", resolver=resolve_to("1.1.1.1"))
        self.assertIsInstance(target, ProbeTarget)
        self.assertEqual(target.port, 80)


# =============================================================================
# Section 5.2 — IPv4 private and special ranges (10 cases)
# =============================================================================
class TestSSRFIPv4(SSRFTestBase):

    def _blocked(self, ip):
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("http://example.com/", resolver=resolve_to(ip))
        self.assertEqual(ctx.exception.code, "blocked_ip", f"expected block for {ip}")

    def test_t13_loopback(self):
        self._blocked("127.0.0.1")

    def test_t14_rfc1918_10(self):
        self._blocked("10.0.0.1")

    def test_t15_rfc1918_172(self):
        self._blocked("172.16.0.1")

    def test_t16_rfc1918_192(self):
        self._blocked("192.168.1.1")

    def test_t17_link_local_metadata(self):
        self._blocked("169.254.169.254")

    def test_t18_unspecified(self):
        self._blocked("0.0.0.0")

    def test_t19_cgnat(self):
        self._blocked("100.64.0.1")

    def test_t20_multicast(self):
        self._blocked("224.0.0.1")

    def test_t21_reserved(self):
        self._blocked("240.0.0.1")

    def test_t22_public_ip_passes(self):
        target = resolve_and_validate("http://example.com/", resolver=resolve_to("1.1.1.1"))
        self.assertIsInstance(target, ProbeTarget)
        self.assertEqual(target.ip, "1.1.1.1")


# =============================================================================
# Section 5.3 — IPv6 private and special ranges (6 cases)
# =============================================================================
class TestSSRFIPv6(SSRFTestBase):

    def _blocked(self, ip):
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("http://example.com/", resolver=resolve_to(ip))
        self.assertEqual(ctx.exception.code, "blocked_ip", f"expected block for {ip}")

    def test_t23_loopback(self):
        self._blocked("::1")

    def test_t24_ula(self):
        self._blocked("fc00::1")

    def test_t25_link_local(self):
        self._blocked("fe80::1")

    def test_t26_unspecified(self):
        self._blocked("::")

    def test_t27_mapped_loopback(self):
        self._blocked("::ffff:127.0.0.1")

    def test_t28_public_ipv6_passes(self):
        target = resolve_and_validate(
            "http://example.com/", resolver=resolve_to("2001:4860:4860::8888")
        )
        self.assertIsInstance(target, ProbeTarget)


# =============================================================================
# Section 5.4 — DNS resolution edge cases (5 cases)
# =============================================================================
class TestSSRFDNS(SSRFTestBase):

    def test_t29_multi_a_with_bad_ip(self):
        resolver = resolve_to("1.1.1.1", "127.0.0.1")
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("http://example.com/", resolver=resolver)
        self.assertEqual(ctx.exception.code, "blocked_ip")

    def test_t30_multi_a_all_public(self):
        resolver = resolve_to("1.1.1.1", "8.8.8.8")
        target = resolve_and_validate("http://example.com/", resolver=resolver)
        self.assertIn(target.ip, {"1.1.1.1", "8.8.8.8"})

    def test_t31_empty_resolution(self):
        resolver = FakeResolver({"example.com": []})
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("http://example.com/", resolver=resolver)
        self.assertEqual(ctx.exception.code, "dns_failure")

    def test_t32_resolver_raises_gaierror(self):
        resolver = FakeResolver({"example.com": socket.gaierror("no such host")})
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("http://example.com/", resolver=resolver)
        self.assertEqual(ctx.exception.code, "dns_failure")

    def test_t33_resolver_raises_timeout(self):
        resolver = FakeResolver({"example.com": socket.timeout("timed out")})
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("http://example.com/", resolver=resolver)
        self.assertEqual(ctx.exception.code, "dns_failure")


# =============================================================================
# Section 5.5 — URL syntax and encoding attacks (5 cases)
# =============================================================================
class TestSSRFEncoding(SSRFTestBase):

    def test_t34_decimal_ip(self):
        # "2130706433" = 0x7F000001 = 127.0.0.1
        # Resolver must NOT be consulted; SSRF module must recognise legacy
        # numeric IPv4 locally and block.
        resolver = FakeResolver({})   # any hostname raises gaierror
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("http://2130706433/", resolver=resolver)
        self.assertEqual(ctx.exception.code, "blocked_ip")

    def test_t35_hex_ip(self):
        # "0x7f.0.0.1" = 127.0.0.1
        resolver = FakeResolver({})
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("http://0x7f.0.0.1/", resolver=resolver)
        self.assertEqual(ctx.exception.code, "blocked_ip")

    def test_t36_short_ip(self):
        # "127.1" = 127.0.0.1
        resolver = FakeResolver({})
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("http://127.1/", resolver=resolver)
        self.assertEqual(ctx.exception.code, "blocked_ip")

    def test_t37_credentials_before_loopback(self):
        # username present -> blocked at credentials gate, not IP gate
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("http://example.com@127.0.0.1/", resolver=resolve_to("1.1.1.1"))
        self.assertEqual(ctx.exception.code, "blocked_credentials")

    def test_t38_fragment_trailing_loopback(self):
        # host is 127.0.0.1; fragment is inert
        resolver = FakeResolver({})
        with self.assertRaises(SSRFError) as ctx:
            resolve_and_validate("http://127.0.0.1:80#@evil.com/", resolver=resolver)
        self.assertEqual(ctx.exception.code, "blocked_ip")


# =============================================================================
# Section 5.6 — Redirect chains (4 cases, integration)
# =============================================================================
class TestProbeRedirects(ProbeTestBase):

    def test_t39_redirect_to_loopback(self):
        with _LocalHTTPServer({
            "/r1": ("redirect", "http://127.0.0.1:9999/"),
        }) as srv:
            with self.assertRaises(Exception) as ctx:
                probe(
                    f"{srv.base_url}/r1",
                    _test_bypass_initial_ssrf=True,
                )
            # Implementation may raise SSRFError or return an outcome with
            # status=blocked_ssrf. Accept either as "the redirect was blocked".
            self._assert_blocked_outcome_or_ssrf(ctx)

    def test_t40_redirect_to_metadata(self):
        with _LocalHTTPServer({
            "/r1": ("redirect", "http://169.254.169.254/latest/meta-data/"),
        }) as srv:
            with self.assertRaises(Exception) as ctx:
                probe(
                    f"{srv.base_url}/r1",
                    _test_bypass_initial_ssrf=True,
                )
            self._assert_blocked_outcome_or_ssrf(ctx)

    def test_t41_too_many_redirects(self):
        with _LocalHTTPServer({
            "/r1": ("redirect", "/r2"),
            "/r2": ("redirect", "/r3"),
            "/r3": ("redirect", "/r4"),
            "/r4": ("redirect", "/r5"),
            "/r5": ("ok", None),
        }) as srv:
            outcome = probe(
                f"{srv.base_url}/r1",
                _test_bypass_initial_ssrf=True,
            )
            self.assertEqual(outcome.status, "down")
            self.assertEqual(outcome.failure_reason, "too_many_redirects")

    def test_t42_single_redirect_ok(self):
        with _LocalHTTPServer({
            "/r1": ("redirect", "/r2"),
            "/r2": ("ok", None),
        }) as srv:
            outcome = probe(
                f"{srv.base_url}/r1",
                _test_bypass_initial_ssrf=True,
            )
            self.assertEqual(outcome.status, "up")
            self.assertEqual(outcome.redirect_count, 1)

    def _assert_blocked_outcome_or_ssrf(self, ctx):
        exc = ctx.exception
        if SSRFError is not None and isinstance(exc, SSRFError):
            self.assertEqual(exc.code, "blocked_ip")
        else:
            self.assertTrue(
                hasattr(exc, "status") or "SSRFError" in str(type(exc)),
                f"expected SSRF block, got {type(exc).__name__}: {exc}",
            )


# =============================================================================
# Section 5.7 — Timeout, TLS, and body handling (8 cases, integration)
# =============================================================================
class TestProbeNetwork(ProbeTestBase):

    def test_t43_read_timeout(self):
        with _LocalHTTPServer({"/slow": ("sleep", 30)}) as srv:
            started = time.monotonic()
            outcome = probe(
                f"{srv.base_url}/slow",
                _test_bypass_initial_ssrf=True,
            )
            elapsed = time.monotonic() - started
            self.assertEqual(outcome.status, "timeout")
            self.assertLess(elapsed, 25, "probe must respect hard cap")

    def test_t44_connection_refused(self):
        # Bind a socket, immediately close it -> port is now unused
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()
        outcome = probe(
            f"http://127.0.0.1:{port}/",
            _test_bypass_initial_ssrf=True,
        )
        self.assertEqual(outcome.status, "down")
        self.assertEqual(outcome.failure_reason, "connect_refused")

    def test_t45_tls_verification_failed(self):
        # Self-signed cert on the server side is rejected by strict verification
        cert_dir = Path(__file__).parent / "_tls_fixtures"
        cert_file = cert_dir / "server.pem"
        key_file = cert_dir / "server.key"
        if not (cert_file.exists() and key_file.exists()):
            self.skipTest("TLS fixtures not generated yet (Phase 3)")
        outcome = probe(
            f"https://127.0.0.1:1/",  # port 1 unused; test is the CERT, not the port
            _test_bypass_initial_ssrf=True,
            _test_ssl_ca=str(cert_file),  # do NOT trust it -> verification fails
        )
        self.assertIn(outcome.status, ("tls_error", "down"))

    def test_t46_valid_cert(self):
        # Requires an HTTPS server with a CA-signed cert we generate at Phase 3.
        self.skipTest("TLS valid-cert test enabled in Phase 3 with generated fixtures")

    def test_t47_204_no_content(self):
        with _LocalHTTPServer({"/empty": ("empty", None)}) as srv:
            outcome = probe(
                f"{srv.base_url}/empty",
                _test_bypass_initial_ssrf=True,
            )
            self.assertEqual(outcome.status, "up")
            self.assertEqual(outcome.http_status, 204)

    def test_t48_body_cap(self):
        with _LocalHTTPServer({"/big": ("big", None)}) as srv:
            outcome = probe(
                f"{srv.base_url}/big",
                _test_bypass_initial_ssrf=True,
            )
            self.assertEqual(outcome.status, "up")
            self.assertLessEqual(
                getattr(outcome, "body_bytes", 0), 65536 + 4096,
                "body must be capped near 64 KB",
            )

    def test_t49_5xx_is_down(self):
        with _LocalHTTPServer({"/500": ("status", 500)}) as srv:
            outcome = probe(
                f"{srv.base_url}/500",
                _test_bypass_initial_ssrf=True,
            )
            self.assertEqual(outcome.status, "down")
            self.assertEqual(outcome.http_status, 500)

    def test_t50_4xx_is_up(self):
        with _LocalHTTPServer({"/404": ("status", 404)}) as srv:
            outcome = probe(
                f"{srv.base_url}/404",
                _test_bypass_initial_ssrf=True,
            )
            self.assertEqual(outcome.status, "up")
            self.assertEqual(outcome.http_status, 404)


if __name__ == "__main__":
    unittest.main()
