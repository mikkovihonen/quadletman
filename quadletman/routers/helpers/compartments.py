"""Compartment-specific template context helpers."""

from sqlalchemy.ext.asyncio import AsyncSession

from ...models.sanitized import SafeSlug
from ...services import compartment_manager


async def notification_hooks_ctx(db: AsyncSession, compartment_id: SafeSlug) -> dict:
    """Build the template context for the notification hooks partial."""
    hooks = await compartment_manager.list_notification_hooks(db, compartment_id)
    comp = await compartment_manager.get_compartment(db, compartment_id)
    container_names = [c.name for c in (comp.containers if comp else [])]
    return {"compartment_id": compartment_id, "hooks": hooks, "container_names": container_names}


async def process_monitor_ctx(db: AsyncSession, compartment_id: SafeSlug) -> dict:
    processes = await compartment_manager.list_processes(db, compartment_id)
    compartment = await compartment_manager.get_compartment(db, compartment_id)
    return {
        "compartment_id": compartment_id,
        "processes": processes,
        "process_monitor_enabled": compartment.process_monitor_enabled,
    }


async def connection_monitor_ctx(db: AsyncSession, compartment_id: SafeSlug) -> dict:
    compartment = await compartment_manager.get_compartment(db, compartment_id)
    connections = await compartment_manager.list_connections(db, compartment_id)
    rules = await compartment_manager.list_whitelist_rules(db, compartment_id)
    containers = await compartment_manager.list_containers(db, compartment_id)
    return {
        "compartment_id": compartment_id,
        "connections": connections,
        "rules": rules,
        "containers": containers,
        "connection_monitor_enabled": compartment.connection_monitor_enabled,
        "connection_history_retention_days": compartment.connection_history_retention_days,
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
