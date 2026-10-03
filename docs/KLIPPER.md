# Klipper fixes and AD5M-specific hardening

Forge-X deliberately keeps the FlashForge host Klipper shipped with the Adventurer 5M / 5M Pro and backports selected fixes from newer upstream Klipper versions instead of replacing the complete host and MCU stack with Klipper 0.13.

This is a deliberate engineering choice for the AD5M.

The printer has very limited host resources, including only 128 MiB of RAM. A complete newer Klipper userspace and the surrounding services increase the resource budget on hardware that already operates close to its practical limits during demanding prints. Forge-X therefore takes the narrower approach: keep the vendor-compatible Klipper/MCU protocol and bring over the fixes that matter for this printer.

This choice also reflects repeated feedback from Forge-X users who prefer improving the existing AD5M stack instead of requiring a Klipper 0.13 MCU migration.

The result is not stock Klipper left untouched. Forge-X replaces selected host-side Klipper modules with reviewed patches and backports while keeping the original MCU firmware. The original files are backed up and restored on uninstall.

> [!NOTE]
> The FlashForge Klipper base is from the Klipper 0.11 era. Forge-X does **not** claim that this makes it equivalent to a complete modern Klipper tree. The goal is different: retain the lightweight, hardware-compatible base and selectively bring in fixes and features that are useful on the AD5M.

## The `Timer too close` problem

