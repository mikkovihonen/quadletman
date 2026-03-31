from pydantic import BaseModel

from ..sanitized import SafeSlug, SafeStr, enforce_model_safety


@enforce_model_safety
class MetricsSnapshot(BaseModel):
    compartment_id: SafeSlug
    cpu_percent: float
    mem_bytes: int
    proc_count: int
    disk_bytes: int


@enforce_model_safety
class StatusDot(BaseModel):
    compartment_id: SafeSlug
    color: SafeStr
    title: SafeStr


@enforce_model_safety
class DiskTotal(BaseModel):
    compartment_id: SafeSlug
    disk_bytes: int


@enforce_model_safety
class ContainerStatus(BaseModel):
    container: SafeStr
    active_state: SafeStr
    sub_state: SafeStr
    load_state: SafeStr
    unit_file_state: SafeStr


@enforce_model_safety
class DiskBreakdown(BaseModel):
    images: list[dict]
    overlays: list[dict]
    volumes: list[dict]
    volumes_total: int
    config_bytes: int


@enforce_model_safety
class DashboardPollResponse(BaseModel):
    poll_interval: int
    disk_poll_interval: int
    metrics: list[MetricsSnapshot]
    status_dots: list[StatusDot]
    disk: list[DiskTotal] | None = None


@enforce_model_safety
class CompartmentPollResponse(BaseModel):
    poll_interval: int
    disk_poll_interval: int
    cpu_percent: float
    mem_bytes: int
    proc_count: int
    disk_bytes: int
    statuses: list[ContainerStatus]
    status_dot: StatusDot
    disk: DiskBreakdown | None = None
