"""Tests for quadletman/models/choices.py and field-choices infrastructure."""

from typing import Annotated

import pytest
from pydantic import BaseModel

from quadletman.models.constraints import (
    AUTO_UPDATE_POLICY_CHOICES,
    DIRECTION_CHOICES,
    EVENT_TYPE_CHOICES,
    HEALTH_ON_FAILURE_CHOICES,
    NET_DRIVER_CHOICES,
    PROTO_CHOICES,
    PULL_POLICY_CHOICES,
    RESTART_POLICY_CHOICES,
    SELINUX_CONTEXT_CHOICES,
    FieldChoice,
    FieldChoices,
    choices_to_frozenset,
)
from quadletman.models.sanitized import (
    SafeAutoUpdatePolicy,
    SafeHealthOnFailure,
    SafePullPolicy,
    SafeRestartPolicy,
)
from quadletman.models.version_span import VersionSpan, get_field_choices
from quadletman.routers.helpers.common import choices_for_template, field_choices_for_template

# ---------------------------------------------------------------------------
# FieldChoice / FieldChoices dataclass basics
# ---------------------------------------------------------------------------


class TestFieldChoiceDataclass:
    def test_frozen(self):
        ch = FieldChoice("a", "A")
        with pytest.raises(AttributeError):
            ch.value = "b"

    def test_defaults(self):
        ch = FieldChoice("x", "X")
        assert ch.is_default is False

    def test_is_default(self):
        ch = FieldChoice("x", "X", is_default=True)
        assert ch.is_default is True


class TestFieldChoicesDataclass:
    def test_frozen(self):
        fc = FieldChoices(choices=(FieldChoice("a", "A"),))
        with pytest.raises(AttributeError):
            fc.dynamic = True

    def test_defaults(self):
        fc = FieldChoices()
        assert fc.choices is None
        assert fc.default_value == ""
        assert fc.empty_label is None
        assert fc.dynamic is False


# ---------------------------------------------------------------------------
# choices_to_frozenset
# ---------------------------------------------------------------------------


class TestChoicesToFrozenset:
    def test_static_choices_no_empty(self):
        fc = FieldChoices(choices=(FieldChoice("a", "A"), FieldChoice("b", "B")))
        assert choices_to_frozenset(fc) == frozenset({"a", "b"})

    def test_includes_empty_when_empty_label(self):
        fc = FieldChoices(
            choices=(FieldChoice("a", "A"),),
            empty_label="default",
        )
        assert choices_to_frozenset(fc) == frozenset({"", "a"})

    def test_none_choices_with_empty(self):
        fc = FieldChoices(dynamic=True, empty_label="any")
        assert choices_to_frozenset(fc) == frozenset({""})

    def test_none_choices_no_empty(self):
        fc = FieldChoices(dynamic=True)
        assert choices_to_frozenset(fc) == frozenset()


# ---------------------------------------------------------------------------
# get_field_choices extraction
# ---------------------------------------------------------------------------

_TEST_FC = FieldChoices(
    choices=(FieldChoice("x", "X"), FieldChoice("y", "Y", is_default=True)),
)
_TEST_DYN = FieldChoices(dynamic=True, empty_label="any")


class _TestModel(BaseModel):
    static_field: Annotated[str, VersionSpan(introduced=(4, 4, 0)), _TEST_FC] = ""
    dynamic_field: Annotated[str, _TEST_DYN] = ""
    plain_field: str = ""


class TestGetFieldChoices:
    def test_extracts_static(self):
        fc = get_field_choices(_TestModel)
        assert "static_field" in fc
        assert fc["static_field"] is _TEST_FC

    def test_extracts_dynamic(self):
        fc = get_field_choices(_TestModel)
        assert "dynamic_field" in fc
        assert fc["dynamic_field"].dynamic is True

    def test_skips_plain(self):
        fc = get_field_choices(_TestModel)
        assert "plain_field" not in fc


# ---------------------------------------------------------------------------
# choices_for_template
# ---------------------------------------------------------------------------


