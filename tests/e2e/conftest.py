"""Fixtures for Playwright E2E tests.

The live server is started as a subprocess with:
  - QUADLETMAN_TEST_AUTH_USER=testuser  — bypasses PAM, no root required
  - QUADLETMAN_DB_PATH=<tmp>/test.db   — isolated throwaway database
  - QUADLETMAN_PORT=18080              — avoid clashing with dev server

Run only E2E tests:
    uv run pytest -m e2e

The harness automatically installs Playwright browser binaries when E2E tests start.
"""

import csv
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
import requests
import yaml
from jinja2 import Environment, FileSystemLoader


def pytest_addoption(parser):
    """Add CLI options for VM E2E tests."""
    parser.addoption(
        "--settings-file",
        action="store",
        default=None,
        help="Path to YAML/JSON settings file with VM configs",
    )
    parser.addoption(
        "--destroy-vms",
        action="store_true",
        default=False,
        help="Destroy existing VMs before starting (default: false)",
    )
    parser.addoption(
        "--reprovision",
        action="store_true",
        default=True,
        help="Reprovision VMs if they exist (default: true)",
    )
    parser.addoption(
        "--artifacts-dir",
        action="store",
        default="test-artifacts",
        help="Directory to store test artifacts",
    )
    parser.addoption(
        "--report-format",
        action="store",
        default="json,csv,html",
        help="Report formats: json,csv,html (comma-separated)",
    )
    parser.addoption(
        "--collect-logs",
        action="store_true",
        default=True,
        help="Collect logs and artifacts from VMs",
    )


def pytest_sessionstart(session):
    """Install required Playwright browsers before running E2E tests."""
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise pytest.UsageError(
            "Playwright browser install failed. Run 'uv run playwright install chromium' manually."
        ) from exc


_E2E_PORT = 18080
_BASE_URL = f"http://127.0.0.1:{_E2E_PORT}"

_test_results = []


def _load_settings(settings_file):
    """Load VM settings from YAML or JSON file.

    If no settings_file provided, uses defaults.
    See tests/e2e/vm-settings.yaml for default configuration.
    """
    defaults = {
        "vms": {
            "fedora": {
                "box": "bento/fedora-41",
                "package": "RPM",
                "memory": 2048,
                "cpus": 2,
                "hostname": "quadletman-smoke-fedora",
            },
            "ubuntu": {
                "box": "bento/ubuntu-24.04",
                "package": "DEB",
                "memory": 2048,
                "cpus": 2,
                "hostname": "quadletman-smoke-ubuntu",
            },
            "debian": {
                "box": "bento/debian-13",
                "package": "DEB",
                "memory": 2048,
                "cpus": 2,
                "hostname": "quadletman-smoke-debian",
            },
        }
    }
    if not settings_file:
        return defaults
    with open(settings_file) as f:
        if settings_file.endswith(".yaml") or settings_file.endswith(".yml"):
            user_settings = yaml.safe_load(f)
        else:
            import json

            user_settings = json.load(f)
    # Merge defaults with user settings
    defaults["vms"].update(user_settings.get("vms", {}))
    return defaults


def _generate_vagrantfile(settings, vm_dir):
    """Generate Vagrantfile from template."""
    template_path = vm_dir / "Vagrantfile.template"
    env = Environment(loader=FileSystemLoader(template_path.parent))
    template = env.get_template(template_path.name)
    vagrantfile_content = template.render(
        vms=settings["vms"],
        RSYNC_EXCLUDES=[
            ".git/",
            ".venv/",
            "__pycache__/",
            "*.egg-info/",
            "dist/",
            "build/",
            "node_modules/",
        ],
    )
    with open(vm_dir / "Vagrantfile", "w") as f:
        f.write(vagrantfile_content)


def _find_free_port():
    """Find a free port on the host."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        s.listen(1)
        port = s.getsockname()[1]
    return port


def _collect_vm_logs(vm_name, vm_dir, artifacts_dir):
    """Collect logs and artifacts from VM."""
    try:
        # Collect journal logs
        journal_log = artifacts_dir / "journal.log"
        with open(journal_log, "w") as f:
            subprocess.run(
                ["vagrant", "ssh", vm_name, "-c", "sudo journalctl -u quadletman --no-pager"],
                cwd=vm_dir,
                stdout=f,
                stderr=subprocess.STDOUT,
                timeout=30,
            )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        pass  # Log collection is best-effort

    try:
        # Collect quadletman logs
        app_log = artifacts_dir / "quadletman.log"
        with open(app_log, "w") as f:
            subprocess.run(
                [
                    "vagrant",
                    "ssh",
                    vm_name,
                    "-c",
                    "sudo cat /var/log/quadletman.log 2>/dev/null || echo 'No app log found'",
                ],
                cwd=vm_dir,
                stdout=f,
                stderr=subprocess.STDOUT,
                timeout=30,
            )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        pass

    try:
        # Collect SELinux audit logs if SELinux is enabled
        selinux_log = artifacts_dir / "selinux-audit.log"
        with open(selinux_log, "w") as f:
            subprocess.run(
                [
                    "vagrant",
                    "ssh",
                    vm_name,
                    "-c",
                    "sudo ausearch -m avc -ts recent 2>/dev/null || echo 'SELinux not enabled or no AVC logs'",
                ],
                cwd=vm_dir,
                stdout=f,
                stderr=subprocess.STDOUT,
                timeout=30,
            )
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        pass


def _wait_for_server(url, timeout=30):
    """Wait for server to respond at URL."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(url, timeout=1)
            if resp.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.2)
    raise TimeoutError(f"Server at {url} did not respond within {timeout}s")


