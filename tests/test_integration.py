"""Home Assistant harness setup/unload and entity service tests."""

import asyncio
from struct import pack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from home_assistant_bluetooth import BluetoothServiceInfo
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.garnet import async_setup_entry
from custom_components.garnet.const import DOMAIN, MANAGERS
from custom_components.garnet.diagnostics import async_get_config_entry_diagnostics
from custom_components.garnet.parser import GarnetBluetoothDeviceData
from custom_components.garnet.soul_protocol import (
    VERIFIED_VERSION,
    SoulConfig,
    stage_config,
)

pytestmark = pytest.mark.asyncio


async def test_cancel_during_platform_forward_cleans_up(hass):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="AA:BB:CC:DD:EE:FF", title="Soul", data={}
    )
    entered = asyncio.Event()

    async def forward(*args):
        entered.set()
        await asyncio.Future()

    with (
        patch(
            "custom_components.garnet.PassiveBluetoothProcessorCoordinator"
        ) as coordinator,
        patch.object(
            hass.config_entries, "async_forward_entry_setups", side_effect=forward
        ),
    ):
        task = asyncio.create_task(async_setup_entry(hass, entry))
        await asyncio.wait_for(entered.wait(), timeout=5)
        manager = hass.data[MANAGERS][entry.entry_id]
        assert entry.entry_id in hass.data[DOMAIN]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
        assert manager.closed
        assert entry.entry_id not in hass.data[MANAGERS]
        assert entry.entry_id not in hass.data[DOMAIN]
        coordinator.return_value.async_start.assert_not_called()


async def test_setup_controls_services_unload(hass, enable_custom_integrations):
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="AA:BB:CC:DD:EE:FF", title="Garnet SOUL", data={}
    )
    entry.add_to_hass(hass)
    with (
        patch(
            "homeassistant.components.bluetooth.update_coordinator.async_address_present",
            return_value=False,
        ),
        patch(
            "homeassistant.components.bluetooth.update_coordinator.async_last_service_info",
            return_value=None,
        ),
        patch(
            "custom_components.garnet.PassiveBluetoothProcessorCoordinator.async_start",
            return_value=lambda: None,
        ),
        patch(
            "homeassistant.components.bluetooth.passive_update_processor.async_register_coordinator_for_restore",
            return_value=lambda: None,
        ),
        patch("homeassistant.setup.async_process_deps_reqs", return_value=None),
        patch(
            "custom_components.garnet.manager.bluetooth.async_scanner_devices_by_address",
            return_value=[object()],
        ),
        patch(
            "custom_components.garnet.manager.bluetooth.async_request_active_scan",
            new_callable=AsyncMock,
        ) as scan,
    ):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        manager = hass.data[MANAGERS][entry.entry_id]
        assert manager.snapshot is None
        assert manager._transport is None
        scan.assert_not_awaited()
        registry = er.async_get(hass)
        entities = er.async_entries_for_config_entry(registry, entry.entry_id)
        selects = [entity for entity in entities if entity.domain == "select"]
        assert len(selects) == 8
        assert all(entity.entity_category.value == "config" for entity in selects)
        assert all(
            hass.states.get(entity.entity_id).state == "unavailable"
            for entity in selects
        )
        manager.snapshot = SoulConfig(VERIFIED_VERSION, bytes(36))
        manager.status = "ready"
        manager.notify()
        await hass.async_block_till_done()
        fresh = next(
            entity for entity in selects if entity.unique_id.endswith("sender_0")
        )
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": fresh.entity_id, "option": "stacked"},
            blocking=True,
        )
        assert manager.changes == {0: 2}
        assert manager._transport is None
        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        assert diagnostics == {
            "management_status": "staged",
            "pending_changes": 1,
            "writable": True,
            "busy": False,
        }
        discard = next(
            entity for entity in entities if entity.unique_id.endswith("_discard")
        )
        await hass.services.async_call(
            "button", "press", {"entity_id": discard.entity_id}, blocking=True
        )
        assert manager.changes == {}
        transport = MagicMock()
        transport.read_config = AsyncMock(
            return_value=SoulConfig(VERIFIED_VERSION, bytes(36))
        )
        transport.apply_config = AsyncMock(
            side_effect=lambda expected, changes: stage_config(expected, changes)
        )
        read_button = next(
            entity for entity in entities if entity.unique_id.endswith("_read")
        )
        apply_button = next(
            entity for entity in entities if entity.unique_id.endswith("_apply")
        )
        with (
            patch(
                "custom_components.garnet.manager.SoulTransport", return_value=transport
            ),
            patch(
                "custom_components.garnet.manager.bluetooth.async_ble_device_from_address",
                return_value=object(),
            ),
        ):
            await hass.services.async_call(
                "button", "press", {"entity_id": read_button.entity_id}, blocking=True
            )
            device = dr.async_get(hass).async_get_device_by_connection(
                (dr.CONNECTION_BLUETOOTH, entry.unique_id), entry.entry_id
            )
            assert device.sw_version == VERIFIED_VERSION
            assert device.model == "G708 SOUL"
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": fresh.entity_id, "option": "single"},
                blocking=True,
            )
            transport.apply_config.assert_not_called()
            await hass.services.async_call(
                "button", "press", {"entity_id": apply_button.entity_id}, blocking=True
            )
            assert manager.snapshot.sender_states[0] == 1
            assert manager.changes == {}
            transport.read_config.return_value = SoulConfig("G708_future", bytes(36))
            await hass.services.async_call(
                "button", "press", {"entity_id": read_button.entity_id}, blocking=True
            )
            await hass.async_block_till_done()
            assert hass.states.get(fresh.entity_id).state == "unavailable"
            assert hass.states.get(apply_button.entity_id).state == "unavailable"
            assert hass.states.get(read_button.entity_id).state != "unavailable"
        scan.assert_not_awaited()
        with (
            patch(
                "custom_components.garnet.manager.bluetooth.async_scanner_devices_by_address",
                return_value=[],
            ),
            patch("custom_components.garnet.manager.SoulTransport") as constructor,
        ):
            with pytest.raises(HomeAssistantError, match="No connectable path"):
                await hass.services.async_call(
                    "button",
                    "press",
                    {"entity_id": read_button.entity_id},
                    blocking=True,
                )
            await hass.async_block_till_done()
            assert hass.states.get(fresh.entity_id).state == "unavailable"
            assert hass.states.get(apply_button.entity_id).state == "unavailable"
            assert not manager.writable
            constructor.assert_not_called()
            assert transport.apply_config.await_count == 1
            scan.assert_awaited_once_with(hass, duration=10)
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert manager.closed
        assert entry.entry_id not in hass.data[MANAGERS]
        assert entry.entry_id not in hass.data[DOMAIN]
        assert not manager._listeners


