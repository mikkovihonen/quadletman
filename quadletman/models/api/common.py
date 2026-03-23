import json as _json
import logging
import typing
from contextvars import ContextVar
from typing import Literal

from pydantic_core import PydanticUndefined
from sqlalchemy import Table, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..sanitized import SafeStr

logger = logging.getLogger(__name__)

# Context variable to collect DB fixes during model_validate.
# _sanitize_db_row stores fixes here; _validate_row/_validate_rows reads them.
_current_db_fixes: ContextVar[dict[str, str]] = ContextVar("_current_db_fixes")

# Host path prefixes that must not be bind-mounted into containers
_BIND_MOUNT_DENYLIST = (
    "/etc",
    "/proc",
    "/sys",
    "/dev",
    "/boot",
    "/root",
    "/var/lib/quadletman",
    "/run/dbus",
)

_EventType = Literal[
    "on_failure",
    "on_restart",
    "on_start",
    "on_stop",
    "on_unexpected_process",
    "on_unexpected_connection",
]
_Proto = Literal["tcp", "udp", "icmp"]
_Direction = Literal["outbound", "inbound"]


def _no_control_chars(v: str, field_name: str = "value") -> SafeStr:
    """Reject strings containing control chars and return a ``SafeStr`` instance.

    Returning ``SafeStr`` (a branded ``str`` subclass) is the proof that this
    check has been performed.  Downstream service functions that accept
    ``SafeStr`` parameters can verify with ``sanitized.require()``.
    """
    return SafeStr.of(v, field_name)


def _loads(d: dict, *fields: str) -> None:
    """In-place JSON-decode string values for the given fields."""
    for f in fields:
        v = d.get(f)
        if isinstance(v, str):
            d[f] = _json.loads(v)


def _sanitize_db_row(d: dict, model_cls: type) -> dict[str, str]:
    """Validate DB row values against their branded types and reset invalid ones.

    Introspects ``model_cls.model_fields`` to find every field whose type is a
    branded ``str`` subclass (i.e. has an ``.of()`` class method).  For each,
    calls ``.of(value, field_name)``.  If validation raises, the field is reset
    to its default and an error is logged so the operator can fix the data.

    Returns a dict of ``{field_name: corrected_default}`` for fields that were
    sanitized.  Fixes are also stored in the ``_current_db_fixes`` context
    variable so ``_validate_row`` / ``_validate_rows`` can persist them.

    This prevents the application from crashing on legacy DB values that no
    longer pass tightened validation rules.
    """
    fixes: dict[str, str] = {}
    for name, field_info in model_cls.model_fields.items():
        value = d.get(name)
        if not isinstance(value, str) or not value:
            continue
        # Extract the base type from Annotated[T, ...] if present.
        ann = field_info.annotation
        origin = typing.get_origin(ann)
        if origin is typing.Annotated:
            ann = typing.get_args(ann)[0]
        # Only validate branded str subclasses that have .of().
        # Skip unions — get the first branded str subclass.
        if typing.get_origin(ann) is typing.Union:
            for arg in typing.get_args(ann):
                if isinstance(arg, type) and issubclass(arg, str) and hasattr(arg, "of"):
                    ann = arg
                    break
            else:
                continue
        if not (isinstance(ann, type) and issubclass(ann, str) and hasattr(ann, "of")):
            continue
        try:
            ann.of(value, name)
        except (ValueError, TypeError):
            # field_info.default is a _Trusted* branded instance whose validity
            # is guaranteed by @enforce_model_safety at import time.  Required
            # fields (PydanticUndefined) are skipped — they always have a value
            # in the DB so the .of() check above is the only guard needed.
            if field_info.default is PydanticUndefined:
                continue
            default = str(field_info.default)
            logger.error(
                "DB sanitize: field %r has invalid value %r — resetting to %r",
                name,
                value,
                default,
            )
            d[name] = default
            fixes[name] = default
    if fixes:
        _current_db_fixes.set(fixes)
    return fixes


async def _persist_db_fixes(
    db: AsyncSession, table: Table, row_id: str, fixes: dict[str, str]
) -> None:
    """Write back sanitized field values to the database.

    Call after ``_sanitize_db_row`` returned a non-empty fixes dict.
    Issues an UPDATE for the affected row and commits.
    """
    if not fixes:
        return
    await db.execute(update(table).where(table.c.id == row_id).values(**fixes))
    await db.commit()
    logger.warning(
        "DB sanitize: persisted fixes for %s row %s: %s",
        table.name,
        row_id,
        fixes,
    )


async def _validate_rows(
    db: AsyncSession,
    model_cls: type,
    table: Table,
    rows,
) -> list:
    """Validate DB rows into model instances, persisting any sanitized fixes.

    Drop-in replacement for::

        [Model.model_validate(dict(r)) for r in rows]

    If ``_sanitize_db_row`` (called inside ``_from_db``) corrected any field,
    the fix is written back to the database so it doesn't recur.
    """
    results = []
    for r in rows:
        d = dict(r)
        token = _current_db_fixes.set({})
        try:
            instance = model_cls.model_validate(d)
            fixes = _current_db_fixes.get({})
            if fixes:
                row_id = d.get("id", "")
                if row_id:
                    await _persist_db_fixes(db, table, row_id, fixes)
            results.append(instance)
        finally:
            _current_db_fixes.reset(token)
    return results


async def _validate_row(
    db: AsyncSession,
    model_cls: type,
    table: Table,
    row,
):
    """Validate a single DB row, persisting any sanitized fixes.

    Drop-in replacement for ``Model.model_validate(dict(row))``.
    Returns ``None`` if *row* is ``None``.
    """
    if row is None:
        return None
    d = dict(row)
    token = _current_db_fixes.set({})
    try:
        instance = model_cls.model_validate(d)
        fixes = _current_db_fixes.get({})
        if fixes:
            row_id = d.get("id", "")
            if row_id:
                await _persist_db_fixes(db, table, row_id, fixes)
        return instance
    finally:
        _current_db_fixes.reset(token)
