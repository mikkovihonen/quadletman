"""Tests for quadletman/models/version_span.py — VersionSpan metadata and utilities."""

import pytest
from pydantic import BaseModel

from quadletman.models.version_span import (
    VersionSpan,
    field_availability,
    field_tooltip,
    get_version_spans,
    is_field_available,
    is_field_deprecated,
    is_value_available,
    value_availability,
    value_tooltip,
)
from quadletman.routers.helpers.common import validate_version_spans

# ---------------------------------------------------------------------------
# is_field_available
# ---------------------------------------------------------------------------


class TestIsFieldAvailable:
    def test_none_version_unavailable(self):
        span = VersionSpan(introduced=(5, 0, 0))
        assert not is_field_available(span, None)

    def test_below_introduced_unavailable(self):
        span = VersionSpan(introduced=(5, 0, 0))
        assert not is_field_available(span, (4, 9, 3))

    def test_at_introduced_available(self):
        span = VersionSpan(introduced=(5, 0, 0))
        assert is_field_available(span, (5, 0, 0))

    def test_above_introduced_available(self):
        span = VersionSpan(introduced=(5, 0, 0))
        assert is_field_available(span, (5, 8, 0))

    def test_at_removed_unavailable(self):
        span = VersionSpan(introduced=(4, 4, 0), removed=(6, 0, 0))
        assert not is_field_available(span, (6, 0, 0))

    def test_below_removed_available(self):
        span = VersionSpan(introduced=(4, 4, 0), removed=(6, 0, 0))
        assert is_field_available(span, (5, 9, 0))

    def test_above_removed_unavailable(self):
        span = VersionSpan(introduced=(4, 4, 0), removed=(6, 0, 0))
        assert not is_field_available(span, (7, 0, 0))


# ---------------------------------------------------------------------------
# is_field_deprecated
# ---------------------------------------------------------------------------


class TestIsFieldDeprecated:
    def test_no_deprecation(self):
        span = VersionSpan(introduced=(5, 0, 0))
        assert not is_field_deprecated(span, (5, 0, 0))

    def test_none_version(self):
        span = VersionSpan(introduced=(4, 0, 0), deprecated=(5, 0, 0))
        assert not is_field_deprecated(span, None)

    def test_below_deprecated(self):
        span = VersionSpan(introduced=(4, 0, 0), deprecated=(5, 0, 0))
        assert not is_field_deprecated(span, (4, 9, 0))

    def test_at_deprecated(self):
        span = VersionSpan(introduced=(4, 0, 0), deprecated=(5, 0, 0))
        assert is_field_deprecated(span, (5, 0, 0))

    def test_deprecated_but_removed(self):
        span = VersionSpan(introduced=(4, 0, 0), deprecated=(5, 0, 0), removed=(6, 0, 0))
        assert not is_field_deprecated(span, (6, 0, 0))

    def test_deprecated_between_deprecated_and_removed(self):
        span = VersionSpan(introduced=(4, 0, 0), deprecated=(5, 0, 0), removed=(6, 0, 0))
        assert is_field_deprecated(span, (5, 5, 0))


# ---------------------------------------------------------------------------
# is_value_available
# ---------------------------------------------------------------------------


class TestIsValueAvailable:
    def test_no_constraints_falls_back_to_field(self):
        span = VersionSpan(introduced=(4, 4, 0))
        assert is_value_available(span, "anything", (4, 4, 0))
        assert not is_value_available(span, "anything", (4, 3, 0))

    def test_constrained_value_below_minimum(self):
        span = VersionSpan(introduced=(4, 4, 0), value_constraints={"image": (5, 0, 0)})
        assert not is_value_available(span, "image", (4, 9, 0))

    def test_constrained_value_at_minimum(self):
        span = VersionSpan(introduced=(4, 4, 0), value_constraints={"image": (5, 0, 0)})
        assert is_value_available(span, "image", (5, 0, 0))

    def test_unconstrained_value_available(self):
        span = VersionSpan(introduced=(4, 4, 0), value_constraints={"image": (5, 0, 0)})
        assert is_value_available(span, "local", (4, 4, 0))

    def test_none_version_unavailable(self):
        span = VersionSpan(introduced=(4, 4, 0), value_constraints={"image": (5, 0, 0)})
        assert not is_value_available(span, "image", None)

    def test_field_unavailable_value_also_unavailable(self):
        span = VersionSpan(introduced=(5, 0, 0), value_constraints={"x": (5, 0, 0)})
        assert not is_value_available(span, "x", (4, 9, 0))


