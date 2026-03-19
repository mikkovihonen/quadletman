"""Tests for quadletman/sanitized.py — branded string types and runtime checks."""

import pytest

from quadletman.models import sanitized
from quadletman.models.sanitized import (
    SafeImageRef,
    SafePortMapping,
    SafeResourceName,
    SafeSecretName,
    SafeSlug,
    SafeStr,
    SafeTimestamp,
    SafeUnitName,
    SafeWebhookUrl,
)

# ---------------------------------------------------------------------------
# SafeStr
# ---------------------------------------------------------------------------


class TestSafeStr:
    def test_of_returns_instance(self):
        s = SafeStr.of("hello world")
        assert isinstance(s, SafeStr)
        assert isinstance(s, str)
        assert s == "hello world"

    def test_of_rejects_newline(self):
        with pytest.raises(ValueError, match="newline"):
            SafeStr.of("bad\nvalue")

    def test_of_rejects_carriage_return(self):
        with pytest.raises(ValueError):
            SafeStr.of("bad\rvalue")

    def test_of_rejects_null_byte(self):
        with pytest.raises(ValueError):
            SafeStr.of("bad\x00value")

    def test_direct_instantiation_raises(self):
        with pytest.raises(TypeError, match="of\\(\\)"):
            SafeStr("hello")

    def test_trusted_skips_validation(self):
        s = SafeStr.trusted("any value", "test fixture")
        assert isinstance(s, SafeStr)
        assert s == "any value"

    def test_empty_string_allowed(self):
        s = SafeStr.of("")
        assert s == ""


# ---------------------------------------------------------------------------
# SafeSlug
# ---------------------------------------------------------------------------


class TestSafeSlug:
    def test_of_valid_slug(self):
        s = SafeSlug.of("my-compartment")
        assert isinstance(s, SafeSlug)
        assert isinstance(s, SafeStr)
        assert s == "my-compartment"

    def test_of_single_char(self):
        assert SafeSlug.of("a") == "a"

    def test_of_rejects_uppercase(self):
        with pytest.raises(ValueError):
            SafeSlug.of("MyComp")

    def test_of_rejects_spaces(self):
        with pytest.raises(ValueError):
            SafeSlug.of("my comp")

    def test_of_rejects_leading_hyphen(self):
        with pytest.raises(ValueError):
            SafeSlug.of("-mycomp")

    def test_of_rejects_trailing_hyphen(self):
        with pytest.raises(ValueError):
            SafeSlug.of("mycomp-")

    def test_of_rejects_control_chars(self):
        with pytest.raises(ValueError):
            SafeSlug.of("my\ncomp")

    def test_of_rejects_too_long(self):
        with pytest.raises(ValueError):
            SafeSlug.of("a" * 33)

    def test_direct_instantiation_raises(self):
        with pytest.raises(TypeError):
            SafeSlug("mycomp")

    def test_trusted_bypasses_regex(self):
        # trusted() should not validate format
        s = SafeSlug.trusted("NOT A VALID SLUG", "test fixture")
        assert isinstance(s, SafeSlug)


# ---------------------------------------------------------------------------
# SafeImageRef
# ---------------------------------------------------------------------------


class TestSafeImageRef:
    def test_of_valid_image(self):
        s = SafeImageRef.of("docker.io/library/nginx:latest")
        assert isinstance(s, SafeImageRef)

    def test_of_rejects_spaces(self):
        with pytest.raises(ValueError):
            SafeImageRef.of("my image")

    def test_of_rejects_too_long(self):
        with pytest.raises(ValueError):
            SafeImageRef.of("a" * 256)

    def test_of_rejects_control_chars(self):
        with pytest.raises(ValueError):
            SafeImageRef.of("image\x00name")


# ---------------------------------------------------------------------------
# SafeUnitName
# ---------------------------------------------------------------------------


class TestSafeUnitName:
    def test_of_valid_unit(self):
        s = SafeUnitName.of("mycontainer.service")
        assert isinstance(s, SafeUnitName)

    def test_of_rejects_spaces(self):
        with pytest.raises(ValueError):
            SafeUnitName.of("my container.service")

    def test_of_rejects_shell_operators(self):
        for bad in ("unit|other", "unit&other", "unit*", "unit~"):
            with pytest.raises(ValueError):
                SafeUnitName.of(bad)


# ---------------------------------------------------------------------------
# SafeSecretName
# ---------------------------------------------------------------------------


class TestSafeSecretName:
    def test_of_valid_name(self):
        s = SafeSecretName.of("my-secret.name_1")
        assert isinstance(s, SafeSecretName)

    def test_of_rejects_leading_dot(self):
        with pytest.raises(ValueError):
            SafeSecretName.of(".hidden")

    def test_of_rejects_too_long(self):
        with pytest.raises(ValueError):
            SafeSecretName.of("a" * 254)