class TestChoicesForTemplate:
    def test_static_choices(self):
        fc = FieldChoices(
            choices=(FieldChoice("a", "Alpha"), FieldChoice("b", "Beta", is_default=True)),
        )
        result = choices_for_template(fc)
        assert len(result) == 2
        assert result[0]["value"] == "a"
        assert result[0]["label"] == "Alpha"
        assert result[0]["is_default"] is False
        assert result[1]["is_default"] is True
        assert all(r["available"] is True for r in result)

    def test_empty_label_prepended(self):
        fc = FieldChoices(
            choices=(FieldChoice("a", "A"),),
            empty_label="none",
        )
        result = choices_for_template(fc)
        assert len(result) == 2
        assert result[0]["value"] == ""
        assert result[0]["label"] == "none"

    def test_current_value_overrides_default(self):
        fc = FieldChoices(
            choices=(FieldChoice("a", "A", is_default=True), FieldChoice("b", "B")),
        )
        result = choices_for_template(fc, current_value="b")
        assert result[0]["is_default"] is False
        assert result[1]["is_default"] is True

    def test_dynamic_items(self):
        fc = FieldChoices(dynamic=True, empty_label="any")
        result = choices_for_template(fc, dynamic_items=["foo", "bar"])
        assert len(result) == 3  # empty + 2 items
        assert result[0]["value"] == ""
        assert result[1]["value"] == "foo"
        assert result[1]["label"] == "foo"
        assert result[2]["value"] == "bar"

    def test_version_gated_value(self):
        fc = FieldChoices(dynamic=True, empty_label="default")
        span = VersionSpan(
            introduced=(4, 4, 0),
            value_constraints={"special": (5, 0, 0)},
        )
        result = choices_for_template(
            fc,
            dynamic_items=["normal", "special"],
            version=(4, 8, 0),
            version_span=span,
        )
        normal = next(r for r in result if r["value"] == "normal")
        special = next(r for r in result if r["value"] == "special")
        assert normal["available"] is True
        assert special["available"] is False
        assert "5.0.0" in special["tooltip"]

    def test_empty_dynamic_items(self):
        fc = FieldChoices(dynamic=True, empty_label="default")
        result = choices_for_template(fc)
        assert len(result) == 1  # just the empty option


# ---------------------------------------------------------------------------
# field_choices_for_template
# ---------------------------------------------------------------------------


class TestFieldChoicesForTemplate:
    def test_returns_static_only(self):
        result = field_choices_for_template(_TestModel, version=(5, 0, 0))
        assert "static_field" in result
        assert "dynamic_field" not in result
        assert "plain_field" not in result

    def test_choice_values(self):
        result = field_choices_for_template(_TestModel, version=(5, 0, 0))
        values = [c["value"] for c in result["static_field"]]
        assert values == ["x", "y"]


# ---------------------------------------------------------------------------
# Branded type round-trip: every choice value passes .of()
# ---------------------------------------------------------------------------


class TestBrandedTypeRoundTrip:
    @pytest.mark.parametrize(
        "choices,brand_cls",
        [
            (RESTART_POLICY_CHOICES, SafeRestartPolicy),
            (PULL_POLICY_CHOICES, SafePullPolicy),
            (AUTO_UPDATE_POLICY_CHOICES, SafeAutoUpdatePolicy),
            (HEALTH_ON_FAILURE_CHOICES, SafeHealthOnFailure),
        ],
    )
    def test_all_choice_values_pass_of(self, choices, brand_cls):
        allowed = choices_to_frozenset(choices)
        for val in allowed:
            brand_cls.of(val, "test")

    @pytest.mark.parametrize(
        "brand_cls",
        [SafeRestartPolicy, SafePullPolicy, SafeAutoUpdatePolicy, SafeHealthOnFailure],
    )
    def test_invalid_value_rejected(self, brand_cls):
        with pytest.raises(ValueError):
            brand_cls.of("__invalid__", "test")


# ---------------------------------------------------------------------------
# Constants sanity checks
# ---------------------------------------------------------------------------


class TestConstants:
    def test_all_constants_are_frozen(self):
        for fc in [
            RESTART_POLICY_CHOICES,
            PULL_POLICY_CHOICES,
            AUTO_UPDATE_POLICY_CHOICES,
            HEALTH_ON_FAILURE_CHOICES,
            EVENT_TYPE_CHOICES,
            SELINUX_CONTEXT_CHOICES,
            NET_DRIVER_CHOICES,
            DIRECTION_CHOICES,
            PROTO_CHOICES,
        ]:
            assert isinstance(fc, FieldChoices)
            with pytest.raises(AttributeError):
                fc.dynamic = True

    def test_restart_has_default(self):
        defaults = [c for c in RESTART_POLICY_CHOICES.choices if c.is_default]
        assert len(defaults) == 1
        assert defaults[0].value == "always"

    def test_event_type_has_default(self):
        defaults = [c for c in EVENT_TYPE_CHOICES.choices if c.is_default]
        assert len(defaults) == 1
        assert defaults[0].value == "on_failure"

    def test_proto_includes_icmp(self):
        values = {c.value for c in PROTO_CHOICES.choices}
        assert "icmp" in values
