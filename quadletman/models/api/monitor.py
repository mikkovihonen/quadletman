from typing import Annotated

from pydantic import BaseModel, model_validator

from ..constraints import (
    DIRECTION_CHOICES,
    IP_ADDRESS_CN,
    N_,
    PORT_NUMBER_CN,
    PROTO_CHOICES,
    FieldChoices,
    FieldConstraints,
)
from ..sanitized import (
    SafeIpAddress,
    SafeMultilineStr,
    SafePortStr,
    SafeRegex,
    SafeResourceName,
    SafeResourceNameOrEmpty,
    SafeSlug,
    SafeStr,
    SafeTimestamp,
    SafeUUID,
    SafeUUIDOrEmpty,
    enforce_model_safety,
)
from .common import _Direction, _Proto, _sanitize_db_row


@enforce_model_safety
class ProcessPattern(BaseModel):
    id: SafeUUID
    compartment_id: SafeSlug
    process_name: SafeStr
    cmdline_pattern: SafeRegex
    segments_json: SafeStr
    created_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        _sanitize_db_row(d, ProcessPattern)
        return d


@enforce_model_safety
class Process(BaseModel):
    id: SafeUUID
    compartment_id: SafeSlug
    process_name: SafeStr
    cmdline: SafeMultilineStr
    known: bool
    pattern_id: SafeUUIDOrEmpty = SafeUUIDOrEmpty.trusted("", "default")
    times_seen: int
    first_seen_at: SafeTimestamp
    last_seen_at: SafeTimestamp

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        if d.get("pattern_id") is None:
            d["pattern_id"] = ""
        _sanitize_db_row(d, Process)
        return d


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
    allowlisted: bool = False

    @model_validator(mode="before")
    @classmethod
    def _from_db(cls, data):
        if not isinstance(data, dict):
            return data
        d = dict(data)
        d.pop("known", None)
        d.setdefault("direction", "outbound")
        d.setdefault("allowlisted", False)
        _sanitize_db_row(d, Connection)
        return d


@enforce_model_safety
class AllowlistRuleCreate(BaseModel):
    """Form input model for creating a connection allowlist rule."""

    description: Annotated[
        SafeStr,
        FieldConstraints(
            description=N_("Description of this allowlist rule"),
            label_hint=N_("free text"),
            placeholder=N_("Allow HTTPS traffic"),
        ),
    ] = SafeStr.trusted("", "default")
    container_name: Annotated[
        SafeResourceNameOrEmpty,
        FieldChoices(dynamic=True, empty_label="any"),
        FieldConstraints(
            description=N_("Container to match, or any"),
            label_hint=N_("leave empty for any container"),
        ),
    ] = SafeResourceNameOrEmpty.trusted("", "default")
    proto: Annotated[
        SafeStr,
        PROTO_CHOICES,
        FieldConstraints(
            description=N_("Network protocol to match"),
            label_hint=N_("network protocol"),
        ),
    ] = SafeStr.trusted("", "default")
    dst_ip: Annotated[
        SafeIpAddress,
        IP_ADDRESS_CN,
        FieldConstraints(
            description=N_("Remote IP or CIDR to match"),
            label_hint=N_("IP or CIDR"),
            placeholder=N_("0.0.0.0/0"),
        ),
    ] = SafeIpAddress.trusted("", "default")
    dst_port: Annotated[
        SafePortStr,
        PORT_NUMBER_CN,
        FieldConstraints(
            description=N_("Remote port to match"),
            label_hint=N_("1–65535"),
            placeholder="443",
        ),
    ] = SafePortStr.trusted("", "default")
    direction: Annotated[
        SafeStr,
        DIRECTION_CHOICES,
        FieldConstraints(
            description=N_("Traffic direction to match"),
            label_hint=N_("traffic direction"),
        ),
    ] = SafeStr.trusted("", "default")


@enforce_model_safety
class AllowlistRule(BaseModel):
    id: SafeUUID
    compartment_id: SafeSlug
    description: Annotated[
        SafeStr,
        FieldConstraints(
            description=N_("Description of this allowlist rule"),
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
        _sanitize_db_row(d, AllowlistRule)
        return d
