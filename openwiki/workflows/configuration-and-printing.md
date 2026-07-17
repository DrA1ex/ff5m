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
| Swap (eMMC, USB, ZRAM, off) | `mod_params.json`; `zchanges.sh`; `.shell/boot/init_swap.sh` | Memory behavior is hardware-sensitive. ZRAM was added in recent history. |
| Camera | `camera` parameter; `zchanges.sh`; `.shell/S98camera` | The hook checks port 8080 and warns if the stock camera is still active. |
| Klipper tuning and real-time scheduling | `tune_klipper`, `klipper_rt`; `zchanges.sh` | Tuning may reboot; `klipper_rt` restarts Klipper. `SCHED_RR` is optional and recent. |
| Config tuning | `tune_config`; `zchanges.sh`; `.py/cfg_backup.py` | Rewrites/restores config through the managed mechanism and restarts Klipper; recalibration follows. |
| Power-loss recovery | `power_loss_recovery`; `zchanges.sh` | Clears saved resurrection state and may restart Klipper outside stock mode. |
| Safety and print defaults | `check_md5`, `cell_weight`, Z-offset, cleaning, KAMP, mesh options | Keep macro expectations synchronized with metadata defaults. |

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
5. **End-of-print flow stops/repositions and optionally schedules motor stop/reboot.** This behavior relies on mod parameters and delayed macros.

The exact slicer configuration, upload route, and checksum post-processing requirements are maintained in [`docs/SLICING.md`](../../docs/SLICING.md). MD5 checking defaults on in metadata but requires the supplied slicer post-processing script; enabling it without that integration will not validate files as intended.

## Calibration and safety

Treat calibration-affecting edits as safety changes. The installation and printing docs require bed-mesh and Z-offset recalibration after installing/uninstalling or changing relevant tuning ([`docs/INSTALL.md`](../../docs/INSTALL.md), [`docs/PRINTING.md`](../../docs/PRINTING.md), [`docs/CALIBRATION.md`](../../docs/CALIBRATION.md)). Display mode affects the mesh/operator workflow: do not assume a mesh profile or stock-screen flow is portable to headless/alternate UI modes.

Recent macro history shows why targeted review matters:

- `CLEAR_NOZZLE` was changed to reset the mesh first, preventing mesh-validation + cleaning from affecting measured Z and potentially scratching hardware.
- Its cooldown condition was tightened so cooldown occurs only when target is below current extruder temperature.
- `BED_LEVEL_SCREWS_TUNE` now owns full screw-calibration preparation: it either calls `CLEAR_NOZZLE` or, with `CLEAN=0`, homes and holds the nozzle at `clear_cooldown_temp`, then delegates measurement to `BED_LEVEL_SCREWS_PROBE`.
- `BED_LEVEL_SCREWS_PROBE` intentionally performs only load-cell tare and corner probing. Feather uses it for **Repeat** while the printer remains homed and at calibration temperatures; it must not be exposed as an unguarded general-purpose calibration button.
- `AUTO_FULL_BED_LEVEL` received a stock/non-stock default-profile correction.
- Interactive `LOAD_MATERIAL` prompts and KAMP Smart Parking were added nearby.

When changing macros, read the caller/callee chain across the active display config, `macros/base.cfg`, any included macro file, and the related docs. Validate the actual motion path—not only template syntax.

## Practical change checklist

- Update `mod_params.json` when a supported setting changes; do not introduce an undocumented mutable key.
- Confirm `[mod_params]` storage and `zchanges.sh` reaction remain coherent.
- Review every display root when shared macros or includes change.
- Update the appropriate user docs (`CONFIGURATION`, `SLICING`, `PRINTING`, `CALIBRATION`, or `SCREEN`) alongside behavior.
- Follow [Testing and change guide](../testing-and-change-guide.md), including physical safety checks.
