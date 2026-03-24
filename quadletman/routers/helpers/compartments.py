"""Compartment-specific template context helpers."""

from sqlalchemy.ext.asyncio import AsyncSession

from ...models.api import NotificationHookCreate
from ...models.constraints import FieldChoices
from ...models.sanitized import SafeSlug
from ...models.version_span import get_field_choices
from ...services import compartment_manager
from .common import choices_for_template


async def notification_hooks_ctx(db: AsyncSession, compartment_id: SafeSlug) -> dict:
    """Build the template context for the notification hooks partial."""
    hooks = await compartment_manager.list_notification_hooks(db, compartment_id)
    comp = await compartment_manager.get_compartment(db, compartment_id)
    container_names = [c.name for c in (comp.containers if comp else [])]
    _fc = get_field_choices(NotificationHookCreate)
    return {
        "compartment_id": compartment_id,
        "hooks": hooks,
        "container_names": container_names,
        "container_name_choices": choices_for_template(
            _fc["qm_container_name"],
            dynamic_items=container_names,
        ),
    }


async def process_monitor_ctx(db: AsyncSession, compartment_id: SafeSlug) -> dict:
    processes = await compartment_manager.list_processes(db, compartment_id)
    patterns = await compartment_manager.list_process_patterns(db, compartment_id)
    compartment = await compartment_manager.get_compartment(db, compartment_id)
    # Compute match counts per pattern
    pattern_match_counts: dict[str, int] = {}
    for p in processes:
        if p.pattern_id:
            pattern_match_counts[str(p.pattern_id)] = (
                pattern_match_counts.get(str(p.pattern_id), 0) + 1
            )
    return {
        "compartment_id": compartment_id,
        "processes": processes,
        "patterns": patterns,
        "pattern_match_counts": pattern_match_counts,
        "process_monitor_enabled": compartment.process_monitor_enabled,
    }


async def connection_monitor_ctx(db: AsyncSession, compartment_id: SafeSlug) -> dict:
    compartment = await compartment_manager.get_compartment(db, compartment_id)
    connections = await compartment_manager.list_connections(db, compartment_id)
    rules = await compartment_manager.list_allowlist_rules(db, compartment_id)
    containers = await compartment_manager.list_containers(db, compartment_id)
    return {
        "compartment_id": compartment_id,
        "connections": connections,
        "rules": rules,
        "containers": containers,
        "connection_monitor_enabled": compartment.connection_monitor_enabled,
        "connection_history_retention_days": compartment.connection_history_retention_days,
        "container_name_choices": choices_for_template(
            FieldChoices(dynamic=True, empty_label="any"),
            dynamic_items=[c.name for c in containers],
        ),
    }


def status_dot_context(compartment_id: SafeSlug, statuses: list[dict], oob: bool = False) -> dict:
    """Compute template context for the status dot partial."""
    active = [s for s in statuses if s["active_state"] == "active"]
    failed = [s for s in statuses if s["active_state"] == "failed"]
    transitioning = [s for s in statuses if s["active_state"] in ("activating", "deactivating")]
    if not statuses:
        color = "bg-gray-600"
        title = "no units"
    elif failed:
        color = "bg-red-500"
        title = f"{len(failed)} failed"
    elif transitioning:
        color = "bg-yellow-400 animate-pulse"
        title = "transitioning"
    elif len(active) == len(statuses):
        color = "bg-green-500"
        title = "all running"
    elif active:
        color = "bg-yellow-500"
        title = f"{len(active)}/{len(statuses)} running"
    else:
        color = "bg-gray-500"
        title = "stopped"
    return {"compartment_id": compartment_id, "color": color, "title": title, "oob": oob}
