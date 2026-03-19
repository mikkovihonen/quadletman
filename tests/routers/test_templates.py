"""Tests for /api/templates and /api/compartments/from-template routes."""

import pytest

from quadletman.models import CompartmentCreate, ContainerCreate, TemplateCreate
from quadletman.models.sanitized import SafeSlug
from quadletman.services import compartment_manager


def _sid(s: str) -> SafeSlug:
    return SafeSlug.trusted(s, "test")


@pytest.fixture(autouse=True)
def mock_system_calls(mocker):
    mocker.patch("quadletman.services.compartment_manager._setup_service_user")
    mocker.patch("quadletman.services.compartment_manager._teardown_service")
    mocker.patch("quadletman.services.compartment_manager._write_and_reload")
    mocker.patch("quadletman.services.compartment_manager.systemd_manager.daemon_reload")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_container_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_network_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_pod_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_volume_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.write_image_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_container_unit")
    mocker.patch("quadletman.services.compartment_manager.quadlet_writer.remove_network_unit")
    mocker.patch("quadletman.services.compartment_manager.user_manager.get_uid", return_value=1001)
    mocker.patch("quadletman.services.compartment_manager.user_manager.sync_helper_users")
    mocker.patch("quadletman.services.compartment_manager.user_manager._setup_subuid_subgid")
    mocker.patch("quadletman.services.compartment_manager.volume_manager.create_volume_dir")
    mocker.patch("quadletman.services.compartment_manager.volume_manager.chown_volume_dir")
    mocker.patch("quadletman.services.compartment_manager.volume_manager.delete_volume_dir")
    mocker.patch(
        "quadletman.services.compartment_manager.get_status",
        return_value={"service_id": "x", "containers": []},
    )
    mocker.patch(
        "quadletman.routers._helpers.user_manager.get_user_info",
        return_value={"uid": 1001, "home": "/home/qm-test"},
    )
    mocker.patch("quadletman.routers._helpers.user_manager.list_helper_users", return_value=[])


async def _make_compartment(db, comp_id="src"):
    await compartment_manager.create_compartment(db, CompartmentCreate(id=comp_id))
    return comp_id


async def _make_compartment_with_container(db, comp_id="src"):
    await _make_compartment(db, comp_id)
    await compartment_manager.add_container(
        db, _sid(comp_id), ContainerCreate(name="web", image="nginx:latest")
    )
    return comp_id


class TestListTemplates:
    async def test_empty_list(self, client):
        resp = await client.get("/api/templates")
        assert resp.status_code == 200
        assert resp.json() == []

    async def test_returns_saved_template(self, client, db):
        await _make_compartment(db)
        await compartment_manager.save_template(
            db, TemplateCreate(name="tmpl1", source_compartment_id="src")
        )
        resp = await client.get("/api/templates")
        names = [t["name"] for t in resp.json()]
        assert "tmpl1" in names


class TestSaveTemplate:
    async def test_saves_template(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/templates",
            json={"name": "my-tmpl", "source_compartment_id": "src"},
        )
        assert resp.status_code == 201
        assert resp.json()["name"] == "my-tmpl"

    async def test_returns_404_for_missing_compartment(self, client):
        resp = await client.post(
            "/api/templates",
            json={"name": "t", "source_compartment_id": "ghost"},
        )
        assert resp.status_code == 404


class TestDeleteTemplate:
    async def test_deletes_template(self, client, db):
        await _make_compartment(db)
        create_resp = await client.post(
            "/api/templates",
            json={"name": "del-tmpl", "source_compartment_id": "src"},
        )
        tid = create_resp.json()["id"]
        resp = await client.delete(f"/api/templates/{tid}")
        assert resp.status_code == 204

    async def test_delete_removes_from_list(self, client, db):
        await _make_compartment(db)
        create_resp = await client.post(
            "/api/templates",
            json={"name": "vanish", "source_compartment_id": "src"},
        )
        tid = create_resp.json()["id"]
        await client.delete(f"/api/templates/{tid}")
        resp = await client.get("/api/templates")
        names = [t["name"] for t in resp.json()]
        assert "vanish" not in names


