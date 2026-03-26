"""Helpers shared across multiple domain routers."""

import asyncio
import json
import os
import re
from collections.abc import Sequence
from typing import Any

from fastapi import Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.engine import get_db
from ...i18n import gettext as _t
from ...models.api import VolumeCreate
from ...models.constraints import FieldChoices
from ...models.sanitized import SafeSlug
from ...models.version_span import (
    PodmanVersion,
    VersionSpan,
    get_field_choices,
    get_field_constraints,
    get_version_spans,
    is_value_available,
    value_tooltip,
)
from ...podman_version import get_features
from ...services import compartment_manager, metrics, systemd_manager, user_manager
from ...utils import dir_size, fmt_bytes

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Maximum size for file uploads (archive restore + single file upload).
MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # 512 MiB

# Environment files are tiny — 64 KiB is generous.
MAX_ENVFILE_BYTES = 64 * 1024

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
    """Run a blocking function in the default thread-pool executor."""
    return await asyncio.get_event_loop().run_in_executor(None, fn, *args)


def toast_trigger(message: str, *, error: bool = False) -> dict[str, str]:
    """Return an HX-Trigger header dict for a showToast notification."""
    return {
        "HX-Trigger": json.dumps(
            {"showToast": message, "toastType": "error" if error else "success"}
        )
    }


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


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
    net_drivers, vol_drivers = user_manager.get_compartment_drivers(comp.id)
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
