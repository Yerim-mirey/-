"""E2E fixtures: force offline scenarios and deny network access."""

import socket

import pytest


@pytest.fixture
def no_network(monkeypatch):
    """Fail loudly if a scenario tries to open a socket."""

    def deny(*args, **kwargs):
        raise AssertionError("Phase 8 Mock E2E 场景不得访问网络")

    monkeypatch.setattr(socket.socket, "connect", deny)
    monkeypatch.setattr(socket, "create_connection", deny)
