"""Offline tests for pinning Baidu traffic to IPv4.

The real failure this guards against is silent: Baidu resolves to both families,
a dual-stack host prefers IPv6, and the console whitelist only records IPv4, so
correctly-whitelisted requests are still refused with status 210. No test here
opens a socket.
"""

import socket

import pytest

from app.providers.baidu import net


@pytest.fixture(autouse=True)
def restore_getaddrinfo():
    """Reset both the patch and its refcount.

    `install_ipv4_only()` is process-wide by design, so an earlier test leaving
    it installed (or a context manager leaving `_depth` above zero) would make
    later tests see a pre-patched socket module.
    """
    original = net._original_getaddrinfo
    socket.getaddrinfo = original
    net._depth = 0
    yield
    socket.getaddrinfo = original
    net._depth = 0


def test_ipv4_only_is_enabled_by_default():
    assert net.force_ipv4_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "False", "no"])
def test_the_flag_can_disable_pinning(monkeypatch, value):
    monkeypatch.setenv(net.ENV_FLAG, value)

    assert net.force_ipv4_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes"])
def test_the_flag_can_confirm_pinning(monkeypatch, value):
    monkeypatch.setenv(net.ENV_FLAG, value)

    assert net.force_ipv4_enabled() is True


def test_installing_replaces_getaddrinfo():
    assert net.install_ipv4_only() is True

    assert socket.getaddrinfo is net._ipv4_only_getaddrinfo


def test_installing_is_idempotent():
    net.install_ipv4_only()
    first = socket.getaddrinfo
    net.install_ipv4_only()

    assert socket.getaddrinfo is first


def test_a_disabled_flag_leaves_getaddrinfo_alone(monkeypatch):
    monkeypatch.setenv(net.ENV_FLAG, "0")
    original = socket.getaddrinfo

    assert net.install_ipv4_only() is False
    assert socket.getaddrinfo is original


def test_the_context_manager_restores_the_original():
    original = net._original_getaddrinfo

    with net.ipv4_only_sockets():
        assert socket.getaddrinfo is net._ipv4_only_getaddrinfo

    assert socket.getaddrinfo is original


def test_nested_contexts_restore_only_after_the_last_exit():
    original = net._original_getaddrinfo

    with net.ipv4_only_sockets():
        with net.ipv4_only_sockets():
            assert socket.getaddrinfo is net._ipv4_only_getaddrinfo
        # Still patched: the outer block is active.
        assert socket.getaddrinfo is net._ipv4_only_getaddrinfo

    assert socket.getaddrinfo is original


def test_the_filter_drops_ipv6_results(monkeypatch):
    """The patch must return only A records when any exist."""
    ipv4 = (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.2.3.4", 443))
    ipv6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))
    monkeypatch.setattr(net, "_original_getaddrinfo", lambda *a, **k: [ipv6, ipv4])

    result = net._ipv4_only_getaddrinfo("example.test", 443)

    assert result == [ipv4]


def test_an_ipv6_only_host_still_resolves(monkeypatch):
    """Pinning must not make an AAAA-only host unreachable."""
    ipv6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))
    monkeypatch.setattr(net, "_original_getaddrinfo", lambda *a, **k: [ipv6])

    result = net._ipv4_only_getaddrinfo("v6only.test", 443)

    assert result == [ipv6]


def test_an_explicit_ipv6_request_is_left_untouched(monkeypatch):
    """A caller that asks for AF_INET6 explicitly must still get it."""
    ipv6 = (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", 443, 0, 0))
    monkeypatch.setattr(net, "_original_getaddrinfo", lambda *a, **k: [ipv6])

    result = net._ipv4_only_getaddrinfo("example.test", 443, socket.AF_INET6)

    assert result == [ipv6]


def test_the_provider_factory_installs_the_pin():
    """Baidu providers must never be built without the IPv4 pin."""
    from app.providers.baidu.live import build_live_providers

    build_live_providers("ak-ipv4-pin-test")

    assert socket.getaddrinfo is net._ipv4_only_getaddrinfo