# ---------------------------------------------------------------------------
# SafeImageRef.trusted
# ---------------------------------------------------------------------------


class TestSafeImageRefTrusted:
    def test_trusted_skips_validation(self):
        s = SafeImageRef.trusted("NOT A VALID IMAGE!!!", "test fixture")
        assert isinstance(s, SafeImageRef)
        assert s == "NOT A VALID IMAGE!!!"

    def test_trusted_stores_reason(self):
        from quadletman.models.sanitized import _TrustedBase

        s = SafeImageRef.trusted("nginx:latest", "DB-sourced image ref")
        assert isinstance(s, _TrustedBase)
        assert s.reason == "DB-sourced image ref"


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class TestProvenance:
    def test_validated_returns_type_and_label(self):
        s = SafeSlug.of("mycomp")
        result = sanitized.provenance(s)
        assert result is not None
        type_name, label = result
        assert type_name == "SafeSlug"
        assert label.startswith("validated:")

    def test_validated_label_contains_field_name_and_callsite(self):
        s = SafeSlug.of("mycomp", "compartment_id")
        _, label = sanitized.provenance(s)
        assert "compartment_id" in label
        assert "test_sanitized.py" in label

    def test_validated_source_stored_on_instance(self):
        s = SafeSlug.of("mycomp", "compartment_id")
        assert hasattr(s, "_source")
        assert "compartment_id" in s._source

    def test_trusted_includes_reason_in_label(self):
        s = SafeSlug.trusted("mycomp", "DB-sourced compartment_id")
        result = sanitized.provenance(s)
        assert result is not None
        type_name, label = result
        assert type_name == "SafeSlug"
        assert label == "trusted:DB-sourced compartment_id"

    def test_non_branded_returns_none(self):
        assert sanitized.provenance("plain string") is None
        assert sanitized.provenance(42) is None
        assert sanitized.provenance(None) is None

    def test_safe_str_validated(self):
        s = SafeStr.of("hello")
        type_name, label = sanitized.provenance(s)
        assert type_name == "SafeStr"
        assert label.startswith("validated:")

    def test_safe_str_trusted(self):
        s = SafeStr.trusted("hello", "internally constructed")
        type_name, label = sanitized.provenance(s)
        assert type_name == "SafeStr"
        assert label == "trusted:internally constructed"

    def test_safe_unit_name_validated(self):
        s = SafeUnitName.of("mycontainer.service")
        type_name, label = sanitized.provenance(s)
        assert type_name == "SafeUnitName"
        assert label.startswith("validated:")

    def test_safe_secret_name_trusted(self):
        s = SafeSecretName.trusted("my-secret", "DB-sourced secret name")
        type_name, label = sanitized.provenance(s)
        assert type_name == "SafeSecretName"
        assert label == "trusted:DB-sourced secret name"

    def test_reason_stored_on_instance(self):
        from quadletman.models.sanitized import _TrustedBase

        s = SafeSlug.trusted("mycomp", "my reason")
        assert isinstance(s, _TrustedBase)
        assert s.reason == "my reason"

    def test_fallback_type_name_when_mro_exhausted(self, mocker):
        # Force the MRO walk to fall through to the fallback return by patching
        # issubclass so the loop condition never matches — exercises line 346.
        from quadletman.models import sanitized

        s = SafeSlug.of("mycomp")
        original_issubclass = issubclass

        def patched_issubclass(cls, bases):
            if bases is sanitized.SafeStr:
                return False
            return original_issubclass(cls, bases)

        mocker.patch("quadletman.models.sanitized.issubclass", side_effect=patched_issubclass)
        result = sanitized.provenance(s)
        assert result is not None
        _, label = result
        assert label.startswith("validated:")


# ---------------------------------------------------------------------------
# require()
# ---------------------------------------------------------------------------


class TestRequire:
    def test_passes_for_correct_type(self):
        s = SafeSlug.of("mycomp")
        sanitized.require(s, SafeSlug)  # should not raise

    def test_passes_for_subtype(self):
        s = SafeSlug.of("mycomp")
        sanitized.require(s, SafeStr)  # SafeSlug IS a SafeStr

    def test_raises_for_raw_str(self):
        with pytest.raises(TypeError, match="upstream caller must sanitize"):
            sanitized.require("mycomp", SafeSlug)

    def test_raises_for_wrong_branded_type(self):
        s = SafeStr.of("mycontainer.service")
        with pytest.raises(TypeError):
            sanitized.require(s, SafeSlug)

    def test_name_appears_in_error(self):
        with pytest.raises(TypeError, match="service_id"):
            sanitized.require("raw", SafeSlug, name="service_id")

    def test_multiple_accepted_types(self):
        s = SafeSlug.of("mycomp")
        sanitized.require(s, SafeStr, SafeSlug)  # should not raise


