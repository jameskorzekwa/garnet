"""Allowlisted diagnostics: no address, raw configuration, or credentials."""

from .const import MANAGERS


async def async_get_config_entry_diagnostics(hass, entry):
    manager = hass.data[MANAGERS][entry.entry_id]
    return {
        "management_status": manager.status,
        "pending_changes": len(manager.changes),
        "writable": manager.writable,
        "busy": manager.busy,
    }
