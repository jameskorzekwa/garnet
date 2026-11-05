"""Configuration staging and concurrency tests without Bluetooth hardware."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.garnet.manager import SoulManager
from custom_components.garnet.soul_protocol import (
    VERIFIED_VERSION,
    SoulConfig,
    SoulProtocolError,
    stage_config,
)

pytestmark = pytest.mark.asyncio


def make_manager(hass, *, soul=True):
    return SoulManager(
        hass, SimpleNamespace(unique_id="AA:BB:CC:DD:EE:FF", entry_id="test"), soul=soul
    )


@pytest.fixture
def discovery():
    with (
        patch(
            "custom_components.garnet.manager.bluetooth.async_scanner_devices_by_address",
            return_value=[object()],
        ) as paths,
        patch(
            "custom_components.garnet.manager.bluetooth.async_request_active_scan",
            new_callable=AsyncMock,
        ) as scan,
    ):
        yield paths, scan


@pytest.fixture
def transport(discovery):
    transport = MagicMock()
    snapshot = SoulConfig(VERIFIED_VERSION, bytes(36))
    transport.read_config = AsyncMock(return_value=snapshot)
    transport.apply_config = AsyncMock(
        side_effect=lambda expected, changes: stage_config(expected, changes)
    )
    with (
        patch("custom_components.garnet.manager.SoulTransport", return_value=transport),
        patch(
            "custom_components.garnet.manager.bluetooth.async_ble_device_from_address",
            return_value=object(),
        ),
    ):
        yield transport


async def test_stage_apply_discard(hass, transport):
    manager = make_manager(hass)
    with pytest.raises(HomeAssistantError):
        await manager.apply()
    transport.read_config.assert_not_called()
    await manager.read()
    expected = manager.snapshot
    manager.stage(0, 2)
    transport.apply_config.assert_not_called()
    manager.discard()
    assert manager.changes == {}
    manager.stage(7, 1)
    await manager.apply()
    transport.apply_config.assert_awaited_once_with(expected, {7: 1})
    assert manager.snapshot.sender_states[7] == 1
    assert manager.status == "ready"
    assert not manager.changes


async def test_failure_requires_read_and_redacts(hass, transport):
    manager = make_manager(hass)
    await manager.read()
    manager.stage(0, 1)
    transport.apply_config.side_effect = RuntimeError("SECRET CONFIG BYTES")
    with pytest.raises(HomeAssistantError, match="uncertain") as err:
        await manager.apply()
    assert "SECRET" not in str(err.value)
    assert manager.snapshot is None
    manager.discard()
    assert manager.status == "uncertain"
    with pytest.raises(HomeAssistantError):
        await manager.apply()
    await manager.read()
    assert manager.writable


async def test_inflight_edits_rejected_and_close_cancels(hass, transport):
    manager = make_manager(hass)
    await manager.read()
    entered = asyncio.Event()

    async def wait():
        entered.set()
        await asyncio.Event().wait()

    transport.read_config.side_effect = wait
    task = asyncio.create_task(manager.read())
    await entered.wait()
    for action in (lambda: manager.stage(0, 1), manager.discard):
        with pytest.raises(HomeAssistantError):
            action()
    with pytest.raises(HomeAssistantError):
        await manager.apply()
    await manager.close()
    assert task.cancelled()
    assert manager.status == "uncertain"
    assert not manager.writable


async def test_unsupported_and_non_soul(hass, transport):
    manager = make_manager(hass, soul=False)
    with pytest.raises(HomeAssistantError):
        await manager.read()
    transport.read_config.assert_not_called()
    manager.observe_name("Soul")
    transport.read_config.return_value = SoulConfig("G708_future", bytes(36))
    await manager.read()
    assert manager.status == "monitor_only"
    with pytest.raises(HomeAssistantError):
        manager.stage(0, 1)
    with pytest.raises(HomeAssistantError):
        await manager.apply()
    transport.apply_config.assert_not_called()


async def test_fresh_device_reused_transport(hass, transport):
    manager = make_manager(hass)
    await manager.read()
    fresh = object()
    with patch(
        "custom_components.garnet.manager.bluetooth.async_ble_device_from_address",
        return_value=fresh,
    ) as lookup:
        await manager.read()
        lookup.assert_called_once_with(hass, manager.address, connectable=True)
    assert manager._transport is transport
    assert transport.device is fresh


async def test_invalid_lpg_stacked_rejected_without_io(hass, transport):
    manager = make_manager(hass)
    await manager.read()
    with pytest.raises(SoulProtocolError):
        manager.stage(7, 2)
    assert manager.changes == {}
    transport.apply_config.assert_not_called()


async def test_apply_cancellation_invalidates_snapshot(hass, transport):
    manager = make_manager(hass)
    await manager.read()
    manager.stage(0, 1)
    transport.apply_config.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await manager.apply()
    assert manager.status == "uncertain"
    assert manager.snapshot is None
    assert not manager.busy
    with pytest.raises(HomeAssistantError):
        await manager.apply()


@pytest.mark.parametrize("apply", [False, True])
async def test_missing_path_sweeps_then_resolves_fresh(
    hass, transport, discovery, apply
):
    manager = make_manager(hass)
    await manager.read()
    manager.stage(0, 1)
    paths, scan = discovery
    paths.side_effect = [[], [object()]]
    fresh = object()

    def resolve(*args, **kwargs):
        scan.assert_awaited_once_with(hass, duration=10)
        assert paths.call_count == 3  # initial read and both recovery checks
        return fresh

    with patch(
        "custom_components.garnet.manager.bluetooth.async_ble_device_from_address",
        side_effect=resolve,
    ) as lookup:
        await (manager.apply() if apply else manager.read())
        lookup.assert_called_once_with(hass, manager.address, connectable=True)
    assert transport.device is fresh
    assert manager.writable
    assert manager.status == "ready"


async def test_valid_path_never_sweeps(hass, transport, discovery):
    manager = make_manager(hass)
    await manager.read()
    manager.stage(0, 1)
    await manager.apply()
    paths, scan = discovery
    paths.assert_called_with(hass, manager.address, connectable=True)
    scan.assert_not_awaited()


@pytest.mark.parametrize("apply", [False, True])
async def test_missing_path_stays_disabled_without_io(
    hass, transport, discovery, caplog, apply
):
    manager = make_manager(hass)
    await manager.read()
    manager.stage(0, 1)
    transport.read_config.reset_mock()
    paths, scan = discovery
    paths.return_value = []
    with pytest.raises(HomeAssistantError, match="No connectable path"):
        await (manager.apply() if apply else manager.read())
    scan.assert_awaited_once_with(hass, duration=10)
    assert "No connectable path" in caplog.text
    assert manager.snapshot is None
    assert not manager.writable
    assert not manager.busy
    assert manager.status == "uncertain"
    transport.read_config.assert_not_awaited()
    transport.apply_config.assert_not_awaited()
    with pytest.raises(HomeAssistantError):
        await manager.apply()
    assert scan.await_count == 1


async def test_scan_backend_error_remains_redacted(hass, transport, discovery, caplog):
    manager = make_manager(hass)
    paths, scan = discovery
    paths.return_value = []
    scan.side_effect = HomeAssistantError("SECRET BACKEND PAYLOAD")
    with pytest.raises(HomeAssistantError, match="uncertain") as err:
        await manager.read()
    assert "SECRET" not in str(err.value)
    assert "SECRET" not in caplog.text
    assert not manager.writable
    transport.read_config.assert_not_awaited()
    transport.apply_config.assert_not_awaited()


async def test_scan_cancellation_invalidates_snapshot(hass, transport, discovery):
    manager = make_manager(hass)
    await manager.read()
    paths, scan = discovery
    paths.return_value = []
    scan.side_effect = asyncio.CancelledError
    with pytest.raises(asyncio.CancelledError):
        await manager.apply()
    assert manager.snapshot is None
    assert not manager.writable
    assert not manager.busy
    transport.apply_config.assert_not_awaited()


async def test_path_disappears_before_device_resolution(hass, transport, discovery):
    manager = make_manager(hass)
    paths, scan = discovery
    paths.side_effect = [[], [object()]]
    with patch(
        "custom_components.garnet.manager.bluetooth.async_ble_device_from_address",
        return_value=None,
    ):
        with pytest.raises(HomeAssistantError, match="No connectable path"):
            await manager.read()
    scan.assert_awaited_once_with(hass, duration=10)
    assert not manager.writable
    transport.read_config.assert_not_awaited()
    transport.apply_config.assert_not_awaited()
