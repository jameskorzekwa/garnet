"""Const definitions."""

from enum import StrEnum

from homeassistant.const import Platform

DOMAIN = "garnet"

PLATFORMS = [Platform.SENSOR, Platform.SELECT, Platform.BUTTON]
MANAGERS = "garnet_managers"
TANK_FAULTS = {
    101: "short",
    102: "open",
    103: "bit_counter",
    104: "stack",
    105: "missing_top",
    106: "missing_bottom",
    107: "no_calibration",
    108: "checksum",
    109: "range",
    110: "disabled",
    111: "initializing",
    112: "bad_id",
}

MFR_ID_BTP3 = 305
MFR_ID_BTP7 = 3264


def is_soul_name(name: str | None) -> bool:
    """Only a SOUL name identifies a potential management-capable controller."""
    return bool(name and "soul" in name.casefold().replace("-", " ").split())


class GarnetTypes(StrEnum):
    """Garnet value types."""

    FRESH_TANK = "fresh_tank"
    FRESH_TANK2 = "fresh_tank2"
    BLACK_TANK = "black_tank"
    BLACK_TANK2 = "black_tank2"
    GREY_TANK = "grey_tank"
    GREY_TANK2 = "grey_tank2"
    GREY_TANK3 = "grey_tank3"
    LPG_TANK = "lpg_tank"
    LPG_2_TANK = "lpg_2_tank"
    GALLEY_TANK = "galley_tank"
    GALLEY_2_TANK = "galley_2_tank"
    TEMP = "temp"
    TEMP_2 = "temp_2"
    TEMP_3 = "temp_3"
    TEMP_4 = "temp_4"
    CHEMICAL_TANK = "chemical_tank"
    CHEMICAL_2_TANK = "chemical_2_tank"
    BATTERY = "battery"
