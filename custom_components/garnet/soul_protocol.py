"""Pure, conservative codec for the verified Garnet SOUL configuration format.

Only sender state bytes are editable. Unknown bytes (including sender targets)
are opaque and preserved. No legacy SOUL_V1 write format is implemented.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, field

VERIFIED_VERSION = "G708_REVK_L433_ver_7.0.4"
SENDER_NAMES = (
    "Fresh1",
    "Grey1",
    "Black1",
    "Fresh2",
    "Grey2",
    "Black2",
    "Grey3",
    "LPG",
)
DEFAULT_EXTRAS = b"\x01\x00\x00\x00"
MAX_FRAME_BYTES = 128


class SoulProtocolError(ValueError):
    """Invalid or unsupported protocol data; messages never contain payloads."""


class SoulUnsupportedVersion(SoulProtocolError):
    """Firmware is readable but has no verified write encoder."""


def validate_version(version: str) -> str:
    """Accept bounded printable version identifiers, not arbitrary responses."""
    if (
        not isinstance(version, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", version)
        or version in {"OK", "ERROR"}
    ):
        raise SoulProtocolError("Invalid firmware version response")
    return version


@dataclass(frozen=True, slots=True)
class SoulConfig:
    """An immutable full read snapshot; raw is intentionally excluded from repr."""

    version: str
    raw: bytes = field(repr=False)

    def __post_init__(self) -> None:
        validate_version(self.version)
        if not isinstance(self.raw, bytes) or len(self.raw) not in (36, 40):
            raise SoulProtocolError("Configuration must be 36 or 40 bytes")
        for index, state in enumerate(self.sender_states):
            if state not in ((0, 1) if index == 7 else (0, 1, 2)):
                raise SoulProtocolError("Invalid sender state or reserved state bits")
        if len(self.raw) == 40 and any(
            value not in (0, 1) for value in self.raw[37:40]
        ):
            raise SoulProtocolError("Invalid optional configuration flags")

    @property
    def sender_states(self) -> tuple[int, ...]:
        """States in SENDER_NAMES order: disabled=0, single=1, stacked=2."""
        return tuple(self.raw[index * 4 + 3] for index in range(8))

    @property
    def writable(self) -> bool:
        """Whether this exact firmware has a verified encoder."""
        return self.version == VERIFIED_VERSION


def parse_config(version: str, frame: str) -> SoulConfig:
    """Decode a single CFG= frame, without its CR terminator."""
    if (
        not isinstance(frame, str)
        or not frame.startswith("CFG=")
        or len(frame) not in (52, 60)
    ):
        raise SoulProtocolError("Invalid configuration response")
    encoded = frame[4:]
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error):
        raise SoulProtocolError("Invalid configuration encoding") from None
    if base64.b64encode(raw).decode("ascii") != encoded:
        raise SoulProtocolError("Noncanonical configuration encoding")
    return SoulConfig(version, raw)


def stage_config(config: SoulConfig, changes: dict[int, int]) -> SoulConfig:
    """Validate changes and replace only explicitly selected state bytes."""
    if not config.writable:
        raise SoulUnsupportedVersion("Firmware is monitor-only; writes are unsupported")
    raw = bytearray(config.raw)
    for index, state in changes.items():
        if type(index) is not int or not 0 <= index < 8:
            raise SoulProtocolError("Invalid sender index")
        if type(state) is not int or state not in ((0, 1) if index == 7 else (0, 1, 2)):
            raise SoulProtocolError("Invalid sender state")
        raw[index * 4 + 3] = state
    return SoulConfig(config.version, bytes(raw))


def encode_config(config: SoulConfig) -> bytes:
    """Return CR-terminated AT+CFG with canonical 42-byte vendor payload.

    The first release supports only the fixed default save key. There is no
    caller-supplied key, credential storage, or key guessing interface.
    """
    if not config.writable:
        raise SoulUnsupportedVersion("Firmware is monitor-only; writes are unsupported")
    full = config.raw + (DEFAULT_EXTRAS if len(config.raw) == 36 else b"")
    return b"AT+CFG=" + base64.b64encode(b"\x00\x00" + full) + b"\r"


class FrameDecoder:
    """Incremental bounded ASCII CR framing; supports fragmented notifications."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes | bytearray) -> list[str]:
        """Return complete frames, rejecting oversized/non-ASCII input."""
        frames = []
        for value in data:
            if value == 13:
                if self._buffer:
                    frames.append(self._buffer.decode("ascii"))
                    self._buffer.clear()
            elif value == 10 and not self._buffer:
                continue  # Also tolerate CRLF without including LF in frames.
            elif not 32 <= value <= 126:
                raise SoulProtocolError("Non-ASCII or control byte in response")
            else:
                self._buffer.append(value)
                if len(self._buffer) > MAX_FRAME_BYTES:
                    raise SoulProtocolError("Response frame exceeds limit")
        return frames
