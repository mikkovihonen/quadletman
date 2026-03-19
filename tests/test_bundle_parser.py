"""Tests for quadletman/services/bundle_parser.py — pure parsing logic."""

from quadletman.models.sanitized import SafeMultilineStr
from quadletman.services.bundle_parser import parse_quadlets_bundle

_m = lambda v: SafeMultilineStr.trusted(v, "test fixture")  # noqa: E731

_SIMPLE_BUNDLE = """\
# FileName=web
[Container]
Image=nginx:latest
PublishPort=8080:80
Environment=KEY=value
Environment=ANOTHER=thing
Label=app=myapp
Network=mynet.network
"""

_TWO_CONTAINER_BUNDLE = """\
# FileName=web
[Container]
Image=nginx:latest
---
# FileName=app
[Container]
Image=myapp:latest
PublishPort=3000:3000
"""

_NETWORK_SECTION = """\
# FileName=web
[Container]
Image=nginx:latest
---
# FileName=mynet
[Network]
"""

_MISSING_IMAGE = """\
# FileName=noimage
[Container]
PublishPort=8080:80
"""

_WITH_VOLUME = """\
# FileName=web
[Container]
Image=nginx:latest
Volume=/data:/mnt/data:Z
"""

_WITH_DEPENDS = """\
# FileName=web
[Unit]
After=db.service redis.service
[Container]
Image=nginx:latest
"""


class TestParseSimpleBundle:
    def test_parses_single_container(self):
        result = parse_quadlets_bundle(_m(_SIMPLE_BUNDLE))
        assert len(result.containers) == 1

    def test_container_name_from_filename(self):
        result = parse_quadlets_bundle(_m(_SIMPLE_BUNDLE))
        assert result.containers[0].name == "web"

    def test_image_parsed(self):
        result = parse_quadlets_bundle(_m(_SIMPLE_BUNDLE))
        assert result.containers[0].image == "nginx:latest"

    def test_port_parsed(self):
        result = parse_quadlets_bundle(_m(_SIMPLE_BUNDLE))
        assert "8080:80" in result.containers[0].ports

    def test_multi_value_environment(self):
        result = parse_quadlets_bundle(_m(_SIMPLE_BUNDLE))
        env = result.containers[0].environment
        assert env.get("KEY") == "value"
        assert env.get("ANOTHER") == "thing"

    def test_label_parsed(self):
        result = parse_quadlets_bundle(_m(_SIMPLE_BUNDLE))
        assert result.containers[0].labels.get("app") == "myapp"

    def test_network_strips_suffix(self):
        result = parse_quadlets_bundle(_m(_SIMPLE_BUNDLE))
        assert result.containers[0].network == "mynet"

    def test_no_warnings_for_clean_bundle(self):
        result = parse_quadlets_bundle(_m(_SIMPLE_BUNDLE))
        assert result.warnings == []


class TestTwoContainerBundle:
    def test_parses_both_containers(self):
        result = parse_quadlets_bundle(_m(_TWO_CONTAINER_BUNDLE))
        assert len(result.containers) == 2

    def test_names_correct(self):
        result = parse_quadlets_bundle(_m(_TWO_CONTAINER_BUNDLE))
        names = {c.name for c in result.containers}
        assert names == {"web", "app"}


class TestSkippedSections:
    def test_network_section_skipped(self):
        result = parse_quadlets_bundle(_m(_NETWORK_SECTION))
        assert "network" in result.skipped_section_types

    def test_container_still_parsed_alongside_network(self):
        result = parse_quadlets_bundle(_m(_NETWORK_SECTION))
        assert len(result.containers) == 1


class TestMissingImage:
    def test_container_skipped_with_warning(self):
        result = parse_quadlets_bundle(_m(_MISSING_IMAGE))
        assert len(result.containers) == 0
        assert any("Image" in w for w in result.warnings)


class TestVolumeWarning:
    def test_volume_triggers_warning(self):
        result = parse_quadlets_bundle(_m(_WITH_VOLUME))
        assert len(result.containers) == 1
        container = result.containers[0]
        assert "/data:/mnt/data:Z" in container.skipped_volumes
        assert any("volume" in w.lower() for w in result.warnings)


class TestDependsOn:
    def test_depends_on_from_after(self):
        result = parse_quadlets_bundle(_m(_WITH_DEPENDS))
        assert "db" in result.containers[0].depends_on
        assert "redis" in result.containers[0].depends_on


