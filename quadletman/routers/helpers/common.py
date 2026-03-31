"""Helpers shared across multiple domain routers."""

import asyncio
import contextvars
import grp
import json
import logging
import os
import pwd
import re
import tomllib
from collections.abc import Sequence
from typing import Any

from fastapi import Cookie, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...config.settings import settings
from ...db.engine import get_db
from ...i18n import gettext as _t
from ...models import sanitized
from ...models.api import VolumeCreate
from ...models.constraints import FieldChoices
from ...models.sanitized import SafeSlug, SafeStr, SafeUsername
from ...models.service import UploadableFieldMeta
from ...models.version_span import (
    PodmanVersion,
    VersionSpan,
    _fmt_version,
    get_field_choices,
    get_field_constraints,
    get_version_spans,
    is_field_available,
    is_field_deprecated,
    is_value_available,
    value_tooltip,
)
from ...podman import get_features, get_network_drivers, get_volume_drivers
from ...security import session as session_store
from ...security.auth import NotAuthenticated
from ...services import compartment_manager, metrics, systemd_manager, user_manager
from ...utils import dir_size, fmt_bytes

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum size for file uploads (archive restore + single file upload).
MAX_UPLOAD_BYTES = settings.max_upload_bytes

# Config files (env, seccomp, containers.conf, auth, etc.) — 64 KiB default.
MAX_CONFIG_FILE_BYTES = settings.max_config_file_bytes
MAX_ENVFILE_BYTES = MAX_CONFIG_FILE_BYTES  # backward-compat alias

# Allowed exec_user values for the terminal WebSocket: "root" or a non-negative integer UID.
EXEC_USER_RE = re.compile(r"^(root|\d+)$")

# ---------------------------------------------------------------------------
# FieldChoices → template-ready conversion
# ---------------------------------------------------------------------------


