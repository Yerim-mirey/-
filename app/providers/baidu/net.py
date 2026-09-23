"""Pin Baidu Web Service traffic to IPv4.

Baidu resolves ``api.map.baidu.com`` to both address families, and a
dual-stack host prefers IPv6, so requests leave from the IPv6 address. The
console's IP whitelist only records IPv4 addresses, so those requests are
answered with HTTP 200 + ``status=210`` (``APP IP校验失败``) no matter how
carefully the IPv4 address is whitelisted.

That failure is genuinely hard to read: the whitelist looks correct, the
address the operator can look up (``curl https://ipinfo.io/ip``) is correct,
and only requests that happen to use IPv6 fail. Measured on the development
host, forcing the family decided it outright:

    forced IPv4 -> status 0
    forced IPv6 -> status 210

because the whitelist held the IPv4 address while Python preferred IPv6.

So Baidu traffic is pinned to IPv4 by default. This changes nothing about the
Tool contracts; it only chooses which address family the HTTP client uses.
``BAIDU_FORCE_IPV4=0`` disables it for a deployment whose whitelist is IPv6.

The hook patches :func:`socket.getaddrinfo`, which is what both ``urllib``
(geocoding and routing) and ``httpx`` (the model adapter, if it ever needed
Baidu) ultimately consult. It is idempotent and reference-counted so nested or
concurrent use cannot leave the process permanently patched.
"""

from __future__ import annotations

import os
import socket
import threading
from collections.abc import Iterator
from contextlib import contextmanager

ENV_FLAG = "BAIDU_FORCE_IPV4"

_original_getaddrinfo = socket.getaddrinfo
_lock = threading.Lock()
_depth = 0


def force_ipv4_enabled() -> bool:
    """Whether Baidu traffic should be pinned to IPv4 (default: yes)."""
    raw = os.environ.get(ENV_FLAG, "")
    if not raw.strip():
        return True
    return raw.strip() not in {"0", "false", "False", "no"}


def _ipv4_only_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    """Resolve as usual, then drop every IPv6 result."""
    results = _original_getaddrinfo(host, port, family, type, proto, flags)
    if family == socket.AF_INET6:
        return results
    filtered = [item for item in results if item[0] == socket.AF_INET]
    # Fall back to the original answer when a host genuinely has no A record,
    # so pinning never makes an IPv6-only host unreachable.
    return filtered or results


@contextmanager
def ipv4_only_sockets() -> Iterator[None]:
    """Force IPv4 for the duration of the block.

    Reference-counted so nesting and concurrency are safe; the patch is
    installed on the first entry and removed on the last exit.
    """
    global _depth
    with _lock:
        _depth += 1
        if _depth == 1:
            socket.getaddrinfo = _ipv4_only_getaddrinfo
    try:
        yield
    finally:
        with _lock:
            _depth -= 1
            if _depth == 0:
                socket.getaddrinfo = _original_getaddrinfo


def install_ipv4_only() -> bool:
    """Pin IPv4 process-wide when configured. Returns whether it is active."""
    if not force_ipv4_enabled():
        return False
    with _lock:
        if socket.getaddrinfo is not _ipv4_only_getaddrinfo:
            socket.getaddrinfo = _ipv4_only_getaddrinfo
    return True


def ipv4_httpx_client(**kwargs) -> "object":
    """An httpx client bound to IPv4, for callers that prefer the transport hook."""
    import httpx

    kwargs.setdefault("transport", httpx.HTTPTransport(local_address="0.0.0.0"))
    return httpx.Client(**kwargs)