# ---------------------------------------------------------------------------
# get_version_spans (Annotated metadata extraction)
# ---------------------------------------------------------------------------


class TestGetVersionSpans:
    def test_extracts_annotated_spans(self):
        from typing import Annotated

        from quadletman.models.sanitized import SafeStr, enforce_model_safety

        @enforce_model_safety
        class TestModel(BaseModel):
            field_a: Annotated[SafeStr, VersionSpan(introduced=(5, 0, 0), quadlet_key="A")]
            field_b: SafeStr = SafeStr.trusted("", "default")

        spans = get_version_spans(TestModel)
        assert "field_a" in spans
        assert "field_b" not in spans
        assert spans["field_a"].introduced == (5, 0, 0)
        assert spans["field_a"].quadlet_key == "A"

    def test_extracts_from_real_models(self):
        from quadletman.models.api import ContainerCreate, ImageCreate, VolumeCreate

        cs = get_version_spans(ContainerCreate)
        assert "apparmor_profile" in cs
        assert cs["apparmor_profile"].introduced == (5, 8, 0)

        iu = get_version_spans(ImageCreate)
        assert "policy" in iu
        assert iu["policy"].introduced == (5, 6, 0)

        vs = get_version_spans(VolumeCreate)
        assert "driver" in vs
        assert vs["driver"].value_constraints == {"image": (5, 0, 0)}


# ---------------------------------------------------------------------------
# field_availability / value_availability
# ---------------------------------------------------------------------------


class TestAvailabilityDicts:
    def test_field_availability(self):
        from quadletman.models.api import ContainerCreate

        avail = field_availability(ContainerCreate, (5, 7, 0))
        assert avail["apparmor_profile"] is False

        avail2 = field_availability(ContainerCreate, (5, 8, 0))
        assert avail2["apparmor_profile"] is True

    def test_field_availability_none_version(self):
        from quadletman.models.api import ContainerCreate

        avail = field_availability(ContainerCreate, None)
        assert avail["apparmor_profile"] is False

    def test_value_availability(self):
        from quadletman.models.api import VolumeCreate

        va = value_availability(VolumeCreate, (4, 9, 0))
        assert va["driver"]["image"] is False

        va2 = value_availability(VolumeCreate, (5, 0, 0))
        assert va2["driver"]["image"] is True


# ---------------------------------------------------------------------------
# field_tooltip / value_tooltip
# ---------------------------------------------------------------------------


class TestTooltips:
    def test_unavailable_tooltip(self):
        span = VersionSpan(introduced=(5, 8, 0))
        tip = field_tooltip(span, (5, 7, 0))
        assert "5.8.0+" in tip
        assert "5.7.0" in tip

    def test_available_tooltip_empty(self):
        span = VersionSpan(introduced=(5, 0, 0))
        assert field_tooltip(span, (5, 0, 0)) == ""

    def test_deprecated_tooltip(self):
        span = VersionSpan(
            introduced=(4, 4, 0),
            deprecated=(6, 0, 0),
            deprecation_message="Use NewKey= instead",
        )
        tip = field_tooltip(span, (6, 0, 0))
        assert "Deprecated" in tip
        assert "NewKey=" in tip

    def test_removed_tooltip(self):
        span = VersionSpan(
            introduced=(4, 4, 0),
            removed=(7, 0, 0),
            deprecation_message="Use NewKey= instead",
        )
        tip = field_tooltip(span, (7, 0, 0))
        assert "Removed" in tip
        assert "NewKey=" in tip

    def test_value_tooltip_constrained(self):
        span = VersionSpan(introduced=(4, 4, 0), value_constraints={"image": (5, 0, 0)})
        tip = value_tooltip(span, "image", (4, 9, 0))
        assert "5.0.0+" in tip
        assert "4.9.0" in tip

    def test_value_tooltip_available(self):
        span = VersionSpan(introduced=(4, 4, 0), value_constraints={"image": (5, 0, 0)})
        assert value_tooltip(span, "image", (5, 0, 0)) == ""

    def test_value_tooltip_unconstrained(self):
        span = VersionSpan(introduced=(4, 4, 0), value_constraints={"image": (5, 0, 0)})
        assert value_tooltip(span, "local", (4, 4, 0)) == ""