def choices_for_template(
    fc: FieldChoices,
    current_value: str = "",
    version: PodmanVersion | None = None,
    version_span: VersionSpan | None = None,
    dynamic_items: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Convert a ``FieldChoices`` annotation into a template-ready list.

    Returns a list of dicts, each with keys:
    ``{value, label, is_default, available, tooltip}``.

    For **static** fields (``fc.dynamic is False``): uses ``fc.choices``.
    For **dynamic** fields: uses *dynamic_items* (value == label).

    The *version_span* parameter enables per-value version gating via
    ``VersionSpan.value_constraints``.
    """
    result: list[dict[str, Any]] = []

    # Prepend the empty option if configured.
    if fc.empty_label is not None:
        is_sel = (not current_value) and fc.default_value == ""
        result.append(
            {
                "value": "",
                "label": fc.empty_label,
                "is_default": is_sel,
                "available": True,
                "tooltip": "",
            }
        )

    if fc.dynamic:
        # Dynamic choices: build entries from the runtime item list.
        for item in dynamic_items or ():
            avail = True
            tip = ""
            if version_span:
                avail = is_value_available(version_span, item, version)
                if not avail:
                    tip = value_tooltip(version_span, item, version)
            is_sel = (current_value == item) or (not current_value and fc.default_value == item)
            result.append(
                {
                    "value": item,
                    "label": item,
                    "is_default": is_sel,
                    "available": avail,
                    "tooltip": tip,
                }
            )
    else:
        # Static choices: use the pre-defined entries.
        for ch in fc.choices or ():
            avail = True
            tip = ""
            if version_span:
                avail = is_value_available(version_span, ch.value, version)
                if not avail:
                    tip = value_tooltip(version_span, ch.value, version)
            is_sel = (current_value == ch.value) or (
                not current_value and (ch.is_default or fc.default_value == ch.value)
            )
            result.append(
                {
                    "value": ch.value,
                    "label": ch.label,
                    "is_default": is_sel,
                    "available": avail,
                    "tooltip": tip,
                }
            )

    return result


def field_choices_for_template(
    model_cls: type[BaseModel],
    version: PodmanVersion | None,
) -> dict[str, list[dict[str, Any]]]:
    """Return ``{field_name: [choice_dicts]}`` for all **static** ``FieldChoices`` fields.

    Dynamic fields (``fc.dynamic is True``) are skipped — their choices are
    populated per-request via ``choices_for_template()`` with *dynamic_items*.
    """
    spans = get_version_spans(model_cls)
    result: dict[str, list[dict[str, Any]]] = {}
    for name, fc in get_field_choices(model_cls).items():
        if fc.dynamic:
            continue
        result[name] = choices_for_template(
            fc,
            version=version,
            version_span=spans.get(name),
        )
    return result


def field_constraints_for_template(
    model_cls: type[BaseModel],
) -> dict[str, dict[str, Any]]:
    """Return ``{field_name: {attr: value}}`` for all ``FieldConstraints`` fields.

    Each inner dict contains only the non-None constraint attributes, ready
    for direct use in template HTML attributes.  The ``html_pattern`` key is
    mapped to ``pattern`` in the output for HTML attribute compatibility.
    """
    result: dict[str, dict[str, Any]] = {}
    for name, fc in get_field_constraints(model_cls).items():
        attrs: dict[str, Any] = {}
        if fc.min is not None:
            attrs["min"] = fc.min
        if fc.max is not None:
            attrs["max"] = fc.max
        if fc.step is not None:
            attrs["step"] = fc.step
        if fc.minlength is not None:
            attrs["minlength"] = fc.minlength
        if fc.maxlength is not None:
            attrs["maxlength"] = fc.maxlength
        if fc.html_pattern is not None:
            attrs["pattern"] = fc.html_pattern
        if fc.placeholder is not None:
            attrs["placeholder"] = fc.placeholder
        if fc.label_hint is not None:
            attrs["label_hint"] = fc.label_hint
        if fc.description is not None:
            attrs["description"] = fc.description
        if fc.pattern_error is not None:
            attrs["pattern_error"] = fc.pattern_error
        if attrs:
            result[name] = attrs
    return result


# ---------------------------------------------------------------------------
# HTMX detection
# ---------------------------------------------------------------------------


def is_htmx(request: Request) -> bool:
    return request.headers.get("HX-Request") == "true"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


async def get_vol_sizes(compartment_id: SafeSlug, volumes) -> dict[str, str]:
    """Compute formatted sizes for all host-backed volumes concurrently."""
    host_vols = [v for v in volumes if not v.qm_use_quadlet]
    if not host_vols:
        return {}
    loop = asyncio.get_event_loop()
    sizes = await asyncio.gather(
        *[
            loop.run_in_executor(
                None,
                dir_size,
                os.path.join(metrics._VOLUMES_BASE, compartment_id, v.qm_name),
            )
            for v in host_vols
        ]
    )
    return {v.qm_name: fmt_bytes(s) for v, s in zip(host_vols, sizes, strict=False)}


# ---------------------------------------------------------------------------
# HTMX response helpers
# ---------------------------------------------------------------------------


async def run_blocking(fn, *args):
    """Run a blocking function in the default thread-pool executor.

    Uses ``copy_context().run()`` so that request-scoped ContextVars
    (e.g. admin credentials for ``host.*`` privilege escalation) are
    visible inside the executor thread.
    """
    ctx = contextvars.copy_context()
    return await asyncio.get_event_loop().run_in_executor(None, ctx.run, fn, *args)


def toast_trigger(message: str, *, error: bool = False) -> dict[str, str]:
    """Return an HX-Trigger header dict for a showToast notification."""
    return {
        "HX-Trigger": json.dumps(
            {"showToast": message, "toastType": "error" if error else "success"}
        )
    }


# ---------------------------------------------------------------------------
# Version-span validation (moved from models.version_span to keep models
# layer free from FastAPI dependency)
# ---------------------------------------------------------------------------

_vs_logger = logging.getLogger("quadletman.models.version_span")


def validate_version_spans(
    model: BaseModel,
    version: PodmanVersion | None,
    version_str: str,
) -> None:
    """Validate all version-gated fields on *model* against *version*.

    Raises ``HTTPException(400)`` if any version-gated field is set to a
    non-default value on an unsupported Podman version.  Logs a warning for
    deprecated fields.

    Also checks ``value_constraints`` — e.g. ``vol_driver="image"`` on
    Podman < 5.0.
    """
    spans = get_version_spans(type(model))
    defaults = {name: info.default for name, info in type(model).model_fields.items()}

    for field_name, span in spans.items():
        value = getattr(model, field_name)
        default = defaults.get(field_name)

        if value == default:
            continue

        if not is_field_available(span, version):
            key_label = span.quadlet_key or field_name
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Field '{key_label}' requires Podman "
                    f"{_fmt_version(span.introduced)}+ "
                    f"(detected: {version_str})"
                ),
            )

        if is_field_deprecated(span, version):
            _vs_logger.warning(
                "Field '%s' is deprecated in Podman %s: %s",
                field_name,
                version_str,
                span.deprecation_message or "(no migration guidance)",
            )

        if span.value_constraints and isinstance(value, str) and value in span.value_constraints:
            min_ver = span.value_constraints[value]
            if version is None or version < min_ver:
                key_label = span.quadlet_key or field_name
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Value '{value}' for '{key_label}' requires Podman "
                        f"{_fmt_version(min_ver)}+ "
                        f"(detected: {version_str})"
                    ),
                )


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


@sanitized.enforce
def _user_in_allowed_group(username: SafeUsername) -> bool:
    try:
        user_groups = {g.gr_name for g in grp.getgrall() if username in g.gr_mem}
        # also include primary group
        pw_entry = pwd.getpwnam(username)
        primary_group = grp.getgrgid(pw_entry.pw_gid).gr_name
        user_groups.add(primary_group)
        return bool(user_groups & set(settings.allowed_groups))
    except KeyError:
        return False


def require_auth(request: Request, qm_session: str = Cookie(default=None)) -> SafeUsername:
    """FastAPI dependency — validates session cookie and returns the authenticated username."""
    if settings.test_auth_user:
        logger.critical(
            "SECURITY: test auth bypass active — request %s %s authenticated as %r without PAM",
            request.method,
            request.url.path,
            settings.test_auth_user,
        )
        return SafeUsername.trusted(settings.test_auth_user, "require_auth:test_bypass")
    if qm_session:
        user = session_store.get_session(SafeStr.of(qm_session, "qm_session"))
        if user:
            return user
    raise NotAuthenticated()


async def require_compartment(
    compartment_id: SafeSlug,
    db: AsyncSession = Depends(get_db),
):
    """FastAPI dependency — raises 404 if the compartment or its Linux user does not exist."""
    comp = await compartment_manager.get_compartment(db, compartment_id)
    if comp is None:
        raise HTTPException(status_code=404, detail=_t("Compartment not found"))
    if not user_manager.user_exists(compartment_id):
        raise HTTPException(status_code=404, detail=_t("Compartment user not found"))
    return comp


# ---------------------------------------------------------------------------
# Config file upload registry
# ---------------------------------------------------------------------------


def _validate_json(content: str) -> None:
    """Validate that content is well-formed JSON."""
    try:
        json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc.args[0]}") from exc


def _validate_seccomp_json(content: str) -> None:
    """Validate that content is a seccomp profile JSON with a defaultAction."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc.args[0]}") from exc
    if not isinstance(data, dict) or "defaultAction" not in data:
        raise ValueError("Seccomp profile must contain a 'defaultAction' key")


