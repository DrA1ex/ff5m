# Configuration and printing workflows

## Configuration ownership

Forge-X exposes most supported tuning through Klipper macros, not ad hoc edits to generated state:

```gcode
LIST_MOD_PARAMS
GET_MOD PARAM=<key>
SET_MOD PARAM=<key> VALUE=<value>
```

The declaration/defaults/UI metadata are in [`mod_params.json`](../../mod_params.json). [`macros/base.cfg`](../../macros/base.cfg) configures `[mod_params]` to persist values to `/opt/config/mod_data/variables.cfg` and to invoke `parameter_changed` on changes. [`.shell/commands/zchanges.sh`](../../.shell/commands/zchanges.sh) implements selected parameter side effects.

Use [`docs/CONFIGURATION.md`](../../docs/CONFIGURATION.md) for the operator procedure and override locations. In particular, user-owned Klipper and Moonraker additions belong under the deployed `mod_data` area, not in generated variable state or this repository's defaults.

### Parameters with runtime behavior

| Concern | Source of truth / reaction | Engineering implication |
|---|---|---|
| Display (`STOCK`, `FEATHER`, `HEADLESS`, `GUPPY`) | `mod_params.json`; `zchanges.sh` calls display switching | This changes boot/network/UI assumptions; not cosmetic. |
| Display ECO mode | `display_eco`; dependent `backlight_eco`; Feather's periodic update; Guppy/Headless `reset_screen` delayed G-code | ECO defaults on. Turning it off hides its brightness setting, cancels pending Guppy/Headless dimming, and restores normal brightness. Feather re-observes the parameter each update. Stock remains vendor-owned and ignores it. |
| Swap (eMMC, USB, ZRAM, off) | `mod_params.json`; `zchanges.sh`; `.shell/boot/init_swap.sh` | Memory behavior is hardware-sensitive. ZRAM was added in recent history. |
| Camera | `camera` parameter; `zchanges.sh`; `.shell/S98camera` | The hook checks port 8080 and warns if the stock camera is still active. |
| Klipper tuning and real-time scheduling | `tune_klipper`, `klipper_rt`; `zchanges.sh` | Tuning may reboot; `klipper_rt` restarts Klipper. `SCHED_RR` is optional and recent. |
| Config tuning | `tune_config`; `zchanges.sh`; `.py/cfg_backup.py` | Rewrites/restores config through the managed mechanism and restarts Klipper; recalibration follows. |
| Power-loss recovery | `power_loss_recovery`; `zchanges.sh` | Clears saved resurrection state and may restart Klipper outside stock mode. |
| Safety and print defaults | `check_md5`, `cell_weight`, Z-offset, cleaning, KAMP, mesh options | Keep macro expectations synchronized with metadata defaults. |

### USB swap and drive preparation

USB storage behavior is shared instead of being implemented independently by each caller:

- [`.shell/boot/usb_storage.sh`](../../.shell/boot/usb_storage.sh) discovers external USB block devices, enumerates kernel-recognized partitions, identifies filesystems, and owns temporary mounts.
- [`.shell/boot/init_swap.sh`](../../.shell/boot/init_swap.sh) consumes those candidates for raw swap partitions or swap files and falls back to eMMC if every USB candidate fails.
- [`.shell/commands/zusb.sh`](../../.shell/commands/zusb.sh) backs the destructive `PREPARE_USB` workflow exposed from [`macros/base.cfg`](../../macros/base.cfg).

Discovery deliberately uses `/sys/block/<disk>/<partition>/partition` and `/proc/partitions`, not `sdX` string parsing or `fdisk` output. Consequently MBR/GPT, primary/logical and multiple partitions, digit-suffixed device names such as `mmcblk9p1`, and filesystems written directly to the whole disk follow the same path. USB ancestry must be visible in sysfs. Zero-capacity card-reader slots and non-direct-access SCSI devices such as optical drives are excluded.

