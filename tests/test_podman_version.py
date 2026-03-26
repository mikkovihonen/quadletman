"""Tests for quadletman/podman_version.py — version parsing and feature flags."""

from quadletman.models.version_span import (
    ARTIFACT_UNITS,
    AUTO_UPDATE_DRY_RUN,
    BUILD_UNITS,
    BUNDLE,
    IMAGE_UNITS,
    PASTA,
    POD_UNITS,
    QUADLET,
    QUADLET_CLI,
    SLIRP4NETNS,
    field_availability,
    is_field_available,
    is_value_available,
)
from quadletman.podman_version import PodmanFeatures, _parse_version


class TestParseVersion:
    def test_standard_version_string(self):
        assert _parse_version("podman version 4.6.1") == (4, 6, 1)

    def test_case_insensitive(self):
        assert _parse_version("Podman Version 5.0.0") == (5, 0, 0)

    def test_embedded_in_longer_output(self):
        assert _parse_version("some prefix\npodman version 4.4.0\nother stuff") == (4, 4, 0)

    def test_returns_none_for_unrecognized(self):
        assert _parse_version("not a podman version") is None

    def test_returns_none_for_empty(self):
        assert _parse_version("") is None


class TestFeatureFlags:
    def _features(self, version: tuple[int, int, int] | None) -> PodmanFeatures:
        version_str = "unknown" if version is None else ".".join(str(v) for v in version)
        return PodmanFeatures(
            version=version,
            version_str=version_str,
            slirp4netns=is_field_available(SLIRP4NETNS, version),
            pasta=is_field_available(PASTA, version),
            quadlet=is_field_available(QUADLET, version),
            image_units=is_field_available(IMAGE_UNITS, version),
            pod_units=is_field_available(POD_UNITS, version),
            build_units=is_field_available(BUILD_UNITS, version),
            quadlet_cli=is_field_available(QUADLET_CLI, version),
            artifact_units=is_field_available(ARTIFACT_UNITS, version),
            bundle=is_field_available(BUNDLE, version),
            auto_update_dry_run=is_field_available(AUTO_UPDATE_DRY_RUN, version),
        )

    def test_none_version_all_flags_false(self):
        f = self._features(None)
        assert not f.quadlet
        assert not f.build_units
        assert not f.image_units
        assert not f.pod_units
        assert not f.quadlet_cli
        assert not f.artifact_units
        assert not f.bundle
        assert not f.pasta
        assert f.version_str == "unknown"

    def test_4_3_0_no_quadlet(self):
        f = self._features((4, 3, 0))
        assert not f.quadlet
        assert not f.build_units
        assert not f.image_units
        assert not f.pod_units
        assert f.pasta  # >= 4.1

    def test_4_4_0_quadlet_enabled(self):
        f = self._features((4, 4, 0))
        assert f.quadlet
        assert not f.build_units
        assert not f.image_units
        assert not f.pod_units

    def test_4_8_0_image_units_enabled(self):
        f = self._features((4, 8, 0))
        assert f.quadlet
        assert f.image_units
        assert not f.pod_units
        assert not f.build_units

    def test_5_0_0_pod_units_enabled(self):
        f = self._features((5, 0, 0))
        assert f.quadlet
        assert f.image_units
        assert f.pod_units
        assert not f.build_units

    def test_5_2_0_build_units_enabled(self):
        f = self._features((5, 2, 0))
        assert f.quadlet
        assert f.image_units
        assert f.pod_units
        assert f.build_units
        assert not f.quadlet_cli
        assert not f.artifact_units
        assert not f.bundle

    def test_5_6_0_quadlet_cli_enabled(self):
        f = self._features((5, 6, 0))
        assert f.quadlet
        assert f.image_units
        assert f.pod_units
        assert f.build_units
        assert f.quadlet_cli
        assert not f.artifact_units
        assert not f.bundle

    def test_5_8_0_all_flags(self):
        f = self._features((5, 8, 0))
        assert f.quadlet
        assert f.image_units
        assert f.pod_units
        assert f.build_units
        assert f.quadlet_cli
        assert f.artifact_units
        assert f.bundle
        assert f.pasta

    def test_available_method_delegates_to_version_span(self):
        f = self._features((5, 0, 0))
        from quadletman.models.version_span import VersionSpan

        span = VersionSpan(introduced=(5, 0, 0))
        assert f.available(span)
        assert not self._features((4, 9, 0)).available(span)

    def test_value_ok_method(self):
        f = self._features((5, 0, 0))
        from quadletman.models.version_span import VersionSpan

        span = VersionSpan(introduced=(4, 4, 0), value_constraints={"image": (5, 0, 0)})
        assert f.value_ok(span, "image")
        assert not self._features((4, 9, 0)).value_ok(span, "image")

    def test_field_level_availability_via_model(self):
        """Property-level checks (formerly boolean flags) now use field_availability."""
        from quadletman.models.api import ContainerCreate, ImageCreate

        # image policy field was introduced at 5.6.0
        avail = field_availability(ImageCreate, (5, 6, 0))
        assert avail["policy"] is True
        avail_old = field_availability(ImageCreate, (5, 5, 0))
        assert avail_old["policy"] is False

        # apparmor was True at 5.8.0
        avail58 = field_availability(ContainerCreate, (5, 8, 0))
        assert avail58["apparmor_profile"] is True
        avail57 = field_availability(ContainerCreate, (5, 7, 0))
        assert avail57["apparmor_profile"] is False

    def test_vol_driver_image_via_value_availability(self):
        """driver image was True at 5.0.0 — now checked via is_value_available."""
        from quadletman.models.api import VolumeCreate
        from quadletman.models.version_span import get_version_spans

        spans = get_version_spans(VolumeCreate)
        span = spans["driver"]
        assert is_value_available(span, "image", (5, 0, 0))
        assert not is_value_available(span, "image", (4, 9, 3))

    def test_get_features_uses_subprocess(self, mocker):
        """get_features() calls subprocess.run and parses the output."""
        import subprocess

        from quadletman import podman_version

        # Clear the lru_cache so we get a fresh call
        podman_version.get_features.cache_clear()

        mock_run = mocker.patch("quadletman.podman_version.subprocess.run")
        mock_run.return_value = subprocess.CompletedProcess(
            args=["podman", "--version"],
            returncode=0,
            stdout="podman version 5.2.0",
            stderr="",
        )

        features = podman_version.get_features()
        assert features.version == (5, 2, 0)
        assert features.quadlet is True
        assert features.build_units is True
        assert features.image_units is True
        assert features.pod_units is True
        assert features.quadlet_cli is False
        assert features.artifact_units is False
        assert features.bundle is False

        # Restore cache state for other tests
        podman_version.get_features.cache_clear()

    def test_get_features_handles_missing_podman(self, mocker):
        """get_features() returns all-False flags when podman is not found."""
        from quadletman import podman_version

        podman_version.get_features.cache_clear()

        mocker.patch(
            "quadletman.podman_version.subprocess.run",
            side_effect=FileNotFoundError("podman not found"),
        )

        features = podman_version.get_features()
        assert features.version is None
        assert features.quadlet is False

        podman_version.get_features.cache_clear()


