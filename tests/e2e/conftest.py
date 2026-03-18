"""Fixtures for Playwright E2E tests.

The live server is started as a subprocess with:
  - QUADLETMAN_TEST_AUTH_USER=testuser  — bypasses PAM, no root required
  - QUADLETMAN_DB_PATH=<tmp>/test.db   — isolated throwaway database
  - QUADLETMAN_PORT=18080              — avoid clashing with dev server

Run only E2E tests:
    uv run pytest -m e2e

Install Playwright browsers once:
    uv run playwright install chromium
"""

import os
import subprocess
import tempfile
import time
from contextlib import suppress
from pathlib import Path

import httpx
import pytest
import requests

_E2E_PORT = 18080
_BASE_URL = f"http://127.0.0.1:{_E2E_PORT}"


@pytest.fixture(scope="session")
def live_server():
    """Start quadletman with test auth bypass and an in-memory DB, yield base URL."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        env = {
            **os.environ,
            "QUADLETMAN_TEST_AUTH_USER": "testuser",
            "QUADLETMAN_DB_PATH": db_path,
            "QUADLETMAN_PORT": str(_E2E_PORT),
            "QUADLETMAN_HOST": "127.0.0.1",
            "QUADLETMAN_LOG_LEVEL": "WARNING",
        }
        proc = subprocess.Popen(["uv", "run", "quadletman"], env=env)
        try:
            _wait_for_server(_BASE_URL + "/health")
            yield _BASE_URL
        finally:
            proc.terminate()
            proc.wait(timeout=5)


@pytest.fixture(scope="session")
def live_server_socket():
    """Start quadletman bound to a Unix socket, yield the socket path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        socket_path = os.path.join(tmpdir, "quadletman.sock")
        env = {
            **os.environ,
            "QUADLETMAN_TEST_AUTH_USER": "testuser",
            "QUADLETMAN_DB_PATH": db_path,
            "QUADLETMAN_UNIX_SOCKET": socket_path,
            "QUADLETMAN_PORT": "28080",  # sentinel — must NOT be bound in socket mode
            "QUADLETMAN_LOG_LEVEL": "WARNING",
        }
        proc = subprocess.Popen(["uv", "run", "quadletman"], env=env)
        try:
            _wait_for_socket(socket_path)
            yield socket_path
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            with suppress(OSError):
                Path(socket_path).unlink()


def _wait_for_server(url: str, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if requests.get(url, timeout=1).status_code == 200:
                return
        except requests.ConnectionError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"Server did not become ready at {url} within {timeout}s")


def _wait_for_socket(path: str, timeout: float = 10.0) -> None:
    transport = httpx.HTTPTransport(uds=path)
    deadline = time.monotonic() + timeout
    with httpx.Client(transport=transport) as client:
        while time.monotonic() < deadline:
            with suppress(Exception):
                if client.get("http://localhost/health").status_code == 200:
                    return
            time.sleep(0.2)
    raise RuntimeError(f"Socket server did not become ready at {path} within {timeout}s")


@pytest.fixture(scope="session")
def server_url(live_server):
    return live_server