Mount access is purpose-specific. Swap and preparation require `rw`; boot-media inspection may reuse an existing `ro` mount or create a temporary `ro` mount. Supported mount filesystems are FAT-family filesystems and ext2/3/4. An empty/late filesystem probe triggers bounded mount attempts in compatibility order. Existing mounts are never unmounted by read-only inspection; only mounts created by the helper are released.

FAT swap files are fully allocated with `dd`, because sparse/fallocate-style files do not provide the stable block mapping required by the target Linux 5.4 FAT driver. EXT uses `fallocate`; a filesystem with `FSTYPE=swap` is activated directly. The BusyBox `mkswap`, `swapon`, and `swapoff` applets are fallbacks because the applets exist on stock firmware even when command symlinks do not.

Drive preparation creates a single MBR partition as FAT32 (`0x0c`) or Linux EXT (`0x83`, formatted ext2 by the available BusyBox `mke2fs`). The two confirmation stages pass a short numeric device fingerprint computed from sysfs path, capacity, model, and the nearest USB serial when available. It is recomputed immediately before erasure, so replacing or removing the selected drive aborts the operation; there is no authentication-token state or temporary token file.

The first prompt is produced by a background shell command. Fluidd requires each `action:prompt_*` line to arrive as a separate Klipper response, matching the repository's existing material dialogs. The opt-in `linewise: True` setting in [`macros/shell.cfg`](../../macros/shell.cfg) uses [`.py/klipper/patches/extras/gcode_shell_command.py`](../../.py/klipper/patches/extras/gcode_shell_command.py) to split only these commands' output into ordered raw response events. Drive formatting uses the threaded `stream` mode so complete output lines reach the console while the process is active without blocking Klipper's reactor. `_PREPARE_USB_EXECUTE` first opens a non-interactive progress prompt; `zusb.sh` replaces it with a success or failure prompt before exiting. Do not make streaming or linewise splitting the default for unrelated shell commands.

## Display modes and configuration roots

The display root determines the starting Klipper configuration:

- [`config/stock.cfg`](../../config/stock.cfg) — default stock-screen bridge.
- [`config/feather.cfg`](../../config/feather.cfg) — Feather UI path.
- [`config/headless.cfg`](../../config/headless.cfg) — no stock UI.
- [`config/guppy.cfg`](../../config/guppy.cfg) — Guppy UI path.

All consume shared macro behavior. In non-stock modes, [`.shell/boot/boot.sh`](../../.shell/boot/boot.sh) initializes network access and starts the MCU/Klipper path itself; if network initialization fails, it switches back to stock config. `zchanges.sh` warns explicitly that users must understand bed mesh, Z-offset, and `START_PRINT`/`END_PRINT` behavior before disabling the stock screen.

## Print lifecycle

1. **Slicer emits Forge-X macros.** The intended entry is `START_PRINT EXTRUDER_TEMP=… BED_TEMP=…`, paired with `END_PRINT`. [`config/stock.cfg`](../../config/stock.cfg) rejects a stock `START_PRINT` call missing either temperature and cancels the print.
2. **Macros capture runtime choices.** `START_PRINT` records temperatures, forced/skip leveling, KAMP, Z-offset, and mesh arguments before delegating to `_START_PRINT`.
3. **Stock-screen bridge forwards lifecycle controls.** `RESUME`, `PAUSE`, and `CANCEL_PRINT` send stock firmware commands through `zsend`; print-file macros run optional MD5 verification and route commands to the stock printing path.
4. **Shared safeguards apply.** [`macros/base.cfg`](../../macros/base.cfg) loads MD5 checking, KAMP, load-cell support, tone support, and safety-oriented motion overrides. For example, its replacement `G28` ensures safe Z/XY parking sequencing.
5. **Pausing is idempotent.** The shared `PAUSE` does nothing when `pause_resume` already reports a paused print, so a repeated pause never parks twice or overwrites the temperature and idle-timeout state `RESUME` restores. Klipper clears the flag only through `RESUME`, `CLEAR_PAUSE`, or `CANCEL_PRINT`, so anything that stops a print outside `pause_resume` has to clear it itself.
6. **End-of-print flow stops/repositions and optionally schedules motor stop/reboot.** This behavior relies on mod parameters and delayed macros.

