from typing import Annotated

from pydantic import BaseModel, model_validator

from ..constraints import N_, RESOURCE_NAME_CN, FieldConstraints
from ..sanitized import (
    SafeCalendarSpec,
    SafeResourceName,
    SafeSlug,
    SafeTimeDuration,
    SafeTimestamp,
    SafeUUID,
    enforce_model_safety,
)
from ..version_span import enforce_model_version_gating


@enforce_model_version_gating(
    exempt={
        "name": "identity field — quadletman resource name, not a Quadlet key",
        "container_id": "quadletman-internal FK to containers table, not a Podman concept",
        "on_calendar": "systemd timer schedule — systemd feature, not Podman-version-dependent",
        "on_boot_sec": "systemd timer delay — systemd feature, not Podman-version-dependent",
        "random_delay_sec": "systemd timer jitter — systemd feature, not Podman-version-dependent",
        "persistent": "systemd timer persistence — systemd feature, not Podman-version-dependent",
        "enabled": "quadletman-internal toggle for timer activation, not a Podman concept",
    }
)
@enforce_model_safety
class TimerCreate(BaseModel):
    name: Annotated[SafeResourceName, RESOURCE_NAME_CN]
    container_id: SafeUUID
    on_calendar: Annotated[
        SafeCalendarSpec,
        FieldConstraints(placeholder=N_("*-*-* 02:00:00"), label_hint=N_("e.g. daily, hourly")),
    ] = SafeCalendarSpec.trusted("", "default")
    on_boot_sec: Annotated[SafeTimeDuration, FieldConstraints(placeholder=N_("5min"))] = (
        SafeTimeDuration.trusted("", "default")
    )
    random_delay_sec: Annotated[SafeTimeDuration, FieldConstraints(placeholder=N_("30s"))] = (
        SafeTimeDuration.trusted("", "default")
    )
    persistent: bool = False
    enabled: bool = True


@enforce_model_safety
class Timer(TimerCreate):
    id: SafeUUID
    compartment_id: SafeSlug
    container_name: SafeResourceName = SafeResourceName.trusted("", "default")
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.setdefault("container_name", "")
        return d
