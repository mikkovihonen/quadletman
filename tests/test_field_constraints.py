"""Tests for FieldConstraints annotation infrastructure."""

from typing import Annotated

import pytest
from pydantic import BaseModel

from quadletman.models.constraints import (
    IMAGE_REF_CN,
    PORT_NUMBER_CN,
    RESOURCE_NAME_CN,
    SECRET_NAME_CN,
    SLUG_CN,
    WEBHOOK_URL_CN,
    FieldConstraints,
)
from quadletman.models.version_span import VersionSpan, get_field_constraints
from quadletman.routers.helpers.common import field_constraints_for_template

# ---------------------------------------------------------------------------
# FieldConstraints dataclass basics
# ---------------------------------------------------------------------------


class TestFieldConstraintsDataclass:
    def test_frozen(self):
        fc = FieldConstraints(maxlength=63)
        with pytest.raises(AttributeError):
            fc.maxlength = 100

    def test_defaults_all_none(self):
        fc = FieldConstraints()
        assert fc.min is None
        assert fc.max is None
        assert fc.step is None
        assert fc.minlength is None
        assert fc.maxlength is None
        assert fc.html_pattern is None
        assert fc.placeholder is None
        assert fc.label_hint is None

    def test_all_fields_set(self):
        fc = FieldConstraints(
            min=1,
            max=65535,
            step=1,
            minlength=1,
            maxlength=5,
            html_pattern="\\d+",
            placeholder="8080",
            label_hint="1–65535",
        )
        assert fc.min == 1
        assert fc.max == 65535
        assert fc.maxlength == 5
        assert fc.html_pattern == "\\d+"


# ---------------------------------------------------------------------------
# get_field_constraints extraction
# ---------------------------------------------------------------------------

_TEST_CN = FieldConstraints(maxlength=63, html_pattern="[a-z]+")


class _TestModel(BaseModel):
    constrained: Annotated[str, VersionSpan(introduced=(4, 4, 0)), _TEST_CN] = ""
    plain: str = ""
    only_version: Annotated[str, VersionSpan(introduced=(5, 0, 0))] = ""


class TestGetFieldConstraints:
    def test_extracts_constrained(self):
        result = get_field_constraints(_TestModel)
        assert "constrained" in result
        assert result["constrained"].maxlength == 63

    def test_skips_plain(self):
        assert "plain" not in get_field_constraints(_TestModel)

    def test_skips_version_only(self):
        assert "only_version" not in get_field_constraints(_TestModel)


# ---------------------------------------------------------------------------
# field_constraints_for_template
# ---------------------------------------------------------------------------


class TestFieldConstraintsForTemplate:
    def test_returns_non_none_attrs_only(self):
        result = field_constraints_for_template(_TestModel)
        assert "constrained" in result
        attrs = result["constrained"]
        assert "maxlength" in attrs
        assert "pattern" in attrs  # html_pattern → pattern
        assert attrs["maxlength"] == 63
        assert attrs["pattern"] == "[a-z]+"
        # None fields should not be present
        assert "min" not in attrs
        assert "max" not in attrs
        assert "placeholder" not in attrs

    def test_maps_html_pattern_to_pattern(self):
        result = field_constraints_for_template(_TestModel)
        assert "html_pattern" not in result["constrained"]
        assert "pattern" in result["constrained"]

    def test_skips_unconstrained(self):
        result = field_constraints_for_template(_TestModel)
        assert "plain" not in result
        assert "only_version" not in result


# ---------------------------------------------------------------------------
# Shared constants sanity checks
# ---------------------------------------------------------------------------


class TestSharedConstants:
    def test_resource_name_cn(self):
        assert RESOURCE_NAME_CN.maxlength == 63
        assert RESOURCE_NAME_CN.html_pattern is not None

    def test_secret_name_cn(self):
        assert SECRET_NAME_CN.maxlength == 253
        assert SECRET_NAME_CN.html_pattern is not None

    def test_slug_cn(self):
        assert SLUG_CN.maxlength == 32
        assert SLUG_CN.html_pattern is not None

    def test_image_ref_cn(self):
        assert IMAGE_REF_CN.maxlength == 255

    def test_webhook_url_cn(self):
        assert WEBHOOK_URL_CN.maxlength == 2048

    def test_port_number_cn(self):
        assert PORT_NUMBER_CN.min == 1
        assert PORT_NUMBER_CN.max == 65535

    def test_all_frozen(self):
        for cn in [
            RESOURCE_NAME_CN,
            SECRET_NAME_CN,
            SLUG_CN,
            IMAGE_REF_CN,
            WEBHOOK_URL_CN,
            PORT_NUMBER_CN,
        ]:
            with pytest.raises(AttributeError):
                cn.maxlength = 999


# ---------------------------------------------------------------------------
# Integration: real models have constraints extracted
# ---------------------------------------------------------------------------


class TestRealModelExtraction:
    def test_container_create_has_constraints(self):
        from quadletman.models.api.container import ContainerCreate

        result = get_field_constraints(ContainerCreate)
        assert "name" in result
        assert "memory_limit" in result
        assert "health_retries" in result
        assert result["name"].maxlength == 63
        assert result["memory_limit"].placeholder is not None

    def test_volume_create_has_name_constraint(self):
        from quadletman.models.api.volume import VolumeCreate

        result = get_field_constraints(VolumeCreate)
        assert "name" in result
        assert result["name"].maxlength == 63

    def test_timer_create_has_constraints(self):
        from quadletman.models.api.timer import TimerCreate

        result = get_field_constraints(TimerCreate)
        assert "name" in result
        assert "on_calendar" in result
        assert result["on_calendar"].placeholder is not None

    def test_compartment_create_has_slug_constraint(self):
        from quadletman.models.api.compartment import CompartmentCreate

        result = get_field_constraints(CompartmentCreate)
        assert "id" in result
        assert result["id"].maxlength == 32
