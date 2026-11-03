"""DNS resolution with a hard timeout.

Wrapped in a thread so a hung DNS server cannot stall the worker.
Tests inject their own resolver callable, bypassing this module entirely.
"""

import socket
import threading

DNS_TIMEOUT_SECONDS = 5.0


def resolve_with_timeout(hostname: str, timeout: float = DNS_TIMEOUT_SECONDS) -> list[str]:
    """Resolve hostname to unique IP strings. Raises on failure/timeout."""
    result: dict = {}
    errors: dict = {}

    def _worker():
        try:
            info = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
            seen = set()
            ips = []
            for _family, _type, _proto, _canon, sockaddr in info:
                ip = sockaddr[0]
                if ip not in seen:
                    seen.add(ip)
                    ips.append(ip)
            result["ips"] = ips
        except Exception as exc:
            errors["exc"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise socket.timeout(f"DNS resolution timed out after {timeout}s")
    if "exc" in errors:
        raise errors["exc"]
    return result.get("ips", [])


default_resolver = resolve_with_timeout
