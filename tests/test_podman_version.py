"""Tests for quadletman/podman_version.py — version parsing and feature flags."""

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
            quadlet=version is not None and version >= (4, 4, 0),
            build_units=version is not None and version >= (4, 5, 0),
            image_pull_policy=version is not None and version >= (5, 0, 0),
            apparmor=version is not None and version >= (5, 8, 0),
            bundle=version is not None and version >= (5, 8, 0),
            pasta=version is not None and version >= (4, 1, 0),
        )

    def test_none_version_all_flags_false(self):
        f = self._features(None)
        assert not f.quadlet
        assert not f.build_units
        assert not f.image_pull_policy
        assert not f.apparmor
        assert not f.bundle
        assert not f.pasta
        assert f.version_str == "unknown"

    def test_4_3_0_no_quadlet(self):
        f = self._features((4, 3, 0))
        assert not f.quadlet
        assert not f.build_units
        assert f.pasta  # >= 4.1

    def test_4_4_0_quadlet_enabled(self):
        f = self._features((4, 4, 0))
        assert f.quadlet
        assert not f.build_units

    def test_4_9_3_no_image_pull_policy(self):
        f = self._features((4, 9, 3))
        assert f.quadlet
        assert f.build_units
        assert not f.image_pull_policy

    def test_5_0_0_image_pull_policy_enabled(self):
        f = self._features((5, 0, 0))
        assert f.image_pull_policy

    def test_4_5_0_build_units_enabled(self):
        f = self._features((4, 5, 0))
        assert f.quadlet
        assert f.build_units
        assert not f.image_pull_policy
        assert not f.apparmor
        assert not f.bundle

    def test_5_8_0_all_flags(self):
        f = self._features((5, 8, 0))
        assert f.quadlet
        assert f.build_units
        assert f.apparmor
        assert f.bundle
        assert f.pasta

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
        assert features.apparmor is False

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