The AD5M can hit `Timer too close` during especially demanding G-code processing. A reproducible example was reported in [Forge-X issue #40](https://github.com/DrA1ex/ff5m/issues/40): the supplied G-code failed at the same point on Forge-X and on stock firmware.

The underlying problem is host starvation. On this hardware, sufficiently dense G-code can keep Klippy busy buffering and processing motion long enough that other reactor work does not run in time. Once the host falls behind far enough, the MCU rejects work that is scheduled too close to the current MCU clock and shuts down with `Timer too close`.

Forge-X 1.4.2 fixes this at the Klipper host level instead of working around it only by disabling services or moving to a heavier full Klipper 0.13 stack.

The key fix is a semantic backport of upstream Klipper commit [50cb362 — “toolhead: Make sure to periodically yield to other tasks when buffering moves”](https://github.com/Klipper3d/klipper/commit/50cb362234f277a4923f1d59d21473d1e0317f62).

The patched `toolhead.py` periodically yields to the reactor while buffering dense motion. This prevents long stretches of move processing from starving MCU communication, timers, and other reactor work.

Forge-X also yields while loading chunks from virtual SD, and carries newer reactor event-dispatch fixes described below. Together, these changes address the host scheduling failure mode that caused heavy files to reproduce `Timer too close` on the AD5M.

The original reproducer from issue #40 was later marked fixed in Forge-X 1.4.2 Beta 3.

> [!IMPORTANT]
> `Timer too close` is a generic Klipper shutdown reason and can still be caused by unrelated hardware or system faults such as unstable power, severe I/O stalls, or other host failures. The Forge-X fix targets the reproducible AD5M host-starvation case caused by heavy G-code processing.

## Upstream Klipper fixes backported by Forge-X

The following fixes are taken from newer upstream Klipper and adapted to the FlashForge 0.11-era host code where necessary.

| Area | Forge-X change | Upstream source |
| --- | --- | --- |
| Heavy G-code / reactor starvation | Periodically yield from `toolhead._check_stall()` while buffering moves so dense G-code cannot starve other reactor tasks. This is the core fix for the reproducible heavy-file `Timer too close` failure. | [50cb362](https://github.com/Klipper3d/klipper/commit/50cb362234f277a4923f1d59d21473d1e0317f62) |
| Reactor fd dispatch | Unifies ready-fd dispatch behavior and prevents stale events from being delivered after a descriptor was removed or reused. Forge-X additionally snapshots registrations for each ready batch before dispatch. | [bb88985](https://github.com/Klipper3d/klipper/commit/bb88985b8d48fa7505fee116eec1c4902361f95d), prerequisites [136283bd](https://github.com/Klipper3d/klipper/commit/136283bd144530f53e96604957d11d8d1b5fe1da) and [0d5b96a6](https://github.com/Klipper3d/klipper/commit/0d5b96a6013570c0ff2519a3c03efdd25055ab36) |
| Multi-MCU homing reports | Staggers TRSYNC reports during multi-MCU homing to reduce communication bursts. | [dab39c02](https://github.com/Klipper3d/klipper/commit/dab39c02cd5681d530388fbaa82d0dc7f31d2e26) |
| Multi-MCU homing report margin | Increases the TRSYNC state-reporting interval margin used during multi-MCU homing. | [1ea9f3aa](https://github.com/Klipper3d/klipper/commit/1ea9f3aa35d7232ee5d106541c5a98c4348c6e47) |
| Endstop/TRSYNC scheduling | Schedules TRSYNC timeout setup at the endstop start clock instead of using an incorrect request clock during homing. | [8e6e467](https://github.com/Klipper3d/klipper/commit/8e6e467ebc16f93ab01ed63c55d24af52b020b54) |
| Probe accuracy | Returns to the starting XY position between `PROBE_ACCURACY` attempts instead of retracting only in Z. | [a353efa](https://github.com/Klipper3d/klipper/commit/a353efa5b617817988128fbd08b2e294624b0531) |
| TMC direction inversion | Corrects the `stepper:set_dir_inverted` event name so TMC phase tracking is refreshed after direction inversion. | [8dd798e](https://github.com/Klipper3d/klipper/commit/8dd798ebb8ad37dcfd6d6d825ff7a7482f3ddafe) |
| TMC enable/disable race | Serializes asynchronous TMC enable/disable processing with a reactor mutex to avoid state races. | [8ea7be5](https://github.com/Klipper3d/klipper/commit/8ea7be5dd7139215a4b3e06b2512d2c3f88c9b5b) |
| Servo PWM timing | Aligns servo updates to software-PWM cycle boundaries, adapted to the older direct-PWM API used by the FlashForge Klipper tree. | [2b4c55f](https://github.com/Klipper3d/klipper/commit/2b4c55ffd118a4982cfb04a01052746bb8cb45d9) |
| Empty G-code button templates | Does not queue an empty rendered button template on the G-code mutex. | [b8c936f](https://github.com/Klipper3d/klipper/commit/b8c936f7b86013677fe3b4b59fcb6d626531a962) |
| Low `max_power` heaters | Scales the minimum PWM-change threshold by `max_power`, fixing heaters configured below 0.05 power not starting correctly. | [01f089e](https://github.com/Klipper3d/klipper/commit/01f089e71039c1f9e63b8f1def218e6f465b41da) |
| Virtual SD file offsets | Counts UTF-8 byte length rather than Python string character count when advancing virtual-SD file position. This is important for non-ASCII G-code comments/object names and recovery positions under Python 3. | [600e89a](https://github.com/Klipper3d/klipper/commit/600e89ae8c759613a3c6fc2b24d0a62d00e6baf2) |
| Input-shaper calibration parameters | Uses `square_corner_velocity` from the printer configuration when calculating shaper recommendations. | [72b301a](https://github.com/Klipper3d/klipper/commit/72b301a2859c3f7ed26d802dd52fc495eef6c353) |
| Input-shaper background-process deadlock | Drains a large calibration result from the multiprocessing pipe before waiting for the child process to exit, preventing `SHAPER_CALIBRATE` from hanging when the result exceeds the OS pipe buffer. Forge-X carried this fix before it later appeared upstream. | [baf188bd](https://github.com/Klipper3d/klipper/commit/baf188bd62bb9c82775d679cf0db30a72f9e9173) |
| Dynamic pressure advance | Backports the host-side support that allows pressure-advance values to change at an exact print time while queued motion already exists. The backport includes matching Python/CFFI pieces and a compatible `c_helper.so`. | [c84d78f3](https://github.com/Klipper3d/klipper/commit/c84d78f3f169bc5163d11b74837f9880b0b7dba4) |

## AD5M-specific Klipper fixes and adaptations

Not every Forge-X replacement is an upstream cherry-pick. Several changes exist specifically because the FlashForge host tree, UI, hardware, or low-memory environment needs behavior that mainline Klipper does not provide in exactly the same form.

| Area | Forge-X behavior |
| --- | --- |
| `TRSYNC_TIMEOUT` tuning | `mcu.py` can select the stock `0.025 s` timeout or the Forge-X tuned `0.05 s` value through `tune_klipper`. This is aimed at the AD5M `E0011` communication-timeout failure mode. |
| Lookahead tuning | `toolhead.py` can select the stock `0.5 s` lookahead flush value or the Forge-X `0.150 s` value through `tune_klipper`. This reduces move-queue pressure associated with `E0017` on the AD5M. |
| Virtual-SD cooperative yielding | `virtual_sdcard.py` explicitly yields to the reactor after reading a G-code chunk and while another G-code mutex user is pending. This reduces the chance that long file-processing bursts monopolize Klippy. |
| Homing diagnostics | `homing.py` retains the vendor-era homing calculations but adds detailed logging of homing/probing moves, trigger positions, halt positions, retracts, and final coordinates. |
| MCU homing diagnostics | `mcu.py` logs homing setup, final TRSYNC stop reasons, and endstop trigger timestamps without flooding the log with periodic TRSYNC reports. |
| G-code coordinate safeguard | `gcode_move.py` adds `RESET_GCODE_ORIGIN`, which clears temporary `G92` coordinate shifts while preserving configured offsets. Forge-X uses it as a safeguard around homing-related workflows. |
| Coordinate diagnostics | `gcode_move.py` exposes and logs coordinate-base/origin state used when investigating unexpected physical positions. |
| G-code command discovery | `gcode.py` backports the newer `gcode/commands` status interface expected by current Moonraker/Mainsail integrations. |
| G-code error handling | The Forge-X G-code replacement avoids repeated error output in selected exception paths and contains AD5M/Forge-X immediate-command handling changes. |
| Statistics with logging disabled | `statistics.py` continues calling subsystem stats callbacks even when periodic stats logging is disabled. This prevents internal producers from being left undrained while still suppressing the log output. |
| Shaper plot/calculation path | The resonance/shaper replacements export precalculated data and include Forge-X-specific handling used by the AD5M calibration workflow. |
| Shell-command execution | `gcode_shell_command.py` adds background, queued, streaming, and daemon-style execution needed by Forge-X services while avoiding synchronous shell work blocking Klippy. It also includes cleanup/timeout handling for long-running commands. |
| Generic sensor threshold actions | `temperature_sensor.py` can execute controlled G-code when a generic sensor exceeds a configured threshold. Forge-X uses this for AD5M load-cell / collision-related safety logic. |
| LED inversion | `led.py` supports inverted LED outputs required by the printer hardware/configuration. |
| Cold-extrusion service override | `heaters.py` contains an internal runtime-only extrusion-temperature override for guided service/calibration operations. It is intentionally not exposed as a general G-code command. |
| Hidden virtual-SD entries | Hidden files and directories are filtered from the virtual-SD file list. |
| Print-time metadata | `virtual_sdcard.py` extracts estimated printing/extrusion time from supported slicer footers for local UI progress reporting. |
| Configuration/logging compatibility | `configfile.py` carries the Forge-X-compatible configuration parser/logging behavior used with the FlashForge config layout. |
| Button handling compatibility | `buttons.py` is retained as part of the patched module set needed by the printer's button/input path. |

## Complete replacement inventory

Forge-X 1.4.2 overlays the following existing Klipper files on a supported AD5M installation:

```text
klippy/configfile.py
klippy/gcode.py
klippy/mcu.py
klippy/reactor.py
klippy/toolhead.py

klippy/chelper/__init__.py
klippy/chelper/c_helper.so

klippy/kinematics/extruder.py

klippy/extras/buttons.py
klippy/extras/gcode_button.py
klippy/extras/gcode_move.py
klippy/extras/gcode_shell_command.py
klippy/extras/heaters.py
klippy/extras/homing.py
klippy/extras/led.py
klippy/extras/probe.py
klippy/extras/resonance_tester.py
klippy/extras/servo.py
klippy/extras/shaper_calibrate.py
klippy/extras/statistics.py
klippy/extras/temperature_sensor.py
klippy/extras/tmc.py
klippy/extras/virtual_sdcard.py
```

The Python pressure-advance backport and `c_helper.so` must be treated as one matched host-side change. The helper is not MCU firmware.

Forge-X-specific plugins such as Feather, power-loss recovery, load-cell tare, `mod_params`, and checksum support are separate additions under `.py/klipper/plugins/`; they are not included in the replacement inventory above because they do not replace stock Klipper modules.

## How patches are applied

Forge-X does not overwrite the stock Klipper tree irreversibly.

During initialization, the matching repository files are symlinked over their stock paths and the original files are retained as `.bak` files. On uninstall, Forge-X removes its links and restores the original files.

This keeps the host patch set auditable and makes rollback possible without reflashing the printer's MCU.

For contributor-level details, see [Built-in Klipper patching](../openwiki/workflows/klipper-patching.md).