def _validate_toml(content: str) -> None:
    """Validate that content is well-formed TOML (containers.conf format)."""
    try:
        tomllib.loads(content)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Invalid TOML: {exc}") from exc


def _validate_auth_json(content: str) -> None:
    """Validate that content is a registry auth JSON with an 'auths' key."""
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON: {exc.args[0]}") from exc
    if not isinstance(data, dict) or "auths" not in data:
        raise ValueError("Auth file must contain an 'auths' key")


UPLOADABLE_FIELDS: dict[str, dict[str, UploadableFieldMeta]] = {
    "container": {
        "environment_file": UploadableFieldMeta(ext=".env", preview="keyvalue"),
        "seccomp_profile": UploadableFieldMeta(
            ext=".json", preview="raw", validate=_validate_seccomp_json
        ),
        "containers_conf_module": UploadableFieldMeta(
            ext=".conf", preview="raw", validate=_validate_toml
        ),
    },
    "image": {
        "auth_file": UploadableFieldMeta(ext=".json", preview="raw", validate=_validate_auth_json),
        "containers_conf_module": UploadableFieldMeta(
            ext=".conf", preview="raw", validate=_validate_toml
        ),
    },
    "build": {
        "auth_file": UploadableFieldMeta(ext=".json", preview="raw", validate=_validate_auth_json),
        "containers_conf_module": UploadableFieldMeta(
            ext=".conf", preview="raw", validate=_validate_toml
        ),
        "ignore_file": UploadableFieldMeta(ext="", preview="raw"),
    },
    "artifact": {
        "auth_file": UploadableFieldMeta(ext=".json", preview="raw", validate=_validate_auth_json),
        "containers_conf_module": UploadableFieldMeta(
            ext=".conf", preview="raw", validate=_validate_toml
        ),
        "decryption_key": UploadableFieldMeta(ext="", preview="raw"),
    },
    "pod": {
        "containers_conf_module": UploadableFieldMeta(
            ext=".conf", preview="raw", validate=_validate_toml
        ),
    },
    "network": {
        "containers_conf_module": UploadableFieldMeta(
            ext=".conf", preview="raw", validate=_validate_toml
        ),
    },
    "volume": {
        "containers_conf_module": UploadableFieldMeta(
            ext=".conf", preview="raw", validate=_validate_toml
        ),
    },
}