class TestSaveTemplateErrors:
    async def test_unexpected_error_returns_500(self, client, db, mocker):
        mocker.patch(
            "quadletman.routers.templates.compartment_manager.save_template",
            side_effect=Exception("db exploded"),
        )
        await _make_compartment(db)
        resp = await client.post(
            "/api/templates",
            json={"name": "boom", "source_compartment_id": "src"},
        )
        assert resp.status_code == 500


class TestCreateFromTemplateErrors:
    async def test_unexpected_error_returns_500(self, client, db, mocker):
        await _make_compartment_with_container(db)
        tmpl_resp = await client.post(
            "/api/templates",
            json={"name": "err-tmpl", "source_compartment_id": "src"},
        )
        tid = tmpl_resp.json()["id"]
        mocker.patch(
            "quadletman.routers.templates.compartment_manager.create_compartment_from_template",
            side_effect=Exception("boom"),
        )
        resp = await client.post(
            f"/api/compartments/from-template/{tid}",
            json={"compartment_id": "fail-comp", "description": ""},
        )
        assert resp.status_code == 500


class TestHTMXPaths:
    async def test_list_templates_htmx_returns_html(self, client):
        resp = await client.get("/api/templates", headers={"HX-Request": "true"})
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]

    async def test_save_template_htmx_returns_html(self, client, db):
        await _make_compartment(db)
        resp = await client.post(
            "/api/templates",
            json={"name": "htmx-tmpl", "source_compartment_id": "src"},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code in (200, 201)
        assert "text/html" in resp.headers["content-type"]

    async def test_create_from_template_htmx_returns_html(self, client, db):
        await _make_compartment_with_container(db)
        tmpl_resp = await client.post(
            "/api/templates",
            json={"name": "htmx-clone", "source_compartment_id": "src"},
        )
        tid = tmpl_resp.json()["id"]
        resp = await client.post(
            f"/api/compartments/from-template/{tid}",
            json={"compartment_id": "htmx-cloned", "description": ""},
            headers={"HX-Request": "true"},
        )
        assert resp.status_code in (200, 201)
        assert "text/html" in resp.headers["content-type"]


class TestCreateFromTemplate:
    async def test_creates_compartment(self, client, db):
        await _make_compartment_with_container(db)
        tmpl_resp = await client.post(
            "/api/templates",
            json={"name": "clone-tmpl", "source_compartment_id": "src"},
        )
        tid = tmpl_resp.json()["id"]
        resp = await client.post(
            f"/api/compartments/from-template/{tid}",
            json={"compartment_id": "cloned", "description": ""},
        )
        assert resp.status_code == 201
        assert resp.json()["id"] == "cloned"

    async def test_cloned_compartment_has_containers(self, client, db):
        await _make_compartment_with_container(db)
        tmpl_resp = await client.post(
            "/api/templates",
            json={"name": "ct", "source_compartment_id": "src"},
        )
        tid = tmpl_resp.json()["id"]
        await client.post(
            f"/api/compartments/from-template/{tid}",
            json={"compartment_id": "cloned2", "description": ""},
        )
        resp = await client.get("/api/compartments/cloned2")
        containers = resp.json()["containers"]
        assert any(c["name"] == "web" for c in containers)

    async def test_returns_404_for_missing_template(self, client):
        resp = await client.post(
            "/api/compartments/from-template/nonexistent",
            json={"compartment_id": "x", "description": ""},
        )
        assert resp.status_code == 404

    async def test_stripped_secrets_warning_in_json(self, client, db):
        """Containers with secrets in the source should produce a warnings field."""
        await _make_compartment(db, "src2")
        container = await compartment_manager.add_container(
            db, _sid("src2"), ContainerCreate(name="app", image="myapp:latest")
        )
        # Manually inject a secret reference into the container row
        from sqlalchemy import text

        await db.execute(
            text("UPDATE containers SET secrets = :sec WHERE id = :id"),
            {"sec": '["my-secret"]', "id": container.id},
        )
        await db.commit()

        tmpl_resp = await client.post(
            "/api/templates",
            json={"name": "secret-tmpl", "source_compartment_id": "src2"},
        )
        tid = tmpl_resp.json()["id"]
        resp = await client.post(
            f"/api/compartments/from-template/{tid}",
            json={"compartment_id": "clonedsec", "description": ""},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert "warnings" in data
        assert len(data["warnings"]) > 0
