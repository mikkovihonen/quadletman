"""Tests for quadletman/models.py — pure Pydantic validation logic."""

import pytest
from pydantic import ValidationError

from quadletman.models import BindMount, CompartmentCreate, ContainerCreate, VolumeCreate

# ---------------------------------------------------------------------------
# CompartmentCreate
# ---------------------------------------------------------------------------


class TestCompartmentCreateId:
    def test_valid_single_char(self):
        comp = CompartmentCreate(id="a")
        assert comp.id == "a"

    def test_valid_slug(self):
        comp = CompartmentCreate(id="my-service")
        assert comp.id == "my-service"

    def test_valid_alphanumeric_only(self):
        comp = CompartmentCreate(id="abc123")
        assert comp.id == "abc123"

    def test_rejects_uppercase(self):
        with pytest.raises(ValidationError):
            CompartmentCreate(id="MyService")

    def test_rejects_leading_hyphen(self):
        with pytest.raises(ValidationError):
            CompartmentCreate(id="-bad")

    def test_rejects_trailing_hyphen(self):
        with pytest.raises(ValidationError):
            CompartmentCreate(id="bad-")

    def test_rejects_too_long(self):
        # max 32 chars: start + up to 30 middle + end = 32 total
        with pytest.raises(ValidationError):
            CompartmentCreate(id="a" * 33, display_name="X")

    def test_rejects_qm_prefix(self):
        with pytest.raises(ValidationError):
            CompartmentCreate(id="qm-foo")

    def test_rejects_underscore(self):
        with pytest.raises(ValidationError):
            CompartmentCreate(id="my_service")


# ---------------------------------------------------------------------------
# VolumeCreate
# ---------------------------------------------------------------------------


class TestVolumeCreate:
    def test_valid(self):
        vol = VolumeCreate(qm_name="mydata")
        assert vol.qm_name == "mydata"
        assert vol.qm_owner_uid == 0

    def test_name_allows_hyphen_and_underscore(self):
        vol = VolumeCreate(qm_name="my-data_v2")
        assert vol.qm_name == "my-data_v2"

    def test_name_rejects_uppercase(self):
        with pytest.raises(ValidationError):
            VolumeCreate(qm_name="MyData")

    def test_name_rejects_leading_digit_not_required(self):
        # digits are allowed at start per pattern ^[a-z0-9][a-z0-9_-]*$
        vol = VolumeCreate(qm_name="1data")
        assert vol.qm_name == "1data"

    def test_selinux_context_default(self):
        vol = VolumeCreate(qm_name="x")
        assert vol.qm_selinux_context == "container_file_t"

    def test_owner_uid_must_be_non_negative(self):
        with pytest.raises(ValidationError):
            VolumeCreate(qm_name="x", qm_owner_uid=-1)


# ---------------------------------------------------------------------------
# BindMount
# ---------------------------------------------------------------------------


class TestBindMount:
    def test_valid(self):
        bm = BindMount(host_path="/data/foo", container_path="/mnt/foo")
        assert bm.host_path == "/data/foo"
        assert bm.options == ""

    def test_rejects_relative_host_path(self):
        with pytest.raises(ValidationError):
            BindMount(host_path="data/foo", container_path="/mnt/foo")

    def test_rejects_relative_container_path(self):
        with pytest.raises(ValidationError):
            BindMount(host_path="/data/foo", container_path="mnt/foo")

    def test_rejects_newline_in_host_path(self):
        with pytest.raises(ValidationError):
            BindMount(host_path="/data/foo\nbar", container_path="/mnt/x")

    def test_rejects_null_byte_in_options(self):
        with pytest.raises(ValidationError):
            BindMount(host_path="/a", container_path="/b", options="Z\x00bad")


# ---------------------------------------------------------------------------
# ContainerCreate — port validation
# ---------------------------------------------------------------------------


class TestContainerPorts:
    def _make(self, ports: list[str]) -> ContainerCreate:
        return ContainerCreate(qm_name="web", image="nginx", ports=ports)

    def test_bare_port(self):
        c = self._make(["80"])
        assert c.ports == ["80"]

    def test_host_container_port(self):
        c = self._make(["8080:80"])
        assert c.ports == ["8080:80"]

    def test_proto_suffix(self):
        c = self._make(["80/tcp"])
        assert c.ports == ["80/tcp"]

    def test_ip_host_container(self):
        c = self._make(["127.0.0.1:8080:80"])
        assert c.ports == ["127.0.0.1:8080:80"]

    def test_accepts_bare_colon_port(self):
        # ':80' means OS-assigned host port — valid per SafePortMapping
        c = self._make([":80"])
        assert c.ports == [":80"]

    def test_rejects_letters(self):
        with pytest.raises(ValidationError):
            self._make(["abc:80"])

    def test_rejects_control_char_in_image(self):
        with pytest.raises(ValidationError):
            ContainerCreate(qm_name="web", image="nginx\nmalicious")

    def test_rejects_control_char_in_env_key(self):
        with pytest.raises(ValidationError):
            ContainerCreate(qm_name="web", image="nginx", environment={"KEY\n": "val"})

    def test_rejects_control_char_in_env_value(self):
        with pytest.raises(ValidationError):
            ContainerCreate(qm_name="web", image="nginx", environment={"KEY": "val\r"})