@pytest.fixture(scope="session")
def live_server():
    """Start quadletman with test auth bypass and an in-memory DB, yield base URL."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        agent_socket = os.path.join(tmpdir, "agent.sock")
        env = {
            **os.environ,
            "QUADLETMAN_TEST_AUTH_USER": "testuser",
            "QUADLETMAN_DB_PATH": db_path,
            "QUADLETMAN_AGENT_SOCKET": agent_socket,
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
    """Start quadletman in Unix socket mode, yield socket path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        agent_socket = os.path.join(tmpdir, "agent.sock")
        socket_path = os.path.join(tmpdir, "quadletman.sock")
        env = {
            **os.environ,
            "QUADLETMAN_TEST_AUTH_USER": "testuser",
            "QUADLETMAN_DB_PATH": db_path,
            "QUADLETMAN_AGENT_SOCKET": agent_socket,
            "QUADLETMAN_UNIX_SOCKET": socket_path,
            "QUADLETMAN_PORT": "28080",  # Sentinel port to check it's not bound
            "QUADLETMAN_LOG_LEVEL": "WARNING",
        }
        proc = subprocess.Popen(["uv", "run", "quadletman"], env=env)
        try:
            # Wait for socket to exist
            timeout = 10
            start = time.time()
            while time.time() - start < timeout:
                if os.path.exists(socket_path):
                    break
                time.sleep(0.1)
            else:
                raise TimeoutError(f"Socket {socket_path} was not created within {timeout}s")
            yield socket_path
        finally:
            proc.terminate()
            proc.wait(timeout=5)


@pytest.fixture(scope="session")
def settings_file(request):
    """Get settings file path from CLI."""
    return request.config.getoption("--settings-file")


@pytest.fixture(scope="session")
def settings(settings_file):
    """Load VM settings."""
    return _load_settings(settings_file)


def pytest_generate_tests(metafunc):
    """Generate parametrized tests for VM fixtures."""
    if "vm_name" in metafunc.fixturenames and metafunc.definition.get_closest_marker("vm"):
        # Get settings for parametrization
        settings_file = metafunc.config.getoption("--settings-file")
        settings = _load_settings(settings_file)
        vm_names = list(settings["vms"].keys())
        metafunc.parametrize("vm_name", vm_names)