_RESOURCE_TYPE_ATTRS: dict[str, str] = {
    "container": "containers",
    "image": "images",
    "build": "builds",
    "artifact": "artifacts",
    "pod": "pods",
    "network": "networks",
    "volume": "volumes",
}


def lookup_resource(comp, resource_type: str, resource_id: str):
    """Find a resource by type and ID from a compartment object.

    Returns the resource or None.
    """
    attr = _RESOURCE_TYPE_ATTRS.get(resource_type)
    if not attr:
        return None
    resources = getattr(comp, attr, [])
    return next((r for r in resources if r.id == resource_id), None)


# ---------------------------------------------------------------------------
# Template context helpers
# ---------------------------------------------------------------------------


def _agent_status(service_id: str) -> dict[str, str] | None:
    """Query the real systemd state of the monitoring agent unit.

    Returns a dict with 'color' (Tailwind bg class) and 'label', or None in
    root mode where agents are not used.
    """
    state = systemd_manager.get_agent_status(service_id)
    if state == "not-applicable":
        return None
    if state == "active":
        return {"color": "bg-green-500", "label": "active"}
    if state == "failed":
        return {"color": "bg-red-500", "label": "failed"}
    if state in ("activating", "deactivating", "reloading"):
        return {"color": "bg-yellow-400 animate-pulse", "label": state}
    # inactive, not-found, unknown, etc.
    return {"color": "bg-gray-500", "label": state}


async def comp_ctx(request: Request, comp) -> dict:
    """Base template context for compartment_detail.html, including service user info."""
    net_drivers = get_network_drivers()
    vol_drivers = get_volume_drivers()
    vol_mounts: dict[str, list[str]] = {}
    for c in comp.containers:
        for vm in c.volumes:
            vol_mounts.setdefault(vm.volume_id, []).append(c.qm_name)
    vol_sizes = await get_vol_sizes(comp.id, comp.volumes)
    _podman = get_features()
    vol_fc = get_field_choices(VolumeCreate)
    vol_spans = get_version_spans(VolumeCreate)
    return {
        "compartment": comp,
        "service_user_info": user_manager.get_user_info(comp.id),
        "helper_users": user_manager.list_helper_users(comp.id),
        "agent_status": _agent_status(comp.id),
        "net_drivers": net_drivers,
        "vol_drivers": vol_drivers,
        "vol_mounts": vol_mounts,
        "vol_sizes": vol_sizes,
        "vol_driver_choices": choices_for_template(
            vol_fc["driver"],
            dynamic_items=vol_drivers,
            version=_podman.version,
            version_span=vol_spans.get("driver"),
        ),
        "net_driver_choices": choices_for_template(
            FieldChoices(dynamic=True, empty_label="default"),
            dynamic_items=list(net_drivers),
        ),
    }