class TestEmptyBundle:
    def test_empty_string(self):
        result = parse_quadlets_bundle(_m(""))
        assert result.containers == []
        assert result.warnings == []

    def test_only_separators(self):
        result = parse_quadlets_bundle(_m("---\n---\n"))
        assert result.containers == []


class TestPodSection:
    def test_pod_parsed_from_bundle(self):
        bundle = "# FileName=mypod\n[Pod]\nPublishPort=8080:80\n"
        result = parse_quadlets_bundle(_m(bundle))
        assert len(result.pods) == 1
        assert result.pods[0].name == "mypod"
        assert "8080:80" in result.pods[0].publish_ports

    def test_pod_network_parsed(self):
        bundle = "# FileName=mypod\n[Pod]\nNetwork=mynet.network\n"
        result = parse_quadlets_bundle(_m(bundle))
        assert result.pods[0].network == "mynet"

    def test_pod_without_filename_and_podname_skipped(self):
        bundle = "[Pod]\nPublishPort=80:80\n"
        result = parse_quadlets_bundle(_m(bundle))
        assert result.pods == []

    def test_pod_name_from_podname_field(self):
        bundle = "[Pod]\nPodName=myapp-backend\n"
        result = parse_quadlets_bundle(_m(bundle))
        assert len(result.pods) == 1
        assert result.pods[0].name == "backend"


class TestVolumeUnitSection:
    def test_volume_unit_parsed(self):
        bundle = "# FileName=data\n[Volume]\n"
        result = parse_quadlets_bundle(_m(bundle))
        assert len(result.volume_units) == 1
        assert result.volume_units[0].name == "data"

    def test_volume_unit_driver_parsed(self):
        bundle = "# FileName=data\n[Volume]\nDriver=local\nDevice=/mnt/disk\nOptions=ro\n"
        result = parse_quadlets_bundle(_m(bundle))
        vu = result.volume_units[0]
        assert vu.vol_driver == "local"
        assert vu.vol_device == "/mnt/disk"
        assert vu.vol_options == "ro"

    def test_volume_copy_false(self):
        bundle = "# FileName=data\n[Volume]\nCopy=false\n"
        result = parse_quadlets_bundle(_m(bundle))
        assert result.volume_units[0].vol_copy is False

    def test_volume_without_filename_and_volumename_skipped(self):
        bundle = "[Volume]\nDriver=local\n"
        result = parse_quadlets_bundle(_m(bundle))
        assert result.volume_units == []


class TestImageUnitSection:
    def test_image_unit_parsed(self):
        bundle = "# FileName=myimage\n[Image]\nImage=nginx:latest\n"
        result = parse_quadlets_bundle(_m(bundle))
        assert len(result.image_units) == 1
        assert result.image_units[0].name == "myimage"
        assert result.image_units[0].image == "nginx:latest"

    def test_image_unit_no_filename_adds_warning(self):
        bundle = "[Image]\nImage=nginx:latest\n"
        result = parse_quadlets_bundle(_m(bundle))
        assert result.image_units == []
        assert any("FileName" in w for w in result.warnings)

    def test_image_unit_no_image_adds_warning(self):
        bundle = "# FileName=myimg\n[Image]\n"
        result = parse_quadlets_bundle(_m(bundle))
        assert result.image_units == []
        assert any("Image" in w for w in result.warnings)


class TestContainerNameFallback:
    def test_name_from_container_name_field(self):
        bundle = "[Container]\nImage=nginx\nContainerName=myapp-web\n"
        result = parse_quadlets_bundle(_m(bundle))
        assert len(result.containers) == 1
        assert result.containers[0].name == "web"

    def test_no_name_at_all_skips(self):
        bundle = "[Container]\nImage=nginx\n"
        result = parse_quadlets_bundle(_m(bundle))
        assert result.containers == []
        assert any("no FileName" in w or "ContainerName" in w for w in result.warnings)


class TestPodAssignment:
    def test_pod_name_stripped(self):
        bundle = "# FileName=web\n[Container]\nImage=nginx\nPod=mypod.pod\n"
        result = parse_quadlets_bundle(_m(bundle))
        assert result.containers[0].pod_name == "mypod"


class TestHostNetwork:
    def test_host_network_preserved(self):
        bundle = "# FileName=x\n[Container]\nImage=foo\nNetwork=host\n"
        result = parse_quadlets_bundle(_m(bundle))
        assert result.containers[0].network == "host"