class TestReadOsRelease:
    def test_reads_os_release(self, mocker):
        from quadletman.podman_version import _read_os_release

        content = 'NAME="Ubuntu"\nVERSION_ID="22.04"\n'
        mocker.patch("builtins.open", mocker.mock_open(read_data=content))
        result = _read_os_release()
        assert "Ubuntu" in result
        assert "22.04" in result

    def test_returns_empty_on_error(self, mocker):
        from quadletman.podman_version import _read_os_release

        mocker.patch("builtins.open", side_effect=OSError)
        assert _read_os_release() == ""


class TestGetHostDistro:
    def test_from_podman_info(self, mocker):
        from quadletman import podman_version

        podman_version.get_host_distro.cache_clear()
        mocker.patch(
            "quadletman.podman_version.get_podman_info",
            return_value={"host": {"distribution": {"distribution": "Fedora", "version": "39"}}},
        )
        result = podman_version.get_host_distro()
        assert "Fedora" in result
        podman_version.get_host_distro.cache_clear()

    def test_fallback_to_os_release(self, mocker):
        from quadletman import podman_version

        podman_version.get_host_distro.cache_clear()
        mocker.patch("quadletman.podman_version.get_podman_info", return_value={})
        mocker.patch("quadletman.podman_version._read_os_release", return_value="Ubuntu 22.04")
        result = podman_version.get_host_distro()
        assert "Ubuntu" in result
        podman_version.get_host_distro.cache_clear()


class TestGetPodmanInfo:
    def test_returns_empty_on_failure(self, mocker):
        import subprocess

        from quadletman import podman_version

        podman_version._podman_info_cache = None
        podman_version._podman_info_last_attempt = 0.0
        mocker.patch(
            "quadletman.podman_version.subprocess.run",
            return_value=subprocess.CompletedProcess([], 1, stdout="", stderr="error"),
        )
        result = podman_version.get_podman_info()
        assert result == {}
        podman_version._podman_info_cache = None
        podman_version._podman_info_last_attempt = 0.0

    def test_returns_empty_on_invalid_json(self, mocker):
        import subprocess

        from quadletman import podman_version

        podman_version._podman_info_cache = None
        podman_version._podman_info_last_attempt = 0.0
        mocker.patch(
            "quadletman.podman_version.subprocess.run",
            return_value=subprocess.CompletedProcess([], 0, stdout="not json", stderr=""),
        )
        result = podman_version.get_podman_info()
        assert result == {}
        podman_version._podman_info_cache = None
        podman_version._podman_info_last_attempt = 0.0
