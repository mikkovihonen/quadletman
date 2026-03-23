from typing import Annotated

from pydantic import BaseModel, model_validator

from ..constraints import N_, FieldConstraints
from ..sanitized import (
    SafeIpAddress,
    SafeMultilineStr,
    SafeResourceName,
    SafeSlug,
    SafeStr,
    SafeTimestamp,
    SafeUUID,
    enforce_model_safety,
)
from .common import _Direction, _Proto, _sanitize_db_row


@enforce_model_safety
class Process(BaseModel):
    id: SafeUUID
    compartment_id: SafeSlug
    process_name: SafeStr
    cmdline: SafeMultilineStr
    known: bool
    times_seen: int
    first_seen_at: SafeTimestamp
    last_seen_at: SafeTimestamp


@enforce_model_safety
class Connection(BaseModel):
    id: SafeUUID
    compartment_id: SafeSlug
    container_name: SafeResourceName
    proto: _Proto
    dst_ip: SafeIpAddress
    dst_port: int
    direction: _Direction
    times_seen: int
    first_seen_at: SafeTimestamp
    last_seen_at: SafeTimestamp
    whitelisted: bool = False

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.pop("known", None)
        d.setdefault("direction", "outbound")
        d.setdefault("whitelisted", False)
        _sanitize_db_row(d, Connection)
        return d


@enforce_model_safety
class WhitelistRule(BaseModel):
    id: SafeUUID
    compartment_id: SafeSlug
    description: Annotated[
        SafeStr,
        FieldConstraints(
            description=N_("Description of this whitelist rule"),
            label_hint=N_("free text"),
        ),
    ]
    container_name: SafeResourceName | None
    proto: _Proto | None
    dst_ip: SafeIpAddress | None
    dst_port: int | None
    direction: _Direction | None
    sort_order: int
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.setdefault("direction", None)
        _sanitize_db_row(d, WhitelistRule)
        return d
