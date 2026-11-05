# Casita development installation — 2026-11-05 UTC

Installed and exercised on **Home Assistant 2026.11.0** with a **G708 SOUL**
controller, firmware `G708_REVK_L433_ver_7.0.4`.

Initial verified integration code: `3f36b97a2b0dc17d2564669b295f1d7334356d6a`,
manifest 1.5.0. Follow-up `547384d` adds read-only timeout recovery; see below.
This is a direct, owner-authorized **development installation**, not a merged
release. Fork PR #1 remains review-only until the owner approves merging.
HACS was not installed on this HA instance; no HACS metadata was fabricated.

## Verification

- 95 tests and 5 subtests passed locally; Ruff lint/format passed.
- GitHub test and validation jobs passed, including hassfest and HACS checks.
- Independent review completed; lifecycle and release-retry findings fixed.
- Existing fresh, grey1, grey2 and voltage entity IDs remained intact.
- Passive readings continued; exception statuses now show `open`, `disabled`,
  etc. instead of losing the reason behind a numeric sensor's unknown value.
- Local brand icon endpoint returned HTTP 200/image/png.
- **Three consecutive explicit Read configuration operations succeeded** in HA.
- Staged Grey2=disabled without Apply: pending count became 1 while its physical
  diagnostic remained OK. Discard restored staged Grey2=single and pending=0.
- A fourth controller read after discard confirmed Grey2=single and ready.
- Registry model and firmware updated to the confirmed controller identity.
- No physical Apply/SAVE was exercised through the installed integration merely
  for testing. The same protocol had previously enabled Grey2 in a separate
  authorized direct-BLE operation. Power-cycle persistence remains untested.

## Live-discovered compatibility details

Some first connections exposed only the read characteristic C300. Discovery
now avoids reusing an incomplete service cache and reconnects a bounded number
of times before any command dispatch.

HA sometimes retained fresh advertisement history but had no scanner-local
connectable path. On an explicit Read/Apply only, the manager requests a
10-second active scan if no current path exists, rechecks, and resolves the
device afresh. It does not bypass HA routing or patch the Bluetooth stack.

Requests on one serial connection are spaced at least **three seconds apart**,
matching the successful direct hardware probe cadence. The eight-command Apply
sequence has about 21 seconds of minimum spacing, plus connection/response
time. This is pacing, not an automatic retry of a potentially applied write.

## Backup and rollback

### Completion-audit follow-up

A later audit read timed out waiting for the version response despite the
earlier four successful reads. This is evidence of intermittent failure, not
proof of permanently reliable reads. Revision `547384d` adds a one-second
notification-settle delay and at most one reconnect/retry for a **read-only**
typed timeout, after successful disconnect and a two-second cooldown. Malformed
frames, cancellation and configuration writes are never retried. Local suite:
**103 tests and 11 subtests passed**, with CI test/hassfest/HACS validation green.
Final live audit of this revision passed **four consecutive configuration
reads** (22.2, 7.0, 7.0, 7.0 seconds); each ended ready with Grey2=single.
Installed transport and manager SHA256 hashes match the reviewed worktree.
The original rollback archive was read and validated again. This demonstrates
current behavior, not a guarantee against every future Bluetooth outage.

Original integration 1.4.0 was archived on the trailer at:

```text
/share/garnet-before-soul-20261105.tgz
```

To roll back, validate the backup archive, replace only
`/config/custom_components/garnet` with its archived `garnet` directory, and
restart HA Core. Do not delete the config entry or entity registry; no storage
migration was required. Controller settings are independent of integration
files, so restoring integration code does not undo controller settings.

Temporary Terminal-add-on Python/Bleak probe dependencies are removed after
verification. The backup remains; existing HA and controller credentials were
never placed in source or deployment archives.

## Use

Open the Garnet device in Settings → Devices & services. Its existing owner
device name may still be “Fresh Water Tank Sensor.” Press **Read configuration**,
choose staged sender settings, inspect pending changes, then deliberately press
**Apply and save staged changes**. **Discard changes** affects staging only.
After an HA restart, read again before editing. Only the exact verified firmware
is writable; all other versions retain passive monitoring.