# ---------------------------------------------------------------------------
# Pydantic model integration — validators return branded types
# ---------------------------------------------------------------------------


class TestModelIntegration:
    def test_compartment_create_id_is_safe_slug(self):
        from quadletman.models import CompartmentCreate

        m = CompartmentCreate(id="valid-id")
        assert isinstance(m.id, SafeSlug), (
            f"CompartmentCreate.id should be SafeSlug at runtime, got {type(m.id)}"
        )

    def test_compartment_create_rejects_qm_prefix(self):
        from pydantic import ValidationError

        from quadletman.models import CompartmentCreate

        with pytest.raises(ValidationError):
            CompartmentCreate(id="qm-bad")

    def test_secret_create_name_is_safe_secret_name(self):
        from quadletman.models import SecretCreate

        m = SecretCreate(name="my-secret")
        assert isinstance(m.name, SafeSecretName), (
            f"SecretCreate.name should be SafeSecretName at runtime, got {type(m.name)}"
        )

    def test_no_control_chars_returns_safe_str(self):
        from quadletman.models import _no_control_chars

        result = _no_control_chars("clean value", "field")
        assert isinstance(result, SafeStr)


# ---------------------------------------------------------------------------
# SafeResourceName
# ---------------------------------------------------------------------------


class TestSafeResourceName:
    def test_of_valid_name(self):
        s = SafeResourceName.of("my-container")
        assert isinstance(s, SafeResourceName)
        assert isinstance(s, SafeStr)
        assert s == "my-container"

    def test_of_with_underscore(self):
        assert SafeResourceName.of("my_volume") == "my_volume"

    def test_of_single_char(self):
        assert SafeResourceName.of("a") == "a"

    def test_of_rejects_uppercase(self):
        with pytest.raises(ValueError):
            SafeResourceName.of("MyContainer")

    def test_of_rejects_leading_hyphen(self):
        with pytest.raises(ValueError):
            SafeResourceName.of("-mycontainer")

    def test_of_rejects_leading_underscore(self):
        with pytest.raises(ValueError):
            SafeResourceName.of("_mycontainer")

    def test_of_rejects_spaces(self):
        with pytest.raises(ValueError):
            SafeResourceName.of("my container")

    def test_of_rejects_too_long(self):
        with pytest.raises(ValueError):
            SafeResourceName.of("a" * 64)

    def test_of_accepts_max_length(self):
        s = SafeResourceName.of("a" * 63)
        assert len(s) == 63

    def test_of_rejects_control_chars(self):
        with pytest.raises(ValueError):
            SafeResourceName.of("my\ncontainer")

    def test_direct_instantiation_raises(self):
        with pytest.raises(TypeError):
            SafeResourceName("mycontainer")

    def test_trusted_bypasses_validation(self):
        s = SafeResourceName.trusted("NOT_VALID!!!", "test fixture")
        assert isinstance(s, SafeResourceName)
        assert s == "NOT_VALID!!!"

    def test_trusted_stores_reason(self):
        from quadletman.models.sanitized import _TrustedBase

        s = SafeResourceName.trusted("mycontainer", "DB-sourced name")
        assert isinstance(s, _TrustedBase)
        assert s.reason == "DB-sourced name"


# ---------------------------------------------------------------------------
# SafeWebhookUrl
# ---------------------------------------------------------------------------


class TestSafeWebhookUrl:
    def test_of_valid_https_url(self):
        s = SafeWebhookUrl.of("https://example.com/webhook")
        assert isinstance(s, SafeWebhookUrl)
        assert isinstance(s, SafeStr)
        assert s == "https://example.com/webhook"

    def test_of_valid_http_url(self):
        s = SafeWebhookUrl.of("http://example.com/hook")
        assert isinstance(s, SafeWebhookUrl)

    def test_of_rejects_non_url(self):
        with pytest.raises(ValueError):
            SafeWebhookUrl.of("not-a-url")

    def test_of_rejects_ftp_scheme(self):
        with pytest.raises(ValueError):
            SafeWebhookUrl.of("ftp://example.com/hook")

    def test_of_rejects_too_long(self):
        with pytest.raises(ValueError):
            SafeWebhookUrl.of("https://example.com/" + "a" * 2048)

    def test_of_rejects_control_chars(self):
        with pytest.raises(ValueError):
            SafeWebhookUrl.of("https://example.com/\x00hook")

    def test_of_rejects_whitespace_in_url(self):
        with pytest.raises(ValueError):
            SafeWebhookUrl.of("https://example.com/my hook")

    def test_direct_instantiation_raises(self):
        with pytest.raises(TypeError):
            SafeWebhookUrl("https://example.com")

    def test_trusted_bypasses_validation(self):
        s = SafeWebhookUrl.trusted("not-a-url", "test fixture")
        assert isinstance(s, SafeWebhookUrl)

    def test_trusted_stores_reason(self):
        from quadletman.models.sanitized import _TrustedBase

        s = SafeWebhookUrl.trusted("https://example.com", "DB-sourced URL")
        assert isinstance(s, _TrustedBase)
        assert s.reason == "DB-sourced URL"


