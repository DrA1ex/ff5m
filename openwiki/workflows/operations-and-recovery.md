# Operations and recovery

## Supported installation envelope

The documented target is Flashforge Adventurer 5M / 5M Pro stock firmware from 2.6.5 through tested 5.1.x releases, with at least 512 MB free in `/data` and 128 MB free in `/` ([`docs/INSTALL.md`](../../docs/INSTALL.md)). Firmware 5.x compatibility rationale is in [`docs/FIRMWARE_5x_COMPAT.md`](../../docs/FIRMWARE_5x_COMPAT.md). Do not generalize support beyond this documented range based on the common architecture alone.

Installation uses a FAT32 USB drive and an intact release archive named for the specific model. Follow the procedural source exactly: [`docs/INSTALL.md`](../../docs/INSTALL.md). Afterward, configure slicer G-code/host access and complete calibration before printing.

## Normal boot and updates

- `.shell/S00init` is installed into stock init and performs runtime preparation before services launch.
- `.shell/S55boot` decides whether to pursue the mod path or stock fallback; alternate screens require successful network initialization.
- `.shell/S99root` creates/migrates Moonraker state on first run and starts the chroot stack.
- Moonraker's update manager defines the Forge-X, Fluidd, Mainsail, and GuppyScreen updaters in [`moonraker.conf`](../../moonraker.conf). User instructions say OTA updates are initiated through **Configuration → Software Update** and are limited to the same major version ([`docs/INSTALL.md`](../../docs/INSTALL.md)).

Before an OTA or configuration migration, retain logs and backups. `S00init` rotates stock logs and links mod logs into `/data/logFiles/mod`; `S99root` uses a bootstrap start/stop cycle to initialize/migrate the Moonraker database when absent.

### Operational caveat: release channels

`moonraker.conf` currently configures Forge-X as a Git updater on `channel: dev`, while `version.txt` states 1.4.1. The inspected repository does not establish whether every released device should consume that channel. Do not “fix” this discrepancy without an explicit release-policy decision.

## Diagnostic order

1. **Preserve evidence:** do not delete install, boot, uninstall, or recovery logs. The root README calls this out because logs can make recovery possible.
2. **Check the current mode:** determine whether a `SKIP_MOD` condition, failed mod boot, network setup in alternate mode, or a service failure is responsible.
3. **Use the lowest-risk documented escape hatch:** the project offers dual boot/failsafe before destructive recovery.
4. **Use the documented recovery ladder:** debug image, dry-run image, full recovery, uninstall image, factory image; only then consider UART/U-Boot/FEL work.

## Dual boot, uninstall, and recovery

[`docs/DUAL_BOOT.md`](../../docs/DUAL_BOOT.md) documents temporary mod bypass mechanisms. Internally, `S00init` recognizes requested skip and a boot-failure flag, and `S99root` suppresses Buildroot services when the corresponding `/tmp` markers exist. Skipping the mod is not equivalent to reverting all configuration—keep that distinction clear in support/runbook work.

[`docs/UNINSTALL.md`](../../docs/UNINSTALL.md) is the source for `REMOVE_MOD` / soft removal and USB/image paths. Use it rather than constructing an uninstall from repository scripts.

[`docs/RECOVERY.md`](../../docs/RECOVERY.md) establishes escalation:

1. Flash debug image and retain diagnostics.
2. Run dry recovery to identify corruption without restoring it.
3. Use full recovery if appropriate.
4. Try uninstall/factory firmware images if recovery does not restore operation.
5. Use UART/U-Boot only for systems that cannot use USB recovery; the guide requires **3.3 V** UART and warns that 5 V can damage the board. FEL is last-resort territory.

These are hardware operations. This wiki records the routing, but the user-facing guide has the necessary physical instructions and must be followed verbatim.

## Operational integrations

| Integration | Operational source | Notes |
|---|---|---|
| Moonraker / Fluidd / Mainsail | `moonraker.conf`, `.root/S65moonraker`, `.root/S70httpd`, `docs/INSTALL.md` | API at port 7125; static UIs are HTTP paths. API key auth is disabled in default config. |
| Stock / Feather / Guppy / headless screens | `config/*.cfg`, `.shell/boot/boot.sh`, `docs/SCREEN.md` | Screen selection changes network and calibration workflow. |
| Camera | `.shell/S98camera`, `.shell/commands/zchanges.sh`, `docs/CAMERA.md` | Ensure stock camera stream is disabled before enabling mod camera. |
| Remote SSH / Telegram timelapse | `.shell/S98zssh`, `telegram/`, `docs/TELEGRAM.md` | User-side SSH material is mutable/private and intentionally not inspected here. |
| Cloud blocking | `.shell/S00init`, `mod_params.json`, `docs/CONFIGURATION.md` | `block_cloud` is opt-in; it changes `/etc/hosts` entries, not routing/firewall. |

## Runbook for changes to boot or operations

- Review the stock-mode fallback and verify that a failure cannot strand the device before recovery access.
- Exercise the default stock mode and each touched alternative-display path.
- If service order, update behavior, mounts, or persistent locations change, review `S00init`, `S55boot`, `S99root`, `.root/start.sh`, and the relevant docs together.
- Add/adjust recovery instructions whenever an operational change affects rollback, diagnostics, or required recalibration.
- Apply the validation guidance in [Testing and change guide](../testing-and-change-guide.md).
