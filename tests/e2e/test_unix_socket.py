"""Smoke tests for Unix socket binding mode (QUADLETMAN_UNIX_SOCKET).

These tests verify that:
  - The app becomes reachable via the Unix socket.
  - The socket has mode 0660 (group-readable by allowed-group members).
  - No TCP port is opened when socket mode is active.
  - Basic HTTP endpoints respond correctly through the socket.
"""

import os
import socket
import stat

import httpx
import pytest


@pytest.fixture(scope="module")
def socket_client(live_server_socket):
    """httpx client pre-configured to talk through the Unix socket."""
    transport = httpx.HTTPTransport(uds=live_server_socket)
    with httpx.Client(transport=transport, base_url="http://localhost") as client:
        yield client


@pytest.mark.e2e
def test_health_via_socket(socket_client):
    resp = socket_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.e2e
def test_root_responds_via_socket(socket_client):
    """/ should return 200 (dashboard) or 303 redirect — not a connection error."""
    resp = socket_client.get("/", follow_redirects=True)
    assert resp.status_code == 200


@pytest.mark.e2e
def test_socket_mode_is_0660(live_server_socket):
    """Socket must be group-readable (0660) so allowed-group members can connect."""
    mode = stat.S_IMODE(os.stat(live_server_socket).st_mode)
    assert mode == 0o660, f"Expected 0660, got {oct(mode)}"


@pytest.mark.e2e
def test_socket_is_a_socket(live_server_socket):
    assert stat.S_ISSOCK(os.stat(live_server_socket).st_mode)


@pytest.mark.e2e
def test_no_tcp_port_bound(live_server_socket):
    """When Unix socket mode is active, the sentinel port must not be bound.

    The fixture sets QUADLETMAN_PORT=28080 — a port the server would use if it
    accidentally fell back to TCP mode.  In socket mode nothing should bind it.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        result = s.connect_ex(("127.0.0.1", 28080))
        assert result != 0, "Server bound TCP port 28080 — Unix socket mode did not take effect"
