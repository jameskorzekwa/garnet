# Garnet SeeLeveL

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![CI and release](https://github.com/jameskorzekwa/garnet/actions/workflows/release.yml/badge.svg)](https://github.com/jameskorzekwa/garnet/actions/workflows/release.yml)

Home Assistant Bluetooth monitoring for Garnet SeeLeveL, with explicit, staged
SOUL sender configuration controls. This is the
[`jameskorzekwa/garnet`](https://github.com/jameskorzekwa/garnet) fork of
[`danTapps/garnet`](https://github.com/danTapps/garnet). Upstream authorship,
brand artwork, and the [MIT license](LICENSE) are retained. This independent
integration is not supported or warranted by Garnet.

## Devices and controls

- Existing 709-BTP3 and 709-BTP7 advertisement monitoring is retained.
- The BTP7 parser exposes Fresh 1/2, Grey 1/2, Black 1/2, LPG, and voltage.
  Grey 3 level and diagnostic status monitoring are implemented for SOUL only;
  they are not exposed for 709-BTP7 devices.
- SOUL controls include eight staged sender selectors, **Read configuration**,
  **Apply and save**, and **Discard staged changes**. Read is mandatory first;
  changing a selector never writes to the device.
- Writing is gated to the exact firmware `G708_REVK_L433_ver_7.0.4`.
  Other firmware is monitor-only; no speculative write format is used.
- Startup preserves passive monitoring and does not read or write controller
  configuration automatically.

See [SOUL controller operation and protocol limits](docs/soul-controller.md)
before using configuration controls. Tests establish software behavior, not
full tank-reading accuracy or the cause of any manufacturer-app crash.

## Installation and migration with HACS

Version **1.5.0** is the prepared fork version; setting the manifest version does
not itself publish a release. Install a published release when available.

1. Make a Home Assistant backup and record the currently installed repository
   and release version before switching implementations.
2. In HACS, add `https://github.com/jameskorzekwa/garnet` as a custom repository
   of category **Integration**. If migrating from upstream, switch HACS's
   repository tracking to the fork rather than maintaining two installations.
   Only one provider of `custom_components/garnet` may be installed at a time.
3. Download the desired published fork release and restart Home Assistant core.
4. Keep the existing Garnet integration entry. The domain stays **`garnet`**;
   existing monitoring entity identifiers are preserved. There is no need to
   delete devices, entities, or the existing config entry to change repositories.
5. Confirm passive monitoring, then explicitly read configuration if you want
   to use the supported SOUL controls. A connectable Bluetooth adapter or proxy
   must be available for controller transactions.

For rollback, select the recorded previous release in HACS (switch repository
tracking back to upstream if appropriate), download it, and restart core. Use
the pre-change backup if needed. Software rollback or restoring an HA backup
does **not** undo settings already saved in the physical controller.

Manual development installation: copy the contents of
`custom_components/garnet/` to `<HA config>/custom_components/garnet/`, then
restart core. Use HACS releases for maintained installations.

## Development and verification

Use Python **3.14**:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements-test.txt
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
.venv/bin/python -m pytest -q
```

The published `pytest-homeassistant-custom-component==0.13.375` harness pins
HA **2026.10.4**. Keep that supported combination for unit and HA integration
tests. HA **2026.11.0** is a separate import-only compatibility check, not a
claim that the harness runs against it; the optional workflow-dispatch input
`check_latest_import` runs that check in a separate environment.

Every push/PR runs Ruff lint, Ruff format, pytest, hassfest, and HACS validation;
changes confined to `AGENTS.md` paths are ignored. Weekly validation and manual
runs never publish. After successful main push CI in this fork, the release job
uses an untagged manifest version unchanged; if already tagged on an older
commit, it advances the patch to the next unused version. It packages
`garnet.zip`, retains upstream Sigstore signing, and publishes the matching
tag/release using the default `GITHUB_TOKEN`. Token-authenticated bump commits
do not trigger recursive workflows. A retry can recover only an immediate,
single-parent bot bump of the triggering commit, with the expected identity and
message, only the manifest changed, and only a semantic patch-version change in
its JSON. It checks out that bump and reuses a matching HEAD tag. Other main
advances or conflicting recovery tags are rejected. Repository rules must permit
the bot's version-bump push.
