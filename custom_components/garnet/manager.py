"""Explicit, in-memory SOUL configuration staging; never polls the controller."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from homeassistant.components import bluetooth
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import Entity

from .const import is_soul_name
from .soul_protocol import SoulConfig, SoulProtocolError, stage_config
from .soul_transport import SoulTransport, SoulTransportError

_LOGGER = logging.getLogger(__name__)


class SoulManager:
    """Serialize operations and keep staged edits separate from physical state."""

    def __init__(self, hass, entry, *, soul: bool = False):
        self.hass = hass
        self.entry = entry
        self.address = entry.unique_id
        self.is_soul = soul
        self.snapshot: SoulConfig | None = None
        self.changes: dict[int, int] = {}
        self.status = "not_read" if soul else "monitor_only"
        self.busy = False
        self.closed = False
        self._lock = asyncio.Lock()
        self._transport: SoulTransport | None = None
        self._task: asyncio.Task | None = None
        self._listeners: set[Callable] = set()

    @property
    def writable(self) -> bool:
        return bool(self.snapshot and self.snapshot.writable and not self.closed)

    def subscribe(self, listener: Callable) -> Callable:
        self._listeners.add(listener)
        return lambda: self._listeners.discard(listener)

    def notify(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    def observe_name(self, name: str | None) -> None:
        if not self.is_soul and is_soul_name(name):
            self.is_soul = True
            self.status = "not_read"
            self.notify()

    def _check_idle(self) -> None:
        if self.closed or self.busy:
            raise HomeAssistantError("Controller operation in progress or unloaded")

    def stage(self, index: int, state: int) -> None:
        self._check_idle()
        if not self.writable:
            raise HomeAssistantError("Read supported firmware configuration first")
        stage_config(self.snapshot, {index: state})
        if state == self.snapshot.sender_states[index]:
            self.changes.pop(index, None)
        else:
            self.changes[index] = state
        self.status = "staged" if self.changes else "ready"
        self.notify()

    def discard(self) -> None:
        self._check_idle()
        self.changes.clear()
        if self.snapshot:
            self.status = "ready" if self.writable else "monitor_only"
        self.notify()

    async def read(self) -> None:
        await self._operate(apply=False)

    async def apply(self) -> None:
        await self._operate(apply=True)

    async def _operate(self, *, apply: bool) -> None:
        self._check_idle()
        if not self.is_soul:
            raise HomeAssistantError("This device is monitor-only")
        if apply and not self.writable:
            raise HomeAssistantError("Read supported firmware configuration first")
        # No await between admission and busy: concurrent UI calls cannot queue
        # stale edits behind an in-flight read/apply.
        self.busy = True
        self._task = asyncio.current_task()
        self.status = "applying" if apply else "reading"
        self.notify()
        async with self._lock:
            try:
                device = bluetooth.async_ble_device_from_address(
                    self.hass, self.address, connectable=True
                )
                if device is None:
                    raise HomeAssistantError(
                        "No connectable Bluetooth device available"
                    )
                if self._transport is None:
                    self._transport = SoulTransport(device)
                else:
                    self._transport.device = device
                if apply:
                    snapshot = await self._transport.apply_config(
                        self.snapshot, dict(self.changes)
                    )
                else:
                    snapshot = await self._transport.read_config()
                self.snapshot = snapshot
                self.changes.clear()
                self.status = "ready" if snapshot.writable else "monitor_only"
                registry = dr.async_get(self.hass)
                device_entry = registry.async_get_device_by_connection(
                    (dr.CONNECTION_BLUETOOTH, self.address), self.entry.entry_id
                )
                if device_entry:
                    kwargs = {"sw_version": snapshot.version}
                    if snapshot.version.startswith("G708"):
                        kwargs["model"] = "G708 SOUL"
                    registry.async_update_device(device_entry.id, **kwargs)
            except asyncio.CancelledError:
                self.snapshot = None
                self.status = "uncertain"
                raise
            except Exception as err:  # noqa: BLE001 -- invalidate snapshot and redact backend payloads
                self.snapshot = None
                self.status = "uncertain"
                detail = (
                    str(err)
                    if isinstance(err, (SoulProtocolError, SoulTransportError))
                    else type(err).__name__
                )
                _LOGGER.warning(
                    "SOUL %s failed: %s", "apply" if apply else "read", detail
                )
                # Backend exceptions can contain payloads or addresses.
                raise HomeAssistantError(
                    "Configuration uncertain; read configuration before applying again"
                ) from None
            finally:
                self.busy = False
                self._task = None
                self.notify()

    async def close(self) -> None:
        self.closed = True
        if self._task and self._task is not asyncio.current_task():
            task = self._task
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.snapshot = None
        self.changes.clear()
        self._listeners.clear()
        self._transport = None


class SoulEntity(Entity):
    """Shared push-only entity lifecycle for management controls."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: SoulManager, key: str) -> None:
        self.manager = manager
        self._attr_unique_id = f"{manager.address}_soul_{key}"
        self._attr_device_info = dr.DeviceInfo(
            connections={(dr.CONNECTION_BLUETOOTH, manager.address)}
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.manager.subscribe(self.async_write_ha_state))
