# Built-in Klipper patching

# Built-in Klipper patching

Forge-X **keeps the printer’s stock host Klipper** at `/opt/klipper/klippy`; it does not build or install a complete upstream Klipper tree. Instead, each normal mod initialization overlays a small, version-sensitive set of replacement modules and extra plugins onto that stock tree. This is the project’s mechanism for carrying printer-specific fixes while retaining the native MCU firmware and making uninstall reversible. See [`docs/MOD_COMPARISON.md`](../../docs/MOD_COMPARISON.md) for the product-level distinction from mods that replace host Klipper and reflash the MCU.

> **Hardware-risk boundary:** a patch can apply mechanically yet be incorrect for a supported stock firmware layout or unsafe in a motion/calibration workflow. Do not edit deployed `/opt/klipper` files as a durable fix; update the overlay and validate on the relevant printer/firmware combination.

## Lifecycle and ownership

The stock-side initializer, [`.shell/S00init`](../../.shell/S00init), binds the real `/opt/klipper` into the Buildroot environment during `init_buildroot`, then calls `apply_klipper_patches` before configuration repair and service startup. The target is therefore the stock-side `/opt/klipper/klippy`, not a copy inside the chroot.

```text
Repository overlay                    Stock host tree
.py/klipper/plugins/*.py  ──symlink→ /opt/klipper/klippy/extras/*.py
.py/klipper/patches/**    ──symlink→ /opt/klipper/klippy/**
                                          └─ original replacement: <file>.bak

boot: cleanup stale overlay → link plugins → back up/replace patch files
      → apply optional tuning edits → start Klipper later in boot
uninstall: remove linked plugins → restore each .bak → disable tuning edits
```

The process is idempotent in the normal supported case:

1. **Clean obsolete overlay entries.** The patcher scans symlinks under the target tree. A symlink with a neighboring `.bak` is treated as a replacement patch; if its relative path is no longer in `patches/`, the original is restored. Other symlinks are treated as plugins and removed when the corresponding plugin filename is no longer present in `plugins/`.
2. **Install plugins.** Every top-level file in [`.py/klipper/plugins/`](../../.py/klipper/plugins/) is force-symlinked into the stock `extras/` directory. Current plugins include the Forge-X parameter interface, power-loss resurrection, Feather screen integration, load-cell tare, MD5 validation, and tone playback.
3. **Replace stock modules.** For each regular file below [`.py/klipper/patches/`](../../.py/klipper/patches/), the patcher preserves the stock target as `<target>.bak` once, removes a prior non-symlink target if necessary, and force-symlinks the repository file into its original relative path. It creates parent directories for nested replacement modules.
4. **Apply adjustable tuning.** Finally it calls [`ztune_klipper.sh apply`](../../.shell/commands/ztune_klipper.sh), which edits two stock files in place according to persistent configuration. These edits are distinct from the symlinked replacement patch set.

Because patch files are linked rather than copied, a later boot points the installed stock tree at the updated repository overlay. It also means an arbitrary manual edit to a linked target edits repository-backed content or is lost when the link is refreshed; use source changes instead.

## What belongs in each overlay area

| Area | Deployment model | Purpose and examples |
|---|---|---|
| [`patches/`](../../.py/klipper/patches/) | Replaces an existing Klipper module; original retained as `.bak` | Core/config parsing and G-code handling; selected `extras` modules such as `virtual_sdcard`, `gcode_shell_command`, LEDs, temperature sensors, resonance/shaper calibration, and statistics. |
| [`plugins/`](../../.py/klipper/plugins/) | Adds a new top-level module under `klippy/extras/` | Forge-X-specific objects loaded through configuration: `mod_params`, `resurrection`, `feather_screen`, `load_cell_tare`, `md5_check`, and `tone_player`; see [Forge-X Klipper extensions](klipper-extensions.md) for their activation, purposes, and commands. |
| [`ztune_klipper.sh`](../../.shell/commands/ztune_klipper.sh) | Modifies unlinked stock `mcu.py` and `toolhead.py` values | Optional reliability tuning for E0011/E0017 behavior; controlled by `tune_klipper`, not a replacement file. |

A replacement must retain the exact stock module path expected by the target firmware. Additions should normally be plugins; do not use a replacement patch merely to introduce a new Forge-X module.

### Patch intent is source-local

The replacement files carry short `Changes:` headers where applicable. Examples include:

- [`patches/gcode.py`](../../.py/klipper/patches/gcode.py) normalizes the raw command used for `M117`/`M118`, and its history includes non-ASCII object-name fixes.
- [`patches/extras/virtual_sdcard.py`](../../.py/klipper/patches/extras/virtual_sdcard.py) excludes hidden files/directories during G-code file enumeration.
- [`patches/extras/gcode_shell_command.py`](../../.py/klipper/patches/extras/gcode_shell_command.py) adds Forge-X background/exclusive execution parameters.
- [`patches/extras/temperature_sensor.py`](../../.py/klipper/patches/extras/temperature_sensor.py) adds a G-code action for out-of-range sensor values.
- [`patches/extras/shaper_calibrate.py`](../../.py/klipper/patches/extras/shaper_calibrate.py) drains a ready child-process result before waiting for the child to exit. Recent history documents why: a result larger than the OS pipe buffer could otherwise block `SHAPER_CALIBRATE` indefinitely.

