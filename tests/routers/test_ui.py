"""Tests for quadletman/routers/ui.py — login flow and health endpoint."""


class TestHealth:
    async def test_health_returns_ok(self, client):
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestIndex:
    async def test_index_requires_auth(self):
        """Index without auth override should redirect to login."""
        from httpx import ASGITransport, AsyncClient

        from quadletman.main import app

        # Clear any overrides to test real auth path
        original = app.dependency_overrides.copy()
        app.dependency_overrides.clear()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as c:
                resp = await c.get("/")
        finally:
            app.dependency_overrides.update(original)
        # Should redirect to /login
        assert resp.status_code in (302, 303)
        assert "/login" in resp.headers.get("location", "")

    async def test_index_returns_html_when_authenticated(self, client):
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]


class TestLoginPage:
    async def test_login_page_accessible(self, client):
        resp = await client.get("/login")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_login_post_fails_with_bad_credentials(self, mocker):
        """POST /login with invalid creds returns 401 (no PAM available in test env)."""
        from httpx import ASGITransport, AsyncClient

        from quadletman.main import app

        # Patch PAM to always fail
        mock_pam = mocker.MagicMock()
        mock_pam.authenticate.return_value = False
        mocker.patch("quadletman.routers.ui.pam.pam", return_value=mock_pam)

        original = app.dependency_overrides.copy()
        app.dependency_overrides.clear()
        try:
            async with AsyncClient(
                transport=ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
            ) as c:
                resp = await c.post(
                    "/login",
                    data={"username": "baduser", "password": "wrongpass"},
                )
        finally:
            app.dependency_overrides.update(original)
        assert resp.status_code == 401
