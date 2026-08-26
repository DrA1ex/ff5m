# Operations and recovery

## Supported installation envelope

The documented target is Flashforge Adventurer 5M / 5M Pro stock firmware from 2.6.5 through tested 5.1.x releases, with at least 512 MB free in `/data` and 128 MB free in `/` ([`docs/INSTALL.md`](../../docs/INSTALL.md)). Firmware 5.x compatibility rationale is in [`docs/FIRMWARE_5x_COMPAT.md`](../../docs/FIRMWARE_5x_COMPAT.md). Do not generalize support beyond this documented range based on the common architecture alone.

Installation uses a FAT32 USB drive and an intact release archive named for the specific model. Follow the procedural source exactly: [`docs/INSTALL.md`](../../docs/INSTALL.md). Afterward, configure slicer G-code/host access and complete calibration before printing.

## Normal boot and updates

- `.shell/S00init` is installed into stock init and performs runtime preparation before services launch.
- `.shell/boot/boot.sh` starts `netd` directly for non-Stock modes. Feather continues offline-capable without waiting; Guppy/headless use the thin `netd-cli wait --timeout 180` and retain the Stock fallback. The wait timeout is boot policy only; `netd` itself keeps the configured network reconnecting indefinitely.
- `.shell/S99root` creates/migrates Moonraker state on first run and starts the chroot stack.
- Moonraker's update manager defines the Forge-X, Fluidd, Mainsail, and GuppyScreen updaters in [`moonraker.conf`](../../moonraker.conf). User instructions say OTA updates are initiated through **Configuration → Software Update** and are limited to the same major version ([`docs/INSTALL.md`](../../docs/INSTALL.md)).

Before an OTA or configuration migration, retain logs and backups. `S00init` rotates stock logs and links mod logs into `/data/logFiles/mod`; `S99root` uses a bootstrap start/stop cycle to initialize/migrate the Moonraker database when absent.

### Early USB boot-media inspection

Special boot flags and a standalone `flashforge_init.sh` are searched by [`.shell/boot/init_boot_flag.sh`](../../.shell/boot/init_boot_flag.sh). Firmware-image detection is delegated to [`.shell/boot/install-image.sh`](../../.shell/boot/install-image.sh) through its `test <directory>` operation, which alone owns the supported `Adventurer5M*.tgz` / `Adventurer5M*.tar.xz` naming and selection policy. The boot-flag script shares [the USB discovery and mount path](configuration-and-printing.md#usb-swap-and-drive-preparation) used by swap initialization, waits for late kernel device-node creation, scans every supported partition (or a whole-disk filesystem), and reads media using `ro` access. Every callback receives the inspected source directory. A temporary inspection mount is released immediately when no flag is found and before every non-image action. For an accepted image, `install-image.sh` selects the file and releases the temporary source mount immediately after staging. A mount that existed before inspection is retained.

`S00init` invokes the existing special-boot handler at the start of `initialize` and only supplies the original `firmwareExe` parent PID. The image callback launches `install-image.sh`; image paths and mount ownership do not leak into `S00init`. The installer reads the stock `MACHINE` / `PID` pair from the active `auto_run.sh` and accepts only an image named for that model. Once an image is accepted, it stops `ffstartup-arm` and terminates the original `firmwareExe` parent before mounting `/data`, so neither a launcher restart nor a mount failure can resume the stock boot. A terminal failure does not return while a live stock parent cannot be stopped. The installer does not start networking. It validates tar member names, checks free space against the uncompressed tar size plus a 16 MiB reserve, replaces `/data/.firmware`, and streams extraction through one screen row updated by percentage. A `.tgz` is deliberately treated as an uncompressed tar; a `.tar.xz` is streamed through `xz -dc` and then tar. The next ordinary boot removes `/data/.firmware` through the same script's `cleanup` operation after `/data` is mounted.

The staged entrypoint policy is fixed and fail closed:

1. If `forge-x-init` exists, it must be a non-symlink executable ELF file and is selected.
2. Otherwise, if `forge-x-init.sh` exists, it must be a non-symlink regular file accepted by `bash -n` and is selected.
3. Only when neither Forge-X entrypoint exists may a non-symlink `flashforge_init.sh` accepted by `bash -n` be selected.

The selected entrypoint runs from `/data/.firmware` with the same stock machine and product arguments (`Adventurer5M 0023` or `Adventurer5MPro 0024`). Immediately before hand-off, the splash stops and Typer draws the firmware-running screen. The installer copies a small runner, Typer, and Typer's required `libstdc++` runtime into the staging directory, starts the runner with detached standard streams, and returns so the `S00init` and `logged` processes can finish. The runner waits until no mount or open file refers to the old mod paths, clears the persisted boot-screen queue, and then starts the selected entrypoint. A non-zero result is recorded in the staging log; after 30 seconds the staged Typer uses only its staged runtime to report that the firmware file failed and the printer may be powered off. A zero result ends the runner because control belongs to the image.

This path intentionally depends on kernel block metadata rather than partition-table parsing. Changes must retain coverage for multiple and logical partitions, digit-suffixed block names, whole-disk filesystems, existing read-only mounts, FAT/ext filesystems, firmware images, and normal boot flags. Unsupported filesystems are skipped so root/eMMC flag checks and the remaining recovery path still run.

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
