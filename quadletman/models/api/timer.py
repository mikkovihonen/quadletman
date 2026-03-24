from typing import Annotated

from pydantic import BaseModel, model_validator

from ..constraints import CALENDAR_SPEC_CN, N_, RESOURCE_NAME_CN, TIME_DURATION_CN, FieldConstraints
from ..sanitized import (
    SafeCalendarSpec,
    SafeResourceName,
    SafeResourceNameOrEmpty,
    SafeSlug,
    SafeTimeDuration,
    SafeTimestamp,
    SafeUUID,
    enforce_model_safety,
)
from ..version_span import enforce_model_version_gating
from .common import _sanitize_db_row


@enforce_model_version_gating(
    exempt={
        "qm_name": "identity field — quadletman resource name, not a Quadlet key",
        "qm_container_id": "quadletman-internal FK to containers table, not a Podman concept",
        "on_calendar": "systemd timer schedule — systemd feature, not Podman-version-dependent",
        "on_boot_sec": "systemd timer delay — systemd feature, not Podman-version-dependent",
        "random_delay_sec": "systemd timer jitter — systemd feature, not Podman-version-dependent",
        "persistent": "systemd timer persistence — systemd feature, not Podman-version-dependent",
        "qm_enabled": "quadletman-internal toggle for timer activation, not a Podman concept",
    }
)
@enforce_model_safety
class TimerCreate(BaseModel):
    qm_name: Annotated[
        SafeResourceName,
        RESOURCE_NAME_CN,
        FieldConstraints(
            description=N_("Name of this timer"),
            label_hint=N_("lowercase, a-z 0-9 and hyphens"),
            placeholder=N_("my-timer"),
        ),
    ]
    qm_container_id: SafeUUID
    on_calendar: Annotated[
        SafeCalendarSpec,
        CALENDAR_SPEC_CN,
        FieldConstraints(
            description=N_("Systemd calendar schedule expression"),
            placeholder=N_("*-*-* 02:00:00"),
            label_hint=N_("e.g. daily, hourly"),
        ),
    ] = SafeCalendarSpec.trusted("", "default")
    on_boot_sec: Annotated[
        SafeTimeDuration,
        TIME_DURATION_CN,
        FieldConstraints(
            description=N_("Run this long after system boot"),
            placeholder=N_("5min"),
            label_hint=N_("e.g. 30s, 5min"),
        ),
    ] = SafeTimeDuration.trusted("", "default")
    random_delay_sec: Annotated[
        SafeTimeDuration,
        TIME_DURATION_CN,
        FieldConstraints(
            description=N_("Random delay before running"),
            placeholder=N_("30s"),
            label_hint=N_("e.g. 30s, 5min"),
        ),
    ] = SafeTimeDuration.trusted("", "default")
    persistent: Annotated[
        bool,
        FieldConstraints(
            description=N_("Run missed schedules on next boot"),
            label_hint=N_("catches up missed runs"),
        ),
    ] = False
    qm_enabled: Annotated[
        bool,
        FieldConstraints(
            description=N_("Whether this timer is active"),
            label_hint=N_("default: on"),
        ),
    ] = True


@enforce_model_safety
class Timer(TimerCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    qm_container_name: SafeResourceNameOrEmpty = SafeResourceNameOrEmpty.trusted("", "default")
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.setdefault("qm_container_name", "")
        _sanitize_db_row(d, Timer)
        return d
