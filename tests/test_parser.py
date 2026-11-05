"""Advertisement identity and diagnostic regressions."""

from struct import pack

import pytest
from home_assistant_bluetooth import BluetoothServiceInfo

from custom_components.garnet.const import TANK_FAULTS
from custom_components.garnet.parser import GarnetBluetoothDeviceData
from custom_components.garnet.sensor import sensor_update_to_bluetooth_data_update

ADDRESS = "AA:BB:CC:DD:EE:FF"


def advertisement(name="Soul", value=50, manufacturer=3264, payload=None):
    return BluetoothServiceInfo(
        name=name,
        address=ADDRESS,
        rssi=-60,
        manufacturer_data={
            manufacturer: payload
            or pack("<HxBBBBBBBBBBB", 1, value, 2, 3, 4, 5, 6, 7, 8, 120, 0, 0)
        },
        service_data={},
        service_uuids=[],
        source="test",
    )


@pytest.mark.parametrize(
    "name,model",
    [
        ("Soul", "G708 SOUL"),
        ("Garnet SOUL AABB", "G708 SOUL"),
        ("709-BTP7", "709-BTP7"),
        ("soulless", "709-BTP7"),
    ],
)
def test_model_only_from_soul_name(name, model):
    parser = GarnetBluetoothDeviceData()
    parser.update(advertisement(name))
    assert parser.model == model


@pytest.mark.parametrize("code,status", [*TANK_FAULTS.items(), (255, "unknown_fault")])
def test_faults_are_text_with_no_level(code, status):
    update = sensor_update_to_bluetooth_data_update(
        GarnetBluetoothDeviceData().update(advertisement(value=code))
    )
    values = {key.key: value for key, value in update.entity_data.items()}
    assert values["fresh_tank"] is None
    assert values["fresh_tank_status"] == status


def test_existing_keys_preserved_btp7_and_btp3():
    update = sensor_update_to_bluetooth_data_update(
        GarnetBluetoothDeviceData().update(advertisement("709-BTP7"))
    )
    assert {
        key.key for key in update.entity_data if not key.key.endswith("_status")
    } >= {
        "fresh_tank",
        "fresh_tank2",
        "grey_tank",
        "grey_tank2",
        "black_tank",
        "black_tank2",
        "lpg_tank",
        "battery",
    }
    payload = pack("<3sB3s3s3sB", b"\x01\x00\x00", 0, b"050", b"000", b"100", 0)
    update = sensor_update_to_bluetooth_data_update(
        GarnetBluetoothDeviceData().update(
            advertisement("BTP3", manufacturer=305, payload=payload)
        )
    )
    assert (
        next(
            value
            for key, value in update.entity_data.items()
            if key.key == "fresh_tank"
        )
        == 50
    )
