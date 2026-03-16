"""Basic smoke tests — verify the app loads and auth bypass works."""

import pytest


@pytest.mark.e2e
def test_dashboard_loads(page, server_url):
    """With TEST_AUTH_USER set, / should load the dashboard without login."""
    page.goto(server_url + "/")
    # Should NOT be redirected to login
    assert "/login" not in page.url
    # Dashboard heading is present
    page.wait_for_selector("text=quadletman", timeout=5000)


@pytest.mark.e2e
def test_login_page_still_accessible(page, server_url):
    """The login page should still render even when auth is bypassed."""
    page.goto(server_url + "/login")
    assert page.locator("input[name='username']").is_visible()


@pytest.mark.e2e
def test_health_endpoint(server_url):
    """Health endpoint should return 200 with no auth required."""
    import requests

    resp = requests.get(server_url + "/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
