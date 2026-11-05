"""Serialized, bounded SOUL BLE transactions, independent of Home Assistant.

The wrapper supplies a discovered BLEDevice. A client_factory(device) must
return an async context manager yielding a connected Bleak-compatible client;
its exit must disconnect, including on cancellation or failure. Each operation
uses a fresh connection. VERSTR requires its exact echo; CFG and SAVE return
direct responses, with optional exact echoes. Idle bytes are discarded and
only one command is in flight. The protocol cannot tag responses: an untagged
OK arriving during a write cannot be uniquely correlated. Full snapshot
readbacks before save and after save are mandatory; ambiguous writes are never
retried. No command or payload is logged.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import TYPE_CHECKING, Any

from .soul_protocol import (
    FrameDecoder,
    SoulConfig,
    SoulProtocolError,
    encode_config,
    parse_config,
    stage_config,
    validate_version,
)

if TYPE_CHECKING:
    from bleak.backends.device import BLEDevice

SERVICE_UUID = "0000a002-0000-1000-8000-00805f9b34fb"
WRITE_UUID = "0000c303-0000-1000-8000-00805f9b34fb"
NOTIFY_UUID = "0000c305-0000-1000-8000-00805f9b34fb"
ClientFactory = Callable[["BLEDevice"], AbstractAsyncContextManager[Any]]


class SoulTransportError(Exception):
    """Safe user-facing failure with explicit mutation uncertainty."""

    def __init__(self, message: str, *, may_have_applied: bool = False) -> None:
        self.may_have_applied = may_have_applied
        super().__init__(message)


class SoulConflictError(SoulTransportError):
    """The full current snapshot differs from the editor's expected snapshot."""


class SoulDiscoveryError(SoulTransportError):
    """Required GATT service/characteristics were not discovered."""


class SoulApplyCancelled(asyncio.CancelledError):
    """Cancellation after write started; still propagates as cancellation."""

    may_have_applied = True


async def _finish_cleanup(awaitable: Any) -> None:
    """Let bounded cleanup finish even if the caller is cancelled again."""
    task = asyncio.ensure_future(awaitable)
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    task.result()
    if cancelled:
        raise asyncio.CancelledError


@asynccontextmanager
async def _default_client_factory(device: BLEDevice):
    from bleak import BleakClient
    from bleak_retry_connector import establish_connection

    client = await establish_connection(
        BleakClient, device, "Garnet SOUL", max_attempts=1, use_services_cache=False
    )
    try:
        yield client
    finally:
        await _finish_cleanup(asyncio.wait_for(client.disconnect(), timeout=10))


class _Exchange:
    def __init__(self, client: Any, timeout: float, chunk_size: int) -> None:
        self.client = client
        self.timeout = timeout
        self.chunk_size = chunk_size
        self.active: asyncio.Queue[str | Exception] | None = None
        self.decoder = FrameDecoder()
        self.received = 0

    def notify(self, _sender: Any, data: bytearray) -> None:
        queue = self.active
        if queue is None:
            return
        try:
            self.received += len(data)
            if self.received > 2048:
                raise SoulProtocolError("Response exceeds transaction limit")
            for frame in self.decoder.feed(data):
                queue.put_nowait(frame)
        except (SoulProtocolError, asyncio.QueueFull):
            # Preserve a terminal error even in a notification flood.
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(SoulProtocolError("Invalid or excessive response data"))

    async def command(
        self,
        command: bytes,
        accepts: Callable[[str], bool],
        *,
        require_echo: bool = False,
    ) -> str:
        queue: asyncio.Queue[str | Exception] = asyncio.Queue(maxsize=32)
        self.decoder = FrameDecoder()
        self.received = 0
        self.active = queue
        echo = command[:-1].decode("ascii")
        echoed = False
        try:
            async with asyncio.timeout(self.timeout):
                for offset in range(0, len(command), self.chunk_size):
                    if offset:
                        await asyncio.sleep(0.05)
                    await self.client.write_gatt_char(
                        WRITE_UUID,
                        command[offset : offset + self.chunk_size],
                        response=False,
                    )
                while True:
                    frame = await queue.get()
                    if isinstance(frame, Exception):
                        raise frame
                    if frame == echo:
                        if echoed:
                            raise SoulProtocolError("Duplicate command echo")
                        echoed = True
                        continue
                    if require_echo and not echoed:
                        raise SoulProtocolError("Missing required command echo")
                    if frame in {"ERROR", "FAIL"}:
                        raise SoulProtocolError("Controller rejected command")
                    if accepts(frame):
                        return frame
                    raise SoulProtocolError("Unexpected response for command")
        except TimeoutError:
            kind = (
                "version read"
                if command == b"AT+VERSTR\r"
                else "configuration read"
                if command == b"AT+CFG?\r"
                else "save"
                if command == b"AT+SAVE=0\r"
                else "configuration write"
            )
            raise SoulTransportError(f"SOUL {kind} response timed out") from None
        finally:
            self.active = None

    async def read(self) -> SoulConfig:
        version = await self.command(
            b"AT+VERSTR\r",
            lambda frame: frame.startswith(("G", "SOUL")),
            require_echo=True,
        )
        validate_version(version)
        frame = await self.command(b"AT+CFG?\r", lambda frame: frame.startswith("CFG="))
        return parse_config(version, frame)