@pytest.mark.parametrize(
    "name,manufacturer", [("Soul", 3264), ("709-BTP7", 3264), ("709-BTP3", 305)]
)
async def test_existing_entity_registry_identity(
    hass, enable_custom_integrations, name, manufacturer
):
    address = "AA:BB:CC:DD:EE:FF"
    entry = MockConfigEntry(domain=DOMAIN, unique_id=address, title=name, data={})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    original = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{address}-fresh_tank",
        config_entry=entry,
        suggested_object_id="my_existing_fresh_level",
    )
    with (
        patch(
            "homeassistant.components.bluetooth.update_coordinator.async_address_present",
            return_value=True,
        ),
        patch(
            "homeassistant.components.bluetooth.update_coordinator.async_last_service_info",
            return_value=None,
        ),
        patch(
            "custom_components.garnet.PassiveBluetoothProcessorCoordinator.async_start",
            return_value=lambda: None,
        ),
        patch(
            "homeassistant.components.bluetooth.passive_update_processor.async_register_coordinator_for_restore",
            return_value=lambda: None,
        ),
        patch("homeassistant.setup.async_process_deps_reqs", return_value=None),
    ):
        assert await async_setup_component(hass, DOMAIN, {})
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        payload = (
            pack("<3sB3s3s3sB", b"\x01\x00\x00", 0, b"050", b"000", b"100", 0)
            if manufacturer == 305
            else pack("<HxBBBBBBBBBBB", 1, 50, 2, 3, 4, 5, 6, 7, 8, 120, 0, 0)
        )
        data = GarnetBluetoothDeviceData().update(
            BluetoothServiceInfo(
                name=name,
                address=address,
                rssi=-60,
                manufacturer_data={manufacturer: payload},
                service_data={},
                service_uuids=[],
                source="test",
            )
        )
        hass.data[DOMAIN][entry.entry_id].async_set_updated_data(data)
        await hass.async_block_till_done()
        assert (
            registry.async_get_entity_id("sensor", DOMAIN, f"{address}-fresh_tank")
            == original.entity_id
        )
        assert hass.states.get(original.entity_id).state == "50"
        manager = hass.data[MANAGERS][entry.entry_id]
        assert manager.is_soul == (name == "Soul")
        assert manager._transport is None
        assert await hass.config_entries.async_unload(entry.entry_id)