Keep an explanation close to the changed behavior in the file or commit. The wiki should not become a speculative inventory of every line-level divergence from stock Klipper.

## Optional tuning is not the patch set

`mod_params.json` declares `tune_klipper` as a boolean defaulting to off and migrates the older `fix_e0017` setting to it. When enabled, `ztune_klipper.sh` changes:

| Stock module | Default restored when disabled | Enabled value | Intended symptom |
|---|---:|---:|---|
| `mcu.py`: `TRSYNC_TIMEOUT` | `0.025` | `0.05` | E0011 communication timeout |
| `toolhead.py`: `LOOKAHEAD_FLUSH_TIME` | `0.5` | `0.150` | E0017 move-queue overflow |

[`zchanges.sh`](../../.shell/commands/zchanges.sh) runs the tuning script when the setting changes, reports that Klipper changed, waits briefly, and reboots the printer. Boot also reapplies the selected setting after the symlink overlay is established. Do not confuse `tune_klipper` with `klipper_rt`: the latter changes the Klipper start/restart scheduling path and does not alter these constants.

## Uninstall and recovery behavior

[`.shell/uninstall.sh`](../../.shell/uninstall.sh) implements the inverse operation before removing the mod:

1. Remove each plugin corresponding to a file currently under `plugins/` from stock `klippy/extras/`.
2. For each file currently under `patches/`, move `<target>.bak` back to `<target>` if that backup exists.
3. Invoke `ztune_klipper.sh 0` to restore the two optional tuning constants to their stock values.

That reversal relies on the backup convention created by the patcher. Never delete or overwrite `<target>.bak` in an installed system without a verified recovery plan. The normal dual-boot/recovery procedures remain the operator safety route; see [`docs/UNINSTALL.md`](../../docs/UNINSTALL.md), [`docs/DUAL_BOOT.md`](../../docs/DUAL_BOOT.md), and [`docs/RECOVERY.md`](../../docs/RECOVERY.md).

## Change procedure

1. **Classify the change.** Use `plugins/` for a new Forge-X extension and `patches/` only when replacing an existing native module. Record the target stock path and behavior being changed.
2. **Check target compatibility.** Compare the replacement with the Klipper module/layout shipped by each supported stock firmware. The supported firmware evidence and limits are in [`docs/FIRMWARE_5x_COMPAT.md`](../../docs/FIRMWARE_5x_COMPAT.md); do not assume mainline Klipper is a compatible baseline.
3. **Preserve deployment symmetry.** Confirm the nested relative path works, a stock target exists to back up for replacements, stale-overlay cleanup recognizes the entry, and uninstall can restore it. Consider how removal/renaming behaves on an already patched printer.
4. **Keep settings coherent.** If a patch depends on a user setting, update the parameter schema, Klipper configuration/macro exposure, shell reaction, migration/deprecation behavior, and operator documentation together.
5. **Validate the exact feature on hardware.** A successful boot establishes only that imports/startup worked. Exercise the affected G-code, calibration, print, screen, or recovery path; inspect Klipper logs; and test the supported firmware/device combination. For tuning, test both enable and disable and confirm the reboot/restart result.
6. **Check removal.** On a controlled device or equivalent recovery scenario, verify that old links are cleaned, `.bak` restoration is possible, and the documented uninstall path returns the native host files.

Recent history reinforces this discipline: the shaper-calibration deadlock fix requires `SHAPER_CALIBRATE` testing, while a nearby advanced-pressure-advance replacement was reverted. Patch applicability alone is not enough evidence of safe behavior.

## Investigation entry points

- **Deployment/reversal:** [`.shell/S00init`](../../.shell/S00init) (`apply_klipper_patches`) and [`.shell/uninstall.sh`](../../.shell/uninstall.sh) (`revert_klipper_patches`).
- **Runtime selection:** [`.shell/commands/ztune_klipper.sh`](../../.shell/commands/ztune_klipper.sh), [`.shell/commands/zchanges.sh`](../../.shell/commands/zchanges.sh), and [`mod_params.json`](../../mod_params.json).
- **Compatibility/product rationale:** [`docs/MOD_COMPARISON.md`](../../docs/MOD_COMPARISON.md) and [`docs/FIRMWARE_5x_COMPAT.md`](../../docs/FIRMWARE_5x_COMPAT.md).
- **Validation posture:** [Testing and change guide](../testing-and-change-guide.md).
