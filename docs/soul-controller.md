# SOUL controller: staged sender settings

## Scope and evidence

The fork retains passive Garnet monitoring and adds explicit controller
transactions. The write encoder is gated to **`G708_REVK_L433_ver_7.0.4`**
exactly. Unknown firmware is monitor-only, even if its configuration can be
read. Legacy SOUL_V1 writes are not implemented.

The protocol was derived from static extraction of the manufacturer's
**LaunchPC** application, with implementation and tests reviewed on 2026-11-05.
Static extraction is not proof of every hidden setting, persistence across a
power cycle, tank-reading accuracy, or the cause of a manufacturer-app crash.
No power-cycle durability claim is made by an acknowledged save/readback.

## User workflow

1. Press **Read configuration**. This explicitly connects and retrieves the
   firmware identifier and configuration snapshot. Startup does not perform
   this transaction; existing advertisement monitoring continues.
2. On the supported firmware, choose staged modes for **Fresh1, Grey1, Black1,
   Fresh2, Grey2, Black2, Grey3, LPG**. Water/waste senders allow `disabled`,
   `single`, or `stacked`; LPG allows only `disabled` or `single`.
3. These selections are local, in-memory edits. They do not write to Bluetooth,
   and they do not survive an integration unload/restart. Selecting the original
   value removes that pending edit.
4. **Apply and save** is available only after a supported read and with pending
   changes. It reads the full current snapshot again and rejects a changed
   snapshot before writing. It then writes the staged configuration, verifies
   a full readback, saves, and verifies another full readback.
5. **Discard staged changes** clears local edits without Bluetooth I/O or
   physical rollback. A successful new read also clears staged changes.

Read/apply operations disable competing controls in this manager. On failure
or cancellation, its snapshot becomes uncertain and another successful read is
required before applying. A failure after dispatch can mean settings already
changed; the implementation does not automatically retry the write or undo it.
Close other controller apps while managing settings and check the result.

These are serialized local operations with optimistic snapshot checks, **not
an atomic transaction with the controller**. Other clients can race between
read, write, save, and readback. There is no guarantee of zero external races.

## Wire format and preservation

- Service `0000a002-0000-1000-8000-00805f9b34fb`; write-without-response
  characteristic `0000c303-0000-1000-8000-00805f9b34fb`; notifications on
  `0000c305-0000-1000-8000-00805f9b34fb`.
- Commands and responses are bounded ASCII CR-delimited frames. Fragmented
  notifications are reassembled; writes are chunked to at most 20 bytes.
- `AT+VERSTR` requires its exact echo before the firmware response.
  `AT+CFG?` accepts a direct `CFG=<base64>` response without an echo.
  Optional exact echoes are accepted for configuration and save commands.
- A read configuration contains 36 or 40 bytes. Eight four-byte sender records
  precede the remaining opaque fields. Only each sender's state byte at
  zero-based offset `4 * index + 3` is editable: `0` disabled, `1` single,
  `2` stacked (not valid for LPG).
- Thus a Grey2-only edit changes **byte 19**, preserving the other **35 bytes**
  of a 36-byte read, or the other 39 bytes of a 40-byte read. Sender targets and
  other hidden fields are not exposed as editable settings.
- `AT+CFG=<base64>` writes a **little-endian 16-bit key of zero plus a 40-byte
  configuration body**. For a 36-byte read, the encoder appends the vendor
  defaults **`01 00 00 00`**; a 40-byte read retains its existing optional bytes.
  These defaults are an encoding assumption derived from the vendor software,
  not validation of all hidden fields. No user-supplied key or key guessing is
  supported.
- Configuration write expects `OK`. `AT+SAVE=0` accepts **`SETTINGS SAVED`**
  (and `OK`); it does not require an echoed command or a `CFG=` save response.
  Explicit configuration reads provide verification before and after save.
- Comparisons include the firmware and complete raw snapshot. If a controller
  changes its readback length from 36 to 40 bytes after writing, that is a
  mismatch, not silently normalized success. Failure is reported as uncertain.
- The protocol has no transaction identifiers. A delayed untagged `OK` cannot
  be uniquely correlated. Snapshot readbacks reduce risk but cannot supply
  protocol-level atomicity or prove persistence after power loss.

## Diagnostics

Tank fault codes are diagnostic states, not percentages:

| Code | State | Code | State |
| --- | --- | --- | --- |
| 101 | short | 107 | no_calibration |
| 102 | open | 108 | checksum |
| 103 | bit_counter | 109 | range |
| 104 | stack | 110 | disabled |
| 105 | missing_top | 111 | initializing |
| 106 | missing_bottom | 112 | bad_id |

The BTP7 parser reports out-of-range tank values as unknown level readings with
separate diagnostic status; unrecognized fault codes become `unknown_fault`.
Grey3 level and diagnostic status monitoring are implemented for SOUL only,
not for 709-BTP7 devices.

Downloaded integration diagnostics are allowlisted to `management_status`,
`pending_changes` (count only), `writable`, and `busy`. They exclude raw
configuration, addresses, keys, and credentials. Controller command payloads
are not logged by the transport. This does not claim that all Home Assistant
Bluetooth debug logging is address-free.

See the [README](../README.md) for HACS migration, release-backed installation,
test commands, and rollback. An HA backup does not back up physical controller
settings; retain a separate record of intended sender modes before applying.