# ---------------------------------------------------------------------------
# SafePortMapping
# ---------------------------------------------------------------------------


class TestSafePortMapping:
    # --- valid forms ---

    def test_of_container_port_only(self):
        s = SafePortMapping.of("80")
        assert isinstance(s, SafePortMapping)
        assert isinstance(s, SafeStr)
        assert s == "80"

    def test_of_host_container(self):
        s = SafePortMapping.of("8080:80")
        assert isinstance(s, SafePortMapping)
        assert s == "8080:80"

    def test_of_ip_host_container(self):
        s = SafePortMapping.of("127.0.0.1:8080:80")
        assert isinstance(s, SafePortMapping)

    def test_of_os_assigned_host_port(self):
        s = SafePortMapping.of(":80")
        assert isinstance(s, SafePortMapping)

    def test_of_tcp_suffix(self):
        s = SafePortMapping.of("80/tcp")
        assert isinstance(s, SafePortMapping)

    def test_of_udp_suffix_with_mapping(self):
        s = SafePortMapping.of("8080:80/udp")
        assert isinstance(s, SafePortMapping)

    # --- invalid forms ---

    def test_of_rejects_non_port(self):
        with pytest.raises(ValueError, match="port mapping"):
            SafePortMapping.of("not-a-port")

    def test_of_rejects_alpha_port(self):
        # letters are never valid as a port number
        with pytest.raises(ValueError):
            SafePortMapping.of("http:80")

    def test_of_rejects_empty_string(self):
        with pytest.raises(ValueError):
            SafePortMapping.of("")

    def test_of_rejects_control_chars(self):
        with pytest.raises(ValueError):
            SafePortMapping.of("80\x00/tcp")

    # --- trusted / direct instantiation ---

    def test_trusted_bypasses_validation(self):
        s = SafePortMapping.trusted("not-valid", "test fixture")
        assert isinstance(s, SafePortMapping)
        assert s == "not-valid"

    def test_trusted_stores_reason(self):
        from quadletman.models.sanitized import _TrustedBase

        s = SafePortMapping.trusted("8080:80", "DB-sourced port mapping")
        assert isinstance(s, _TrustedBase)
        assert s.reason == "DB-sourced port mapping"

    def test_direct_instantiation_raises(self):
        with pytest.raises(TypeError):
            SafePortMapping("80")


# ---------------------------------------------------------------------------
# SafeTimestamp
# ---------------------------------------------------------------------------


class TestSafeTimestamp:
    # --- valid forms ---

    def test_of_iso_with_T_separator(self):
        s = SafeTimestamp.of("2024-01-01T00:00:00")
        assert isinstance(s, SafeTimestamp)
        assert isinstance(s, SafeStr)
        assert s == "2024-01-01T00:00:00"

    def test_of_iso_with_space_separator(self):
        s = SafeTimestamp.of("2024-01-01 00:00:00")
        assert isinstance(s, SafeTimestamp)
        assert s == "2024-01-01 00:00:00"

    def test_of_iso_with_microseconds(self):
        s = SafeTimestamp.of("2024-01-01T00:00:00.123456")
        assert isinstance(s, SafeTimestamp)
        assert s == "2024-01-01T00:00:00.123456"

    # --- invalid forms ---

    def test_of_rejects_not_a_date(self):
        with pytest.raises(ValueError, match="ISO 8601"):
            SafeTimestamp.of("not-a-date")

    def test_of_rejects_invalid_month(self):
        with pytest.raises(ValueError):
            SafeTimestamp.of("2024-13-01")

    def test_of_rejects_empty_string(self):
        with pytest.raises(ValueError):
            SafeTimestamp.of("")

    def test_of_rejects_control_chars(self):
        with pytest.raises(ValueError):
            SafeTimestamp.of("2024-01-01\x00T00:00:00")

    # --- trusted / direct instantiation ---

    def test_trusted_bypasses_validation(self):
        s = SafeTimestamp.trusted("not-a-date", "test fixture")
        assert isinstance(s, SafeTimestamp)
        assert s == "not-a-date"

    def test_trusted_stores_reason(self):
        from quadletman.models.sanitized import _TrustedBase

        s = SafeTimestamp.trusted("2024-01-01T00:00:00", "DB row")
        assert isinstance(s, _TrustedBase)
        assert s.reason == "DB row"

    def test_direct_instantiation_raises(self):
        with pytest.raises(TypeError):
            SafeTimestamp("2024-01-01T00:00:00")