@pytest.fixture(scope="function")
def vagrant_vm(vm_name, settings, request, vm_artifacts_dir):
    """Bring up VM, yield VM info, teardown."""
    # Check if vagrant is installed
    try:
        subprocess.run(["vagrant", "--version"], check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pytest.skip("Vagrant not installed. Install Vagrant to run VM-based E2E tests.")

    vm_dir = Path(__file__).parent.parent.parent / "packaging"

    # Generate Vagrantfile from template
    _generate_vagrantfile(settings, vm_dir)

    host_port = _find_free_port()

    env = os.environ.copy()
    env["VM_NAME"] = vm_name
    env["HOST_PORT"] = str(host_port)

    destroy = request.config.getoption("--destroy-vms")
    reprovision = request.config.getoption("--reprovision")
    collect_logs = request.config.getoption("--collect-logs")

    vagrant_log = vm_artifacts_dir / "vagrant.log"

    if destroy:
        subprocess.run(["vagrant", "destroy", "-f", vm_name], cwd=vm_dir)

    # vagrant up
    with open(vagrant_log, "w") as log_file:
        cmd = ["vagrant", "up", vm_name]
        if reprovision:
            cmd.append("--provision")
        subprocess.run(cmd, cwd=vm_dir, check=True, env=env, stdout=log_file, stderr=log_file)

    try:
        yield {"name": vm_name, "host_port": host_port, "config": settings["vms"][vm_name]}
    finally:
        if collect_logs:
            # Collect logs from VM
            _collect_vm_logs(vm_name, vm_dir, vm_artifacts_dir)
        # Always destroy
        subprocess.run(["vagrant", "destroy", "-f", vm_name], cwd=vm_dir)


@pytest.fixture(scope="session")
def artifacts_dir(request):
    """Get artifacts directory."""
    return Path(request.config.getoption("--artifacts-dir"))


@pytest.fixture(scope="session", autouse=True)
def setup_artifacts(artifacts_dir):
    """Create artifacts directory."""
    artifacts_dir.mkdir(exist_ok=True)


@pytest.fixture(scope="function")
def test_artifacts_dir(artifacts_dir, request):
    """Create per-test artifacts directory."""
    # For VM tests, use vm_name; for local tests, use "local"
    vm_name = (
        getattr(request, "param", {}).get("vm_name", "local")
        if hasattr(request, "param")
        else "local"
    )
    if "vm_name" in request.fixturenames:
        vm_name = request.getfixturevalue("vm_name")

    test_name = request.node.name
    test_dir = artifacts_dir / vm_name / test_name
    test_dir.mkdir(parents=True, exist_ok=True)
    return test_dir


@pytest.fixture(scope="function", autouse=True)
def setup_test_artifacts(request, test_artifacts_dir):
    """Set up per-test artifacts."""
    # Store for use in hooks
    request.node.test_artifacts_dir = test_artifacts_dir


def pytest_runtest_makereport(item, call):
    """Collect test results."""
    if call.when == "call":
        # Get VM name from parametrized fixture
        vm = getattr(item, "vm_name", "unknown")
        if vm == "unknown":
            # Try to get from request
            for fixture_name in item.fixturenames:
                if fixture_name == "vm_name":
                    vm = item.funcargs.get("vm_name", "unknown")
                    break

        # Capture screenshot on failure
        screenshot_path = None
        if call.excinfo and hasattr(item, "test_artifacts_dir"):
            try:
                page = item.funcargs.get("page")
                if page:
                    screenshot_path = item.test_artifacts_dir / "failure.png"
                    page.screenshot(path=str(screenshot_path))
            except Exception:
                pass  # Screenshot is best-effort

        result = {
            "test_name": item.name,
            "vm": vm,
            "status": call.excinfo is None and "passed" or "failed",
            "duration": call.duration,
            "error": str(call.excinfo) if call.excinfo else None,
            "screenshot": str(screenshot_path) if screenshot_path else None,
            "artifacts_dir": str(item.test_artifacts_dir)
            if hasattr(item, "test_artifacts_dir")
            else None,
        }
        _test_results.append(result)


def pytest_sessionfinish(session, exitstatus):
    """Generate reports."""
    artifacts_dir = Path(session.config.getoption("--artifacts-dir"))
    report_format = session.config.getoption("--report-format").split(",")

    if "json" in report_format:
        with open(artifacts_dir / "report.json", "w") as f:
            json.dump(_test_results, f, indent=2)

    if "csv" in report_format:
        with open(artifacts_dir / "report.csv", "w", newline="") as f:
            if _test_results:
                writer = csv.DictWriter(f, fieldnames=_test_results[0].keys())
                writer.writeheader()
                writer.writerows(_test_results)

    # HTML report
    if "html" in report_format:
        html_report = artifacts_dir / "report.html"
        with open(html_report, "w") as f:
            f.write(_generate_html_report(_test_results))

    print("\n=== E2E Test Summary ===")
    print(f"Total tests: {len(_test_results)}")
    passed = sum(1 for r in _test_results if r["status"] == "passed")
    failed = sum(1 for r in _test_results if r["status"] == "failed")
    print(f"Passed: {passed}, Failed: {failed}")
    print(f"Artifacts: {artifacts_dir}")
    print(f"Exit status: {exitstatus}")


def _generate_html_report(results):
    """Generate a simple HTML report."""
    html = """<!DOCTYPE html>
<html>
<head>
    <title>E2E Test Report</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; }
        table { border-collapse: collapse; width: 100%; }
        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
        th { background-color: #f2f2f2; }
        .passed { color: green; }
        .failed { color: red; }
        .error { background-color: #ffe6e6; }
    </style>
</head>
<body>
    <h1>E2E Test Report</h1>
    <table>
        <tr>
            <th>Test Name</th>
            <th>VM</th>
            <th>Status</th>
            <th>Duration (s)</th>
            <th>Screenshot</th>
            <th>Artifacts</th>
            <th>Error</th>
        </tr>"""

    for result in results:
        status_class = "passed" if result["status"] == "passed" else "failed"
        error_cell = f'<td class="error">{result["error"]}</td>' if result["error"] else "<td></td>"
        screenshot_cell = (
            f'<td><a href="{result["screenshot"]}">Screenshot</a></td>'
            if result["screenshot"]
            else "<td></td>"
        )
        artifacts_cell = (
            f'<td><a href="{result["artifacts_dir"]}">Artifacts</a></td>'
            if result["artifacts_dir"]
            else "<td></td>"
        )

        html += f"""
        <tr>
            <td>{result["test_name"]}</td>
            <td>{result["vm"]}</td>
            <td class="{status_class}">{result["status"]}</td>
            <td>{result["duration"]:.2f}</td>
            {screenshot_cell}
            {artifacts_cell}
            {error_cell}
        </tr>"""

    html += """
    </table>
</body>
</html>"""
    return html