The exact slicer configuration, upload route, and checksum post-processing requirements are maintained in [`docs/SLICING.md`](../../docs/SLICING.md). MD5 checking defaults on in metadata but requires the supplied slicer post-processing script; enabling it without that integration will not validate files as intended.

## Calibration and safety

Treat calibration-affecting edits as safety changes. The installation and printing docs require bed-mesh and Z-offset recalibration after installing/uninstalling or changing relevant tuning ([`docs/INSTALL.md`](../../docs/INSTALL.md), [`docs/PRINTING.md`](../../docs/PRINTING.md), [`docs/CALIBRATION.md`](../../docs/CALIBRATION.md)). Display mode affects the mesh/operator workflow: do not assume a mesh profile or stock-screen flow is portable to headless/alternate UI modes.

Recent macro history shows why targeted review matters:

- `CLEAR_NOZZLE` was changed to reset the mesh first, preventing mesh-validation + cleaning from affecting measured Z and potentially scratching hardware.
- Its cooldown condition was tightened so cooldown occurs only when target is below current extruder temperature.
- `BED_LEVEL_SCREWS_TUNE` now owns full screw-calibration preparation: it either calls `CLEAR_NOZZLE` or, with `CLEAN=0`, leaves the current bed target untouched, homes, and holds only the nozzle at `clear_cooldown_temp`, then delegates measurement to `BED_LEVEL_SCREWS_PROBE`.
- `BED_LEVEL_SCREWS_PROBE` intentionally performs only load-cell tare and corner probing. Feather uses it for **Repeat** while the printer remains homed and at calibration temperatures; it must not be exposed as an unguarded general-purpose calibration button.
- Feather uses one generic cancellation dialog for print preparation, supported calibration operations, filament workflows, and Cold Pull. `interruptible` operations can stop the current G-code chain at the next managed boundary, while `cancelable` operations additionally own a cleanup domain.
- Homing, probing, and motion remain atomic. An accepted request is delivered at the next context transition, explicit `_CONTEXT_CANCEL_POINT`, or managed wait; an active wait is interrupted immediately through `M108`. While cancellation is pending, the dialog offers Continue Operation or immediate `M112`. A `non_interruptible` operation offers only Continue or `M112`.
- Displayable progress is owned by `operation_context`: reusable macros nest without losing their caller's state, and `_WAIT_TEMPERATURE` temporarily derives heating/cooling states from the heater and accepted range. Cancellation cleanup is registered once per operation type.
- Feather composes the global `global.abort` action from printer activity and page-owned controls. Calibration progress and explicitly long-running G-code declare activity, while `_WAIT_TEMPERATURE`, motion, heater targets, and print state keep ABORT visible on every live page except Home. Idle movement pages arm ABORT only after at least one axis is homed; short bookkeeping G-code does not affect visibility. Touch handling dispatches `M112` before command-depth checks, debounce, or the 80 ms button-feedback callback, so it never waits for the active calibration's G-code mutex.
- `AUTO_FULL_BED_LEVEL` received a stock/non-stock default-profile correction.
- Interactive `LOAD_MATERIAL` prompts and KAMP Smart Parking were added nearby.

When changing macros, read the caller/callee chain across the active display config, `macros/base.cfg`, any included macro file, and the related docs. Validate the actual motion path—not only template syntax.

## Practical change checklist

- Update `mod_params.json` when a supported setting changes; do not introduce an undocumented mutable key.
- Confirm `[mod_params]` storage and `zchanges.sh` reaction remain coherent.
- Review every display root when shared macros or includes change.
- Update the appropriate user docs (`CONFIGURATION`, `SLICING`, `PRINTING`, `CALIBRATION`, or `SCREEN`) alongside behavior.
- Follow [Testing and change guide](../testing-and-change-guide.md), including physical safety checks.
