"""E2E tests on VMs — verify the app loads and auth works."""

import pytest


@pytest.mark.e2e
@pytest.mark.vm
def test_dashboard_loads_on_vm(page, host_based_server):
    """Test dashboard loads on VM with real auth."""
    page.goto(host_based_server + "/")
    # Should redirect to login
    assert "/login" in page.url
    # Login
    page.fill("input[name='username']", "smoketest")
    page.fill("input[name='password']", "smoketest")
    page.click("button[type='submit']")
    # Should load dashboard
    page.wait_for_selector("text=quadletman", timeout=5000)


@pytest.mark.e2e
@pytest.mark.vm
def test_health_endpoint_on_vm(host_based_server):
    """Health endpoint should return 200 on VM."""
    import requests

    resp = requests.get(host_based_server + "/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