# ---------------------------------------------------------------------------
# validate_version_spans
# ---------------------------------------------------------------------------


class TestValidateVersionSpans:
    def test_default_values_pass(self):
        from quadletman.models.api import ContainerCreate

        model = ContainerCreate(qm_name="web", image="nginx:latest")
        # Should not raise — all fields at defaults
        validate_version_spans(model, (4, 4, 0), "4.4.0")

    def test_unsupported_field_raises(self):
        from fastapi import HTTPException

        from quadletman.models.api import ContainerCreate
        from quadletman.models.sanitized import SafeStr

        model = ContainerCreate(
            qm_name="web",
            image="nginx:latest",
            apparmor_profile=SafeStr.of("myprofile", "test"),
        )
        with pytest.raises(HTTPException) as exc_info:
            validate_version_spans(model, (5, 7, 0), "5.7.0")
        assert exc_info.value.status_code == 400
        assert "AppArmor" in exc_info.value.detail
        assert "5.8.0" in exc_info.value.detail

    def test_supported_field_passes(self):
        from quadletman.models.api import ContainerCreate
        from quadletman.models.sanitized import SafeStr

        model = ContainerCreate(
            qm_name="web",
            image="nginx:latest",
            apparmor_profile=SafeStr.of("myprofile", "test"),
        )
        # Should not raise on 5.8.0+
        validate_version_spans(model, (5, 8, 0), "5.8.0")

    def test_unsupported_value_raises(self):
        from fastapi import HTTPException

        from quadletman.models.api import VolumeCreate
        from quadletman.models.sanitized import SafeStr

        model = VolumeCreate(qm_name="myvol", driver=SafeStr.of("image", "test"))
        with pytest.raises(HTTPException) as exc_info:
            validate_version_spans(model, (4, 9, 0), "4.9.0")
        assert exc_info.value.status_code == 400
        assert "image" in exc_info.value.detail
        assert "5.0.0" in exc_info.value.detail

    def test_supported_value_passes(self):
        from quadletman.models.api import VolumeCreate
        from quadletman.models.sanitized import SafeStr

        model = VolumeCreate(qm_name="myvol", driver=SafeStr.of("image", "test"))
        validate_version_spans(model, (5, 0, 0), "5.0.0")

    def test_none_version_unsupported_field_raises(self):
        from fastapi import HTTPException

        from quadletman.models.api import ImageCreate

        model = ImageCreate(
            qm_name="myimg",
            image="nginx:latest",
            containers_conf_module="/etc/containers/conf.d/custom.conf",
        )
        with pytest.raises(HTTPException) as exc_info:
            validate_version_spans(model, None, "unknown")
        assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# Feature-level constants
# ---------------------------------------------------------------------------


class TestFeatureConstants:
    def test_build_units_version_fixed(self):
        from quadletman.models.version_span import BUILD_UNITS

        assert BUILD_UNITS.introduced == (5, 2, 0)

    def test_image_units_version(self):
        from quadletman.models.version_span import IMAGE_UNITS

        assert IMAGE_UNITS.introduced == (4, 8, 0)

    def test_pod_units_version(self):
        from quadletman.models.version_span import POD_UNITS

        assert POD_UNITS.introduced == (5, 0, 0)

    def test_artifact_units_version(self):
        from quadletman.models.version_span import ARTIFACT_UNITS

        assert ARTIFACT_UNITS.introduced == (5, 7, 0)

    def test_kube_units_version(self):
        from quadletman.models.version_span import KUBE_UNITS

        assert KUBE_UNITS.introduced == (4, 4, 0)


# ---------------------------------------------------------------------------
# Model VersionSpan counts
# ---------------------------------------------------------------------------


class TestModelVersionSpanCounts:
    def test_container_create_span_count(self):
        from quadletman.models.api import ContainerCreate

        spans = get_version_spans(ContainerCreate)
        assert len(spans) >= 65  # comprehensive coverage (build fields moved to BuildCreate)

    def test_pod_create_span_count(self):
        from quadletman.models.api import PodCreate

        spans = get_version_spans(PodCreate)
        assert len(spans) >= 15

    def test_image_unit_create_span_count(self):
        from quadletman.models.api import ImageCreate

        spans = get_version_spans(ImageCreate)
        assert len(spans) >= 8

    def test_kube_create_span_count(self):
        from quadletman.models.api import KubeCreate

        spans = get_version_spans(KubeCreate)
        assert len(spans) >= 8