class SoulTransport:
    """One lock per instance; reuse one instance per device in the HA wrapper.

    Timeouts cover connection/notification setup and each command, respectively.
    discovery_attempts bounds reconnects for incomplete discovery ONLY. No read,
    configuration write, or save transaction is retried after dispatch.
    """

    def __init__(
        self,
        device: BLEDevice,
        client_factory: ClientFactory | None = None,
        *,
        timeout: float = 10.0,
        connect_timeout: float = 30.0,
        discovery_attempts: int = 3,
    ) -> None:
        if not 0 < timeout <= 120 or not 0 < connect_timeout <= 120:
            raise ValueError("Timeout must be positive and at most 120 seconds")
        if type(discovery_attempts) is not int or not 1 <= discovery_attempts <= 5:
            raise ValueError("Discovery attempts must be between one and five")
        self.device = device
        self._factory = client_factory or _default_client_factory
        self._timeout = timeout
        self._connect_timeout = connect_timeout
        self._attempts = discovery_attempts
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def _connection(self):
        for attempt in range(self._attempts):
            manager = self._factory(self.device)
            try:
                async with asyncio.timeout(self._connect_timeout):
                    client = await manager.__aenter__()
            except TimeoutError:
                raise SoulTransportError(
                    "SOUL Bluetooth connection timed out"
                ) from None
            try:
                service = client.services.get_service(SERVICE_UUID)
                chars = (
                    {char.uuid.lower(): char for char in service.characteristics}
                    if service
                    else {}
                )
                if (
                    WRITE_UUID not in chars
                    or NOTIFY_UUID not in chars
                    or "write-without-response" not in chars[WRITE_UUID].properties
                    or "notify" not in chars[NOTIFY_UUID].properties
                ):
                    if attempt + 1 == self._attempts:
                        raise SoulDiscoveryError(
                            "Required SOUL characteristics are unavailable"
                        )
                    continue
                limit = getattr(
                    chars[WRITE_UUID], "max_write_without_response_size", 20
                )
                if type(limit) is not int or limit < 1:
                    raise SoulTransportError("Invalid BLE write size limit")
                exchange = _Exchange(client, self._timeout, min(20, limit))
                try:
                    async with asyncio.timeout(self._connect_timeout):
                        await client.start_notify(NOTIFY_UUID, exchange.notify)
                except TimeoutError:
                    raise SoulTransportError(
                        "SOUL notification setup timed out"
                    ) from None
                yield exchange
                return
            finally:
                await _finish_cleanup(
                    asyncio.wait_for(manager.__aexit__(None, None, None), timeout=15)
                )

    async def read_config(self) -> SoulConfig:
        """Read version and validated full snapshot without configuration writes."""
        async with self._lock:
            try:
                async with self._connection() as exchange:
                    return await exchange.read()
            except (SoulTransportError, SoulProtocolError):
                raise
            except Exception as err:  # noqa: BLE001 -- redact backend errors containing payloads
                raise SoulTransportError(
                    f"SOUL read failed ({type(err).__name__}); no configuration write attempted"
                ) from None

    async def apply_config(
        self, expected: SoulConfig, changes: dict[int, int]
    ) -> SoulConfig:
        """Compare full snapshot, stage, write, verify, save, then verify again.

        No-op still reads/compares but never writes/saves. Failure after dispatch
        is uncertain, even if save was acknowledged; there is no rollback claim.
        Cancellation after dispatch raises SoulApplyCancelled.
        """
        target = stage_config(expected, dict(changes))
        started = False
        async with self._lock:
            try:
                async with self._connection() as exchange:
                    current = await exchange.read()
                    if current != expected:
                        raise SoulConflictError(
                            "Configuration changed; refresh before applying"
                        )
                    if target == current:
                        return current
                    command = encode_config(target)
                    started = True
                    await exchange.command(command, lambda frame: frame == "OK")
                    if await exchange.read() != target:
                        raise SoulProtocolError(
                            "Configuration readback mismatch before save"
                        )
                    await exchange.command(
                        b"AT+SAVE=0\r", lambda frame: frame in {"SETTINGS SAVED", "OK"}
                    )
                    final = await exchange.read()
                    if final != target:
                        raise SoulProtocolError(
                            "Configuration readback mismatch after save"
                        )
                    return final
            except asyncio.CancelledError:
                if started:
                    raise SoulApplyCancelled(
                        "Apply cancelled; configuration may have applied"
                    ) from None
                raise
            except Exception as err:
                if started:
                    raise SoulTransportError(
                        "Apply failed; configuration may have applied. Refresh before retrying; no rollback performed",
                        may_have_applied=True,
                    ) from None
                if isinstance(err, (SoulTransportError, SoulProtocolError)):
                    raise
                raise SoulTransportError(
                    "Apply failed; no configuration write attempted"
                ) from None
