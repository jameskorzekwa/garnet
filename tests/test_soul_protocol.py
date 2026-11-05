"""Radio/HA-free protocol tests, also runnable with stdlib unittest."""

import base64
import importlib
import sys
import types
import unittest
from pathlib import Path

# Load the actual modules without executing the integration's HA __init__.
PACKAGE = "_garnet_soul_unit"
if PACKAGE not in sys.modules:
    package = types.ModuleType(PACKAGE)
    package.__path__ = [
        str(Path(__file__).resolve().parents[1] / "custom_components/garnet")
    ]
    sys.modules[PACKAGE] = package
p = importlib.import_module(f"{PACKAGE}.soul_protocol")

BASE64 = "3RkKAd1LXwHdS18B3RkKAN1LXwDdS18A3UtfAN0ZCgEAAAAA"
BASELINE = base64.b64decode(BASE64)


class ProtocolTests(unittest.TestCase):
    def test_baseline_and_exact_grey2_diff(self):
        config = p.parse_config(p.VERIFIED_VERSION, "CFG=" + BASE64)
        self.assertEqual(config.sender_states, (1, 1, 1, 0, 0, 0, 0, 1))
        changed = p.stage_config(config, {4: 1})
        self.assertEqual(
            [i for i, (a, b) in enumerate(zip(config.raw, changed.raw)) if a != b], [19]
        )
        self.assertEqual(changed.raw[16:19], b"\xddK_")
        self.assertEqual(config.raw, BASELINE)
        self.assertNotIn(BASE64, repr(config))
        self.assertNotIn(repr(BASELINE), repr(config))

    def test_canonical_vendor_payload(self):
        command = p.encode_config(p.SoulConfig(p.VERIFIED_VERSION, BASELINE))
        self.assertTrue(command.startswith(b"AT+CFG="))
        self.assertTrue(command.endswith(b"\r"))
        payload = base64.b64decode(command[7:-1], validate=True)
        self.assertEqual(len(payload), 42)
        self.assertEqual(payload, b"\0\0" + BASELINE + b"\1\0\0\0")

    def test_full_40_preserves_every_extra_byte(self):
        raw = BASELINE + b"\x07\x01\x01\x01"
        config = p.parse_config(
            p.VERIFIED_VERSION, "CFG=" + base64.b64encode(raw).decode()
        )
        changed = p.stage_config(config, {4: 2})
        payload = base64.b64decode(p.encode_config(changed)[7:-1])
        self.assertEqual(payload[2:], raw[:19] + b"\2" + raw[20:])

    def test_opaque_bytes_are_preserved_not_interpreted(self):
        raw = BASELINE[:32] + b"\x80\xff\x10\x22"
        self.assertEqual(
            p.stage_config(p.SoulConfig(p.VERIFIED_VERSION, raw), {4: 1}).raw[32:],
            raw[32:],
        )

    def test_unsupported_version_is_monitor_only(self):
        config = p.SoulConfig("G708_OTHER_ver_8.0", BASELINE)
        self.assertFalse(config.writable)
        for action in (
            lambda: p.stage_config(config, {}),
            lambda: p.encode_config(config),
        ):
            with self.assertRaises(p.SoulUnsupportedVersion):
                action()

    def test_malformed_frames_and_versions(self):
        for frame in (
            "",
            BASE64,
            "CFG=" + "!" * 48,
            "CFG=" + BASE64 + "\r",
            "CFG=" + "A" * 56,
        ):
            with (
                self.subTest(frame_length=len(frame)),
                self.assertRaises(p.SoulProtocolError),
            ):
                p.parse_config(p.VERIFIED_VERSION, frame)
        for version in ("", "OK", "ERROR", "x\r", "x" * 97):
            with self.assertRaises(p.SoulProtocolError):
                p.SoulConfig(version, BASELINE)
        # Nonzero unused base64 pad bits are not canonical.
        encoded = base64.b64encode(BASELINE + b"\1\0\0\0").decode()
        with self.assertRaises(p.SoulProtocolError):
            p.parse_config(p.VERIFIED_VERSION, "CFG=" + encoded[:-3] + "B==")

    def test_invalid_states_reserved_bits_and_shapes(self):
        for index, state in ((0, 3), (4, 0x81), (7, 2), (7, 255)):
            raw = bytearray(BASELINE)
            raw[index * 4 + 3] = state
            with self.assertRaises(p.SoulProtocolError):
                p.SoulConfig(p.VERIFIED_VERSION, bytes(raw))
        for raw in (
            BASELINE[:-1],
            BASELINE + b"\0",
            bytearray(BASELINE),
            BASELINE + b"\1\2\0\0",
        ):
            with self.assertRaises(p.SoulProtocolError):
                p.SoulConfig(p.VERIFIED_VERSION, raw)
        for changes in (
            {8: 1},
            {-1: 0},
            {True: 1},
            {"4": 1},
            {4: True},
            {4: 3},
            {7: 2},
        ):
            with self.assertRaises(p.SoulProtocolError):
                p.stage_config(p.SoulConfig(p.VERIFIED_VERSION, BASELINE), changes)

    def test_framing_split_multi_and_limits(self):
        decoder = p.FrameDecoder()
        self.assertEqual(decoder.feed(b"AT+VER"), [])
        self.assertEqual(
            decoder.feed(b"STR\rG708\r\nOK\r"), ["AT+VERSTR", "G708", "OK"]
        )
        for data in (b"x" * 129, b"\xff", b"x\n"):
            with self.assertRaises(p.SoulProtocolError):
                p.FrameDecoder().feed(data)
