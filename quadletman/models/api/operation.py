"""Pydantic models for lifecycle operation queue."""

from pydantic import BaseModel

from ..sanitized import SafeSlug, SafeStr, SafeTimestamp, SafeUUID


class Operation(BaseModel):
    """A queued lifecycle operation (start/stop/restart/resync)."""

    id: SafeUUID
    compartment_id: SafeSlug
    op_type: SafeStr
    status: SafeStr  # pending, running, completed, failed
    payload: SafeStr  # JSON string: operation parameters
    result: SafeStr  # JSON string: errors list or empty
    submitted_by: SafeStr
    submitted_at: SafeTimestamp
    started_at: SafeTimestamp | None = None
    completed_at: SafeTimestamp | None = None
