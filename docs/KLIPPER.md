# Klipper fixes and AD5M-specific hardening

Forge-X keeps the Klipper that FlashForge ships with the AD5M (0.11 generation) and adds fixes from newer upstream Klipper. The MCU firmware is not changed, and uninstalling restores the original files.

**What you get:**

- A fix for `Timer too close` shutdowns caused by very dense G-code (Forge-X 1.4.2 and newer).
- Fixes for homing and probing with the printer's two MCUs.
- Optional tuning for the E0011 (communication timeout) and E0017 (move queue overflow) errors.
- Correct G-code file positions for files with non-ASCII characters, such as non-English object names.
- Adaptive Pressure Advance (dynamic pressure advance backported from Klipper 0.13), so the adaptive PA profiles from recent OrcaSlicer versions can be used.
- A faster input shaper calculation and extra diagnostic logs for homing and probing.
- Less garbage-collector work after startup and fewer configuration copies while rendering macros.
- Stock-screen connections preserved during `RESTART`, `FIRMWARE_RESTART`, and `SAVE_CONFIG` (see [Screens](SCREEN.md#klipper-restart-and-saving)).
- Probe cleanup after failed measurements, so the next calibration can start normally.

**What you need to do:** nothing for most fixes, they are applied automatically. To enable the timing tuning for E0011 / E0017, run:

```gcode
SET_MOD PARAM=tune_klipper VALUE=1
```

**Contents:**
[Approach](#approach) ·
[`Timer too close`](#the-timer-too-close-problem) ·
[All changes](#all-changes) ·
[Source history](#source-history-of-the-patches) ·
[Replaced files](#replaced-files) ·
[Applying and removing the patches](#applying-and-removing-the-patches)

## Approach

Forge-X keeps the FlashForge host Klipper and the stock MCU firmware, and replaces selected host-side modules with reviewed patches and backports. It does not replace the whole stack with Klipper 0.13.

The AD5M is an older printer. Klipper 0.13 is a good release, but for most tasks the Klipper that FlashForge ships, together with the fixes and features backported by Forge-X, is enough, and there is no need for 0.13 at the moment. If a real need appears, Forge-X can move to it. Users also asked to improve the existing stack instead of requiring an MCU migration. See [Why doesn't Forge-X use Klipper 0.13?](FAQ.md#why-doesnt-forge-x-use-klipper-013)

The FlashForge base is from the Klipper 0.11 era, and this is not equivalent to a complete modern Klipper tree. The aim is to keep the lightweight, hardware-compatible base and bring in the fixes and features that are useful on the AD5M.

## The `Timer too close` problem

The AD5M can hit `Timer too close` during demanding G-code. A reproducible example is [issue #40](https://github.com/DrA1ex/ff5m/issues/40): the supplied G-code failed at the same point on Forge-X and on the stock firmware.

The cause is host starvation. Sufficiently dense G-code can keep Klippy busy buffering motion for so long that other reactor work does not run in time. When the host falls behind far enough, the MCU receives work scheduled too close to its clock and shuts down.

Forge-X 1.4.2 fixes this in Klipper itself: `toolhead.py` periodically yields to the reactor while buffering dense motion (a backport of upstream commit [50cb362](https://github.com/Klipper3d/klipper/commit/50cb362234f277a4923f1d59d21473d1e0317f62)), together with the newer reactor event-dispatch fixes. The reproducer from issue #40 was marked fixed in Forge-X 1.4.2 Beta 3.

> [!IMPORTANT]
> `Timer too close` is a generic Klipper shutdown reason and can still be caused by unrelated hardware or system faults, such as unstable power or severe I/O stalls. The fix targets the reproducible host-starvation case caused by heavy G-code. See also the [F.A.Q.](FAQ.md#what-causes-timer-too-close-or-mcu-errors-e0011)

## All changes

Each row is one topic. "Based on" is the upstream Klipper commit, where there is one. Some commits are cherry-picked, and others are adapted to the 0.11 code; the commit message in the history says which. "History" links to published commits in the [history repository](#source-history-of-the-patches); the local overlay includes further changes listed in that section.

| Area | What changes | Based on | History |
| --- | --- | --- | --- |
| `Timer too close` (dense G-code) | `toolhead.py` yields to the reactor while buffering moves, so dense G-code cannot starve MCU communication and timers. Extra buffering yields wait for at least 20 ms of reactor dispatch work and are suppressed during low-latency joystick submissions. | [50cb362](https://github.com/Klipper3d/klipper/commit/50cb362234f277a4923f1d59d21473d1e0317f62) (adapted) | [`c7adba6`](https://github.com/DrA1ex/klipper-ad5m/commit/c7adba6747aa27b83b33e54fb81fd8662a5202fe) |
| Reactor events | `reactor.py` does not deliver stale events after a file descriptor was removed or reused. Each ready batch is bound to the current registrations. | [bb88985](https://github.com/Klipper3d/klipper/commit/bb88985) and its prerequisites 136283bd, 0d5b96a6 | [`65060d3`](https://github.com/DrA1ex/klipper-ad5m/commit/65060d3cb1f48da081d57926db18db389d3e93d3), [`655e168`](https://github.com/DrA1ex/klipper-ad5m/commit/655e168665273ca13385a9125a4b7ba99b160ab7), [`7cb0998`](https://github.com/DrA1ex/klipper-ad5m/commit/7cb099828c231e1c30de375fe5a49391daecb799), [`e14be6f`](https://github.com/DrA1ex/klipper-ad5m/commit/e14be6fbf14f4eb89f5086f40213abf1874617b1) |
| E0017 (move queue) | `toolhead.py`: with `tune_klipper` the lookahead flush time is `0.150 s` instead of the FlashForge `0.5 s`. `buffer_time_high` defaults to 1.5 s instead of 2.0 s, a value chosen by testing on the printer. | [16fc46fe](https://github.com/Klipper3d/klipper/commit/16fc46fe) (lookahead value) | [`f5ef7ca`](https://github.com/DrA1ex/klipper-ad5m/commit/f5ef7ca5bb430a2b5070766319a8d5455b6783e1), [`b46e060`](https://github.com/DrA1ex/klipper-ad5m/commit/b46e06015d16b57d3439f75886cedd4a67200a24) |
| Low-buffer log | `toolhead.py` logs `LOW_BUFFER_FLUSH` with the timing values when the print buffer falls below `buffer_time_low`. It shows after the fact that the host fell behind. | Forge-X | [`f8982c1`](https://github.com/DrA1ex/klipper-ad5m/commit/f8982c1d7eedfd9c7519c6ba8c635fc103d981ff) |
| E0011 (communication timeout) | `mcu.py`: TRSYNC reports are staggered during multi-MCU homing, the reporting margin is larger, and the timeout is set at the endstop start clock. With `tune_klipper` the TRSYNC timeout is `0.05 s` instead of `0.025 s`, which gives the two MCUs more communication margin. | [dab39c02](https://github.com/Klipper3d/klipper/commit/dab39c02), [1ea9f3aa](https://github.com/Klipper3d/klipper/commit/1ea9f3aa), [8e6e467](https://github.com/Klipper3d/klipper/commit/8e6e467) | [`8878676`](https://github.com/DrA1ex/klipper-ad5m/commit/887867660529247a1c9addcb17518c1d2cbdb949), [`778e927`](https://github.com/DrA1ex/klipper-ad5m/commit/778e9279f2a2b100130def2f68c9e6bd2f7cf833), [`40bba59`](https://github.com/DrA1ex/klipper-ad5m/commit/40bba594667d8934520fb1f362d1604ac997531c), [`49599c5`](https://github.com/DrA1ex/klipper-ad5m/commit/49599c57831033f9f430106fc45f4791de338445) |
| Homing diagnostics | `homing.py` and `mcu.py` log homing moves, start/trigger/halt step positions, endstop trigger times, and TRSYNC stop reasons, without logging periodic reports. The homing calculations are unchanged. | Forge-X | [`fe27357`](https://github.com/DrA1ex/klipper-ad5m/commit/fe273577936b2fd79a74ed50e87e23d6b1d5b502), [`cdfbd0c`](https://github.com/DrA1ex/klipper-ad5m/commit/cdfbd0cb321eaf8479d9ad9a90ac52c1a75e5563) |
| Probe accuracy | `PROBE_ACCURACY` returns to the starting XY position between attempts instead of retracting only in Z. Locally owned multi-probe sessions are closed even if sampling, tolerance checks, or retracts fail. | [a353efa](https://github.com/Klipper3d/klipper/commit/a353efa) | [`29a9b78`](https://github.com/DrA1ex/klipper-ad5m/commit/29a9b7847d21093a487f2d4e660465fce0720fb0) |
| TMC drivers | The `stepper:set_dir_inverted` event name is fixed, so phase tracking is refreshed after direction inversion. Enable and disable are serialized with a reactor mutex. The driver error checks that the stock firmware disables are active again. | [8dd798e](https://github.com/Klipper3d/klipper/commit/8dd798e), [8ea7be5](https://github.com/Klipper3d/klipper/commit/8ea7be5) | [`7c44b94`](https://github.com/DrA1ex/klipper-ad5m/commit/7c44b94335a9a3f3570dbbfa3392d13417bb5e99), [`fb2d4a1`](https://github.com/DrA1ex/klipper-ad5m/commit/fb2d4a175a77b5630867df3dbc0dfb909523bed5), [`7e0f882`](https://github.com/DrA1ex/klipper-ad5m/commit/7e0f8822df6d32aa95894ab8d3d7cca95e31ed5f) |
| Servo PWM | `servo.py` and `mcu.py` align servo updates to software-PWM cycle boundaries, adapted to the older direct-PWM API. | [2b4c55f](https://github.com/Klipper3d/klipper/commit/2b4c55f) | [`54cad6c`](https://github.com/DrA1ex/klipper-ad5m/commit/54cad6c9defa8df1bd9f0dcc226142fc357ccc68) |
| Buttons | `buttons.py` keeps button events in order when a callback blocks. `gcode_button.py` does not queue empty rendered templates. | [92fe8f1](https://github.com/Klipper3d/klipper/commit/92fe8f1), [b8c936f](https://github.com/Klipper3d/klipper/commit/b8c936f) | [`c4a366d`](https://github.com/DrA1ex/klipper-ad5m/commit/c4a366d265529351e6e19df133fc20a1f1997661), [`5807bf8`](https://github.com/DrA1ex/klipper-ad5m/commit/5807bf8a15bfabd467c6ccfb6def7fac01cc5ab7) |
| Heaters | `heaters.py` scales the minimum PWM change with `max_power`, so heaters configured below 0.05 power start. An internal, runtime-only cold-extrusion override (no G-code command) is used by the guided extruder calibration. | [01f089e](https://github.com/Klipper3d/klipper/commit/01f089e) | [`cd7d5a4`](https://github.com/DrA1ex/klipper-ad5m/commit/cd7d5a42ad0dd5f575463de0206e897ad9b45820), [`a77f0e8`](https://github.com/DrA1ex/klipper-ad5m/commit/a77f0e8b0bbd25d0684c2fee3e1a4e2324e31d62) |
| Virtual SD | `virtual_sdcard.py` counts file positions in UTF-8 bytes (non-ASCII object names, recovery positions), hides dot-files and dot-directories from the file list, has `load_file()` for Power Loss Recovery, and reads the estimated print time from slicer footers. | [600e89a](https://github.com/Klipper3d/klipper/commit/600e89a) | [`e339689`](https://github.com/DrA1ex/klipper-ad5m/commit/e339689b2c02ffca0675298e1a9c5db619e41028), [`1498658`](https://github.com/DrA1ex/klipper-ad5m/commit/1498658d709f34c6a4198377af1f03cd0d5b8d67), [`96046e8`](https://github.com/DrA1ex/klipper-ad5m/commit/96046e8894bf6d6a9df4405f351963e0dd990d1c), [`a7c87f0`](https://github.com/DrA1ex/klipper-ad5m/commit/a7c87f03d812185b1af1ebcad0a070141202385d) |
| Input shaper | `square_corner_velocity` from the printer configuration is used for the `max_accel` recommendation. A large result no longer hangs `SHAPER_CALIBRATE` on the multiprocessing pipe. The calculated shapers are saved as JSON so the plot is drawn without calculating them again. | [72b301a](https://github.com/Klipper3d/klipper/commit/72b301a) (partly); the pipe fix predates [baf188bd](https://github.com/Klipper3d/klipper/commit/baf188bd) | [`facc955`](https://github.com/DrA1ex/klipper-ad5m/commit/facc955388c194eab7fed39f1e052668c9bb9637), [`8020a09`](https://github.com/DrA1ex/klipper-ad5m/commit/8020a0945ca213388ab7a0cb58ea4457bacdb2a4), [`7efc331`](https://github.com/DrA1ex/klipper-ad5m/commit/7efc331ad69fea19ac93f5b010d41e040a7122c3), [`2413426`](https://github.com/DrA1ex/klipper-ad5m/commit/24134266700141cafd688900f110f56f7b54fa7b) |
| Adaptive Pressure Advance | `kinematics/extruder.py`, `chelper/__init__.py`, and `kin_extruder.c` allow pressure-advance values to change at an exact print time. This needs the matching prebuilt `c_helper.so`; it is not MCU firmware. | [c84d78f3](https://github.com/Klipper3d/klipper/commit/c84d78f3) | [`4f41d5d`](https://github.com/DrA1ex/klipper-ad5m/commit/4f41d5df9c9fc6b4105b61633224d8f4dae95ff3) |
| G-code parser | Extended command names are validated, the `M112` pattern is a raw string, and the registered commands with their help are exposed in the `gcode` status (used by Moonraker and Mainsail). Errors are reported once. Immediate commands (`M108`, `TONE`, `ALARM`, `BEEP`, and commands registered by plugins) run ahead of the G-code mutex. Commands from the G-code pipe are processed without "ok" replies. | [5493c60](https://github.com/Klipper3d/klipper/commit/5493c60), [0087f04](https://github.com/Klipper3d/klipper/commit/0087f04), [6676c1d](https://github.com/Klipper3d/klipper/commit/6676c1d) | [`684af26`](https://github.com/DrA1ex/klipper-ad5m/commit/684af26135538c98452cb99373684443f11fa6df), [`9760550`](https://github.com/DrA1ex/klipper-ad5m/commit/97605509087d451eb2d114cc23290339ea1b9a79), [`88816aa`](https://github.com/DrA1ex/klipper-ad5m/commit/88816aaacaaf79b1b447cf70b0455d73b65549bf), [`3bc98e8`](https://github.com/DrA1ex/klipper-ad5m/commit/3bc98e8ac61fce97ebc069d17a96a481145a7207), [`be2ac69`](https://github.com/DrA1ex/klipper-ad5m/commit/be2ac69771bc28bbe63a5dd6898d6ce55ed01c6e), [`20efc39`](https://github.com/DrA1ex/klipper-ad5m/commit/20efc39196c4e60c11ccd554f02663d83936e4b6), [`48513ef`](https://github.com/DrA1ex/klipper-ad5m/commit/48513efba59b53d4a061e17862602d09c4944b63) |
| G-code coordinates | `gcode_move.py` adds `RESET_GCODE_ORIGIN`, which clears temporary `G92` shifts without moving, exposes `base_position`, and logs the coordinates after homing and in `RESTORE_GCODE_STATE`. | Forge-X | [`4674b2c`](https://github.com/DrA1ex/klipper-ad5m/commit/4674b2cfa514ca3b5346b18f2b7647ca5820955a), [`b9308e2`](https://github.com/DrA1ex/klipper-ad5m/commit/b9308e2a3b44c36441e5a8507764b738e63a93bd), [`54fe6e3`](https://github.com/DrA1ex/klipper-ad5m/commit/54fe6e3c0db01608320dee66fe45e14fe6aa1b3a) |
| Sensor threshold actions | `temperature_sensor.py` runs G-code when a sensor reaches a configured value. Forge-X uses it for the load-cell bed collision protection; emergency shutdowns include the measured `weightValue` load. | Forge-X | [`9db50ca`](https://github.com/DrA1ex/klipper-ad5m/commit/9db50cae7ba675b65d926ce8fbe332537cb1f804) |
| Shell commands | `gcode_shell_command.py` is added (it is not in the stock firmware). Besides synchronous commands it has background, queued, streaming, and daemon modes, timeouts, and clean shutdown. Synchronous return codes are exposed in status for the timelapse start check. | Forge-X | [`8c4aea3`](https://github.com/DrA1ex/klipper-ad5m/commit/8c4aea3614fe772a921171e2217004437ceb20ee) |
| Configuration, LEDs, statistics | `configfile.py`: `log_config` option. `led.py`: `invert` option. `statistics.py`: `disabled` option, while the stats callbacks keep running. | Forge-X | [`1f6df92`](https://github.com/DrA1ex/klipper-ad5m/commit/1f6df922e612376d28b117633d58e284e815c9ac), [`298c195`](https://github.com/DrA1ex/klipper-ad5m/commit/298c1959e24951c205edbd27452d23cab42e3f31), [`0ff264c`](https://github.com/DrA1ex/klipper-ad5m/commit/0ff264c7f5249b44e786efb364ee5d5a6ebd3fd2) |
| Library path | `chelper/__init__.py` finds `c_helper.so` next to the stock module, without following the symlink to the Forge-X directory. | Forge-X | [`802d965`](https://github.com/DrA1ex/klipper-ad5m/commit/802d96588e9051bb6661db61259db2b9f3c25cce) |
| Garbage collection and configuration copies | Startup objects are frozen after Klipper becomes ready and unfrozen on disconnect. Static configuration snapshots are immutable and reused during macro status copying; changing warnings and pending-save items remain isolated. | [d57fe439](https://github.com/Klipper3d/klipper/commit/d57fe4395e77b0b6f5eabccf890498049fabbcfe) (freeze); Forge-X (configuration snapshots) | [`1c21f43`](https://github.com/DrA1ex/klipper-ad5m/commit/1c21f43b9938345b70efcbb66f6ec4b27eee3f23) |
| Stock screen reloads | `webhooks.py` preserves the Stock API socket and output subscription across in-process `RESTART`, `FIRMWARE_RESTART`, and `SAVE_CONFIG`. A hard process restart still closes them. | Forge-X | Not yet in the history repository ([source](../.py/klipper/patches/webhooks.py)) |

## Source history of the patches

The Klipper backports in `.py/klipper/patches/`, together with the startup garbage-collection module in `.py/klipper/plugins/`, are published as a Git history in [DrA1ex/klipper-ad5m](https://github.com/DrA1ex/klipper-ad5m), a copy of Klipper with these commits on top of each other:

1. upstream Klipper `v0.11.0` (`e02b7256`), the base of the FlashForge Klipper;
2. one commit with the changes the stock FlashForge firmware makes to it;
3. the Forge-X commits, one topic per commit.

You can read the exact diff of every published change there. Upstream commits that apply cleanly are cherry-picked with the original author. Commits that had to be adapted to the 0.11 code say so in their message and name the upstream commit they are based on.

- [What Forge-X changes compared with the stock firmware](https://github.com/DrA1ex/klipper-ad5m/compare/c6e78dffa3d16a25710942a379b019debbacd5af...main)
- [What the stock firmware changes compared with upstream `v0.11.0`](https://github.com/DrA1ex/klipper-ad5m/compare/e02b725602067a2cd098a62be9a4bb10fc74a9bd...c6e78dffa3d16a25710942a379b019debbacd5af)
- [Description of the repository](https://github.com/DrA1ex/klipper-ad5m/blob/main/AD5M.md)

The startup garbage-collection and immutable-configuration changes are combined in [`1c21f43`](https://github.com/DrA1ex/klipper-ad5m/commit/1c21f43b9938345b70efcbb66f6ec4b27eee3f23). Their original commits remain available on `experimental-gc`: [freeze startup objects](https://github.com/DrA1ex/klipper-ad5m/commit/fdf28b66445e378ed0eeada64a7cfdeaaed1a09c) and [reuse immutable configuration snapshots](https://github.com/DrA1ex/klipper-ad5m/commit/fb218ab088b35d0b331bb0fa1a8f07bf55e97122).

> [!NOTE]
> The patches were written as whole files, not as a series of commits on top of Klipper. The history was reconstructed by topic afterwards, so the order of the commits is chosen for reading and is not the order of the original work. The stock commit has the same MD5 sums as the stock firmware for the four files it covers. The stock `virtual_sdcard.py` from a printer is identical to upstream `v0.11.0`, so it is not part of the stock commit; the repository description explains the difference to the stock file list.

**Forge-X 1.4.2 is slightly ahead of the published history.** A few files have newer changes that are not yet in the history repository: `configfile.py`, `extras/gcode_shell_command.py`, `extras/probe.py`, `extras/temperature_sensor.py`, `reactor.py`, `toolhead.py`, and the added `webhooks.py`. These changes are described in the table above, and the files are in [`.py/klipper/patches/`](../.py/klipper/patches/). The manifest lists the SHA-256 of the files that Forge-X actually installs; its `commit` field points to the published history snapshot used for comparison.

To check it yourself, run `python3 tests/verify_klipper_fork.py --fork https://github.com/DrA1ex/klipper-ad5m`. It clones the commit recorded in [`docs/klipper-ad5m-manifest.json`](klipper-ad5m-manifest.json) and compares every Python file with `.py/klipper/patches/`. Without `--fork` it only checks the current local inventory and SHA-256 sums, which is also done by the host tests (`tests/test_klipper_fork_manifest.py`). With `--fork`, the local differences listed above are reported as mismatches; the check does not ignore them. The prebuilt `c_helper.so` is listed in the manifest with its SHA-256. The C sources in the history repository are the sources it is built from.

## Replaced files

Forge-X overlays Klipper files on a supported AD5M installation. The table above describes the changes, and the replacement-file inventory with SHA-256 sums is in [`docs/klipper-ad5m-manifest.json`](klipper-ad5m-manifest.json). The replacement modules overwrite stock paths, except `klippy/extras/gcode_shell_command.py`, which is added through the same mechanism. The new `garbage_collection.py` module lives under `.py/klipper/plugins/` and is loaded by Klipper during startup.

The Python pressure-advance backport and `c_helper.so` are one matched change. Forge-X plugins (Feather, power-loss recovery, load-cell tare, `mod_params`, checksum support) are separate additions under `.py/klipper/plugins/`; they do not replace Klipper modules.

## Applying and removing the patches

Patching is reversible. During initialization every stock file that Forge-X replaces is first saved as a neighboring `.bak` file, and the Forge-X version is linked into its place. The stock MCU firmware is not reflashed.

Uninstall does the reverse: it removes the plugins, restores every patched file from its `.bak`, restores the configuration through the backup and restore path, and only then removes the runtime. If Forge-X cannot start, the early boot guard can start the stock firmware, and the recovery menu and the USB recovery images can uninstall it, so removal does not depend on a working Forge-X or Klipper.

See [Uninstall](UNINSTALL.md), the [Firmware Recovery Guide](RECOVERY.md), [Dual boot and recovery menu](DUAL_BOOT.md), and, for the overlay itself, [Built-in Klipper patching](../openwiki/workflows/klipper-patching.md).
