"""The Garnet integration."""

from __future__ import annotations

import asyncio
import logging

from homeassistant.components.bluetooth import BluetoothScanningMode
from homeassistant.components.bluetooth.passive_update_processor import (
    PassiveBluetoothProcessorCoordinator,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, MANAGERS, PLATFORMS, is_soul_name
from .manager import SoulManager
from .parser import GarnetBluetoothDeviceData

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Garnet BLE device from a config entry."""
    address = entry.unique_id
    assert address is not None
    data = GarnetBluetoothDeviceData()
    manager = hass.data.setdefault(MANAGERS, {})[entry.entry_id] = SoulManager(
        hass, entry, soul=is_soul_name(entry.title)
    )

    def update(service_info):
        manager.observe_name(service_info.name)
        return data.update(service_info)

    coordinator = hass.data.setdefault(DOMAIN, {})[entry.entry_id] = (
        PassiveBluetoothProcessorCoordinator(
            hass,
            _LOGGER,
            address=address,
            mode=BluetoothScanningMode.PASSIVE,
            update_method=update,
        )
    )
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except (Exception, asyncio.CancelledError):
        await manager.close()
        hass.data[MANAGERS].pop(entry.entry_id)
        hass.data[DOMAIN].pop(entry.entry_id)
        raise
    entry.async_on_unload(
        coordinator.async_start()
    )  # only start after all platforms have had a chance to subscribe
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    if unload_ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await hass.data[MANAGERS].pop(entry.entry_id).close()
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok
