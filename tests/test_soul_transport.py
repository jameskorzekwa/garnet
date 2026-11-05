"""Mock BLE transactional tests; never scan or use a physical radio."""

import asyncio
import base64
import importlib
import unittest
from contextlib import asynccontextmanager
from itertools import pairwise
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from test_soul_protocol import BASELINE, PACKAGE, p

t = importlib.import_module(f"{PACKAGE}.soul_transport")


class ConnectionFactoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_never_reuses_partial_service_cache(self):
        client = SimpleNamespace(disconnect=AsyncMock())
        device = object()
        with patch(
            "bleak_retry_connector.establish_connection",
            new=AsyncMock(return_value=client),
        ) as connect:
            async with t._default_client_factory(device) as actual:
                self.assertIs(actual, client)
            self.assertFalse(connect.call_args.kwargs["use_services_cache"])
            self.assertEqual(connect.call_args.kwargs["max_attempts"], 1)
            client.disconnect.assert_awaited_once()


class FakeRadio:
    def __init__(self):
        self.raw = BASELINE
        self.version = p.VERIFIED_VERSION
        self.commands = []
        self.chunks = []
        self.echo_all = False
        self.write_limit = 20
        self.connections = 0
        self.disconnects = 0
        self.active = 0
        self.max_active = 0
        self.incomplete = 0
        self.ack = b"SETTINGS SAVED"
        self.hook = None
        self.write_started = asyncio.Event()
        self.save_started = asyncio.Event()
        self.saved = False

    @asynccontextmanager
    async def factory(self, _device):
        self.connections += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        client = FakeClient(self, self.connections <= self.incomplete)
        try:
            yield client
        finally:
            await asyncio.sleep(0)
            self.active -= 1
            self.disconnects += 1


class FakeClient:
    def __init__(self, radio, incomplete):
        self.radio = radio
        self.callback = None
        self.pending = bytearray()
        chars = (
            []
            if incomplete
            else [
                SimpleNamespace(
                    uuid=t.WRITE_UUID,
                    properties=["write-without-response"],
                    max_write_without_response_size=radio.write_limit,
                ),
                SimpleNamespace(uuid=t.NOTIFY_UUID, properties=["notify"]),
            ]
        )
        self.services = SimpleNamespace(
            get_service=lambda uuid: (
                SimpleNamespace(characteristics=chars)
                if uuid == t.SERVICE_UUID
                else None
            )
        )

    async def start_notify(self, uuid, callback):
        assert uuid == t.NOTIFY_UUID
        self.callback = callback
        self.emit(b"OK\rCFG=stale\rpartial")  # Idle frames and partial bytes.

    def emit(self, data):
        self.callback(None, bytearray(data))

    async def write_gatt_char(self, uuid, command, *, response):
        assert uuid == t.WRITE_UUID
        assert response is False
        radio = self.radio
        assert len(command) <= min(20, radio.write_limit)
        radio.chunks.append((bytes(command), asyncio.get_running_loop().time()))
        self.pending.extend(command)
        if not self.pending.endswith(b"\r"):
            return
        command = bytes(self.pending)
        self.pending.clear()
        radio.commands.append(command)
        await asyncio.sleep(0)
        if radio.hook and await radio.hook(self, command):
            return
        if command == b"AT+VERSTR\r":
            reply = radio.version.encode()
        elif command == b"AT+CFG?\r":
            reply = b"CFG=" + base64.b64encode(radio.raw)
        elif command.startswith(b"AT+CFG="):
            radio.write_started.set()
            payload = base64.b64decode(command[7:-1], validate=True)
            assert len(payload) == 42 and payload[:2] == b"\0\0"
            radio.raw = payload[2 : 2 + len(radio.raw)]
            reply = b"OK"
        elif command == b"AT+SAVE=0\r":
            radio.save_started.set()
            radio.saved = True
            reply = radio.ack
        else:
            raise AssertionError("Unexpected command")
        # Real firmware echoes VERSTR only; split all responses across packets.
        echo = command if radio.echo_all or command == b"AT+VERSTR\r" else b""
        wire = echo + reply + b"\r"
        for offset in range(0, len(wire), 2):
            self.emit(wire[offset : offset + 2])


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.radio = FakeRadio()
        self.transport = t.SoulTransport(object(), self.radio.factory, timeout=0.5)
        self.config = p.SoulConfig(p.VERIFIED_VERSION, BASELINE)

    def assert_clean(self):
        self.assertEqual(self.radio.active, 0)
        self.assertEqual(self.radio.connections, self.radio.disconnects)

    async def test_read(self):
        self.assertEqual(await self.transport.read_config(), self.config)
        self.assertEqual(self.radio.commands, [b"AT+VERSTR\r", b"AT+CFG?\r"])
        self.assert_clean()

    async def test_apply_readback_before_save_finalread(self):
        result = await self.transport.apply_config(self.config, {4: 1})
        self.assertEqual(result, p.stage_config(self.config, {4: 1}))
        self.assertEqual(
            self.radio.commands,
            [
                b"AT+VERSTR\r",
                b"AT+CFG?\r",
                p.encode_config(result),
                b"AT+VERSTR\r",
                b"AT+CFG?\r",
                b"AT+SAVE=0\r",
                b"AT+VERSTR\r",
                b"AT+CFG?\r",
            ],
        )
        self.assertTrue(self.radio.saved)
        self.assertEqual(result.raw, BASELINE[:19] + b"\1" + BASELINE[20:])
        chunks = self.radio.chunks[2:6]
        self.assertEqual([len(data) for data, _ in chunks], [20, 20, 20, 4])
        self.assertEqual(b"".join(data for data, _ in chunks), p.encode_config(result))
        for (_, previous), (_, current) in pairwise(chunks):
            self.assertGreaterEqual(current - previous, 0.049)
        self.assert_clean()

    async def test_save_ok_variant_and_40_bytes(self):
        self.radio.ack = b"OK"
        self.radio.raw += b"\7\1\1\1"
        current = await self.transport.read_config()
        result = await self.transport.apply_config(current, {4: 1})
        self.assertEqual(result.raw[36:], b"\7\1\1\1")
        self.assert_clean()

    async def test_noop_does_not_write_or_save(self):
        self.assertEqual(
            await self.transport.apply_config(self.config, {4: 0}), self.config
        )
        self.assertEqual(len(self.radio.commands), 2)
        self.assert_clean()

    async def test_full_snapshot_conflict(self):
        raw = bytearray(BASELINE)
        raw[16] = 0x12  # Opaque target change, not a sender state.
        self.radio.raw = bytes(raw)
        with self.assertRaises(t.SoulConflictError) as caught:
            await self.transport.apply_config(self.config, {4: 1})
        self.assertFalse(caught.exception.may_have_applied)
        self.assertEqual(len(self.radio.commands), 2)
        self.assert_clean()

    async def test_unknown_firmware_readable_write_rejected(self):
        self.radio.version = "G708_NEW_ver_9.0"
        config = await self.transport.read_config()
        with self.assertRaises(p.SoulUnsupportedVersion):
            await self.transport.apply_config(config, {4: 1})
        self.assertEqual(self.radio.connections, 1)

    async def test_incomplete_discovery_reconnect_before_commands(self):
        self.radio.incomplete = 2
        await self.transport.apply_config(self.config, {4: 1})
        self.assertEqual(self.radio.connections, 3)
        self.assertEqual(sum(c.startswith(b"AT+CFG=") for c in self.radio.commands), 1)
        self.assert_clean()

    async def test_discovery_retries_bounded_without_any_commands(self):
        self.radio.incomplete = 10
        with self.assertRaises(t.SoulDiscoveryError):
            await self.transport.read_config()
        self.assertEqual(self.radio.connections, 3)
        self.assertEqual(self.radio.commands, [])
        self.assert_clean()

    async def test_save_ack_cannot_acknowledge_config_write(self):
        async def hook(client, command):
            if command.startswith(b"AT+CFG="):
                client.emit(b"SETTINGS SAVED\r")
                return True
            return False

        self.radio.hook = hook
        with self.assertRaises(t.SoulTransportError) as caught:
            await self.transport.apply_config(self.config, {4: 1})
        self.assertTrue(caught.exception.may_have_applied)
        self.assertEqual(self.radio.connections, 1)
        self.assertFalse(self.radio.saved)
        self.assert_clean()

    async def test_read_timeout_no_retry(self):
        async def hook(client, command):
            return True

        self.radio.hook = hook
        with self.assertRaises(t.SoulTransportError) as caught:
            await self.transport.read_config()
        self.assertFalse(caught.exception.may_have_applied)
        self.assertEqual(self.radio.connections, 1)
        self.assert_clean()

    async def test_mismatch_prevents_save(self):
        async def hook(client, command):
            if command.startswith(b"AT+CFG="):
                client.emit(command + b"OK\r")  # ACK but deliberately don't apply.
                return True
            return False

        self.radio.hook = hook
        with self.assertRaises(t.SoulTransportError) as caught:
            await self.transport.apply_config(self.config, {4: 1})
        self.assertTrue(caught.exception.may_have_applied)
        self.assertFalse(self.radio.saved)
        self.assert_clean()

    async def test_final_mismatch_is_uncertain(self):
        async def hook(client, command):
            if command == b"AT+CFG?\r" and self.radio.saved:
                client.emit(command + b"CFG=" + base64.b64encode(BASELINE) + b"\r")
                return True
            return False

        self.radio.hook = hook
        with self.assertRaises(t.SoulTransportError) as caught:
            await self.transport.apply_config(self.config, {4: 1})
        self.assertTrue(caught.exception.may_have_applied)
        self.assertTrue(self.radio.saved)
        self.assert_clean()

    async def test_cancellation_during_write_disconnects_without_retry(self):
        async def hook(client, command):
            if command.startswith(b"AT+CFG="):
                self.radio.write_started.set()
                await asyncio.Future()
            return False

        self.radio.hook = hook
        task = asyncio.create_task(self.transport.apply_config(self.config, {4: 1}))
        await self.radio.write_started.wait()
        task.cancel()
        with self.assertRaises(t.SoulApplyCancelled) as caught:
            await task
        self.assertTrue(caught.exception.may_have_applied)
        self.assertEqual(self.radio.connections, 1)
        self.assert_clean()

    async def test_cancellation_during_read_disconnects(self):
        reading = asyncio.Event()

        async def hook(client, command):
            reading.set()
            await asyncio.Future()

        self.radio.hook = hook
        task = asyncio.create_task(self.transport.read_config())
        await reading.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_clean()

    async def test_concurrent_applies_serialized_second_conflicts(self):
        results = await asyncio.gather(
            self.transport.apply_config(self.config, {4: 1}),
            self.transport.apply_config(self.config, {3: 1}),
            return_exceptions=True,
        )
        self.assertIsInstance(results[0], p.SoulConfig)
        self.assertIsInstance(results[1], t.SoulConflictError)
        self.assertEqual(self.radio.max_active, 1)
        self.assertEqual(sum(c.startswith(b"AT+CFG=") for c in self.radio.commands), 1)
        self.assert_clean()

    async def test_read_limits_and_malformed_data_disconnect(self):
        for data in (b"x" * 129, b"\xff", b"x\r" * 1100):

            async def hook(client, command, data=data):
                client.emit(data)
                return True

            self.radio.hook = hook
            with self.assertRaises(p.SoulProtocolError):
                await self.transport.read_config()
            self.assert_clean()

    async def test_no_payload_in_error(self):
        async def hook(client, command):
            if command.startswith(b"AT+CFG="):
                raise RuntimeError(command.decode())
            return False

        self.radio.hook = hook
        with self.assertRaises(t.SoulTransportError) as caught:
            await self.transport.apply_config(self.config, {4: 1})
        self.assertNotIn("AT+", str(caught.exception))
        self.assertTrue(caught.exception.__suppress_context__)
        self.assert_clean()

    async def test_legacy_version_is_read_only(self):
        self.radio.version = "SOUL_V1"
        config = await self.transport.read_config()
        self.assertFalse(config.writable)
        with self.assertRaises(p.SoulUnsupportedVersion):
            await self.transport.apply_config(config, {4: 1})

    async def test_foreign_echo_cannot_supply_ack(self):
        async def hook(client, command):
            if command.startswith(b"AT+CFG="):
                client.emit(command + b"AT+OTHER\rOK\r")
                return True
            return False

        self.radio.hook = hook
        with self.assertRaises(t.SoulTransportError) as caught:
            await self.transport.apply_config(self.config, {4: 1})
        self.assertTrue(caught.exception.may_have_applied)
        self.assertFalse(self.radio.saved)
        self.assert_clean()

    async def test_write_timeout_can_have_applied_and_is_not_retried(self):
        async def hook(client, command):
            if command.startswith(b"AT+CFG="):
                self.radio.raw = base64.b64decode(command[7:-1])[2:38]
                client.emit(command)  # Applied, echo received, ACK lost.
                return True
            return False

        self.radio.hook = hook
        with self.assertRaises(t.SoulTransportError) as caught:
            await self.transport.apply_config(self.config, {4: 1})
        self.assertTrue(caught.exception.may_have_applied)
        self.assertEqual(self.radio.raw[19], 1)
        self.assertEqual(len(self.radio.commands), 3)
        self.assertFalse(self.radio.saved)
        self.assert_clean()

    async def test_save_timeout_is_uncertain_and_not_retried(self):
        async def hook(client, command):
            if command == b"AT+SAVE=0\r":
                client.emit(command)
                return True
            return False

        self.radio.hook = hook
        with self.assertRaises(t.SoulTransportError) as caught:
            await self.transport.apply_config(self.config, {4: 1})
        self.assertTrue(caught.exception.may_have_applied)
        self.assertEqual(self.radio.commands.count(b"AT+SAVE=0\r"), 1)
        self.assertEqual(self.radio.connections, 1)
        self.assert_clean()

    async def test_optional_echoes_full_roundtrip(self):
        self.radio.echo_all = True
        result = await self.transport.apply_config(self.config, {4: 1})
        self.assertEqual(result.raw, BASELINE[:19] + b"\1" + BASELINE[20:])
        self.assertTrue(self.radio.saved)
        self.assert_clean()

    async def test_smaller_characteristic_limit(self):
        self.radio.write_limit = 7
        transport = t.SoulTransport(object(), self.radio.factory, timeout=2)
        result = await transport.apply_config(self.config, {4: 1})
        self.assertEqual(result.raw[19], 1)
        self.assertTrue(all(len(data) <= 7 for data, _ in self.radio.chunks))
        self.assertTrue(self.radio.saved)
        self.assert_clean()

    async def test_larger_characteristic_limit_still_caps_at_twenty(self):
        self.radio.write_limit = 64
        await self.transport.apply_config(self.config, {4: 1})
        self.assertEqual(max(len(data) for data, _ in self.radio.chunks), 20)
        self.assert_clean()

    async def test_invalid_characteristic_limit_fails_before_commands(self):
        self.radio.write_limit = 0
        with self.assertRaises(t.SoulTransportError) as caught:
            await self.transport.apply_config(self.config, {4: 1})
        self.assertFalse(caught.exception.may_have_applied)
        self.assertEqual(self.radio.chunks, [])
        self.assertEqual(self.radio.connections, 1)
        self.assert_clean()

    async def test_stale_idle_ok_and_partial_bytes_are_discarded(self):
        async with self.radio.factory(object()) as client:
            exchange = t._Exchange(client, 0.5, 20)
            await client.start_notify(t.NOTIFY_UUID, exchange.notify)
            await exchange.command(
                b"AT+VERSTR\r",
                lambda frame: frame == p.VERIFIED_VERSION,
                require_echo=True,
            )
            client.emit(b"OK\rCFG=old\rpartial")
            frame = await exchange.command(
                b"AT+CFG?\r", lambda frame: frame.startswith("CFG=")
            )
            self.assertEqual(p.parse_config(p.VERIFIED_VERSION, frame), self.config)
        self.assert_clean()

    async def test_verstr_requires_exact_echo(self):
        for prefix in (b"", b"AT+OTHER\r"):

            async def hook(client, command, prefix=prefix):
                client.emit(prefix + p.VERIFIED_VERSION.encode() + b"\r")
                return True

            self.radio.hook = hook
            with self.assertRaises(p.SoulProtocolError):
                await self.transport.read_config()
            self.assert_clean()

    async def test_ok_cannot_acknowledge_config_read(self):
        async def hook(client, command):
            if command == b"AT+CFG?\r":
                client.emit(b"OK\r")
                return True
            return False

        self.radio.hook = hook
        with self.assertRaises(p.SoulProtocolError):
            await self.transport.read_config()
        self.assertEqual(self.radio.connections, 1)
        self.assert_clean()

    async def test_config_frame_cannot_acknowledge_save(self):
        async def hook(client, command):
            if command == b"AT+SAVE=0\r":
                client.emit(b"CFG=" + base64.b64encode(self.radio.raw) + b"\r")
                return True
            return False

        self.radio.hook = hook
        with self.assertRaises(t.SoulTransportError) as caught:
            await self.transport.apply_config(self.config, {4: 1})
        self.assertTrue(caught.exception.may_have_applied)
        self.assertEqual(self.radio.commands.count(b"AT+SAVE=0\r"), 1)
        self.assert_clean()
