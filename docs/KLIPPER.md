# Klipper fixes and AD5M-specific hardening

Forge-X keeps the Klipper that FlashForge ships with the AD5M (0.11 generation) and adds fixes from newer upstream Klipper. The MCU firmware is not changed, and uninstalling restores the original files.

**What you get:**

- A fix for `Timer too close` shutdowns caused by very dense G-code (Forge-X 1.4.2 and newer).
- Fixes for homing and probing with the printer's two MCUs.
- Optional tuning for the E0011 (communication timeout) and E0017 (move queue overflow) errors.
- Correct G-code file positions for files with non-ASCII characters, such as non-English object names.
- Adaptive Pressure Advance (dynamic pressure advance backported from Klipper 0.13), so the adaptive PA profiles from recent OrcaSlicer versions can be used.
- A faster input shaper calculation and extra diagnostic logs for homing and probing.

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

Each row is one topic. "Based on" is the upstream Klipper commit, where there is one. Some commits are cherry-picked, and others are adapted to the 0.11 code; the commit message in the history says which. "History" links to the commits in the [history repository](#source-history-of-the-patches).

| Area | What changes | Based on | History |
| --- | --- | --- | --- |
| `Timer too close` (dense G-code) | `toolhead.py` yields to the reactor while buffering moves, so dense G-code cannot starve MCU communication and timers. This is the core fix. | [50cb362](https://github.com/Klipper3d/klipper/commit/50cb362234f277a4923f1d59d21473d1e0317f62) (adapted) | [`ed2d6bb`](https://github.com/DrA1ex/klipper-ad5m/commit/ed2d6bb6b5ab2173bafbed96eda183a3e6d0b5b4) |
| Reactor events | `reactor.py` does not deliver stale events after a file descriptor was removed or reused. Each ready batch is bound to the current registrations. | [bb88985](https://github.com/Klipper3d/klipper/commit/bb88985) and its prerequisites 136283bd, 0d5b96a6 | [`7a25e9d`](https://github.com/DrA1ex/klipper-ad5m/commit/7a25e9d596f383fd8f7ff1e48643fcca941ff5c7), [`173fa1e`](https://github.com/DrA1ex/klipper-ad5m/commit/173fa1eb1c3032c7d711444a9a93eb1a463d468a), [`0cf76eb`](https://github.com/DrA1ex/klipper-ad5m/commit/0cf76eb02001fbb881522622b0b3c80f4ecd6025), [`b814e88`](https://github.com/DrA1ex/klipper-ad5m/commit/b814e88d60028b02b15edd8b89c80d17b0f2e3c4), [`2fe2ca9`](https://github.com/DrA1ex/klipper-ad5m/commit/2fe2ca95e3490da6997e4bfb823e6a8af7a081fd) |
| E0017 (move queue) | `toolhead.py`: with `tune_klipper` the lookahead flush time is `0.150 s` instead of the FlashForge `0.5 s`. `buffer_time_high` defaults to 1.5 s instead of 2.0 s, a value chosen by testing on the printer. | [16fc46fe](https://github.com/Klipper3d/klipper/commit/16fc46fe) (lookahead value) | [`5ada55e`](https://github.com/DrA1ex/klipper-ad5m/commit/5ada55e9b814b1afe8f6689d70ce56a13b32e771), [`4aefa48`](https://github.com/DrA1ex/klipper-ad5m/commit/4aefa48a08efc059c74d2c8f44ea42905cca8009) |
| Low-buffer log | `toolhead.py` logs `LOW_BUFFER_FLUSH` with the timing values when the print buffer falls below `buffer_time_low`. It shows after the fact that the host fell behind. | Forge-X | [`c239a63`](https://github.com/DrA1ex/klipper-ad5m/commit/c239a63b7d9578dd2af3c5c222d3eadb3ef311e0) |
| E0011 (communication timeout) | `mcu.py`: TRSYNC reports are staggered during multi-MCU homing, the reporting margin is larger, and the timeout is set at the endstop start clock. With `tune_klipper` the TRSYNC timeout is `0.05 s` instead of `0.025 s`, which gives the two MCUs more communication margin. | [dab39c02](https://github.com/Klipper3d/klipper/commit/dab39c02), [1ea9f3aa](https://github.com/Klipper3d/klipper/commit/1ea9f3aa), [8e6e467](https://github.com/Klipper3d/klipper/commit/8e6e467) | [`1a12435`](https://github.com/DrA1ex/klipper-ad5m/commit/1a12435e4afd0716ff746b09f0ee325766d5f736), [`92bbdff`](https://github.com/DrA1ex/klipper-ad5m/commit/92bbdff6cc55dcc28fbd677db7e99a7a89d8ab82), [`451cdb3`](https://github.com/DrA1ex/klipper-ad5m/commit/451cdb339fa74f2615a31a7ffff730118c3a85bf), [`0c34084`](https://github.com/DrA1ex/klipper-ad5m/commit/0c34084e492ecf0a3ddf2645a12da733daa5e0a5) |
| Homing diagnostics | `homing.py` and `mcu.py` log homing moves, start/trigger/halt step positions, endstop trigger times, and TRSYNC stop reasons, without logging periodic reports. The homing calculations are unchanged. | Forge-X | [`3601c70`](https://github.com/DrA1ex/klipper-ad5m/commit/3601c700e03fdc4e3a369fdf8cb39bc66903b7f1), [`42f5e5d`](https://github.com/DrA1ex/klipper-ad5m/commit/42f5e5d0c9a90ac6b311ac6db51cf437316540cf) |
| Probe accuracy | `PROBE_ACCURACY` returns to the starting XY position between attempts instead of retracting only in Z. | [a353efa](https://github.com/Klipper3d/klipper/commit/a353efa) | [`70cdc93`](https://github.com/DrA1ex/klipper-ad5m/commit/70cdc937c41b3645befdb08710664713e42b29f6) |
| TMC drivers | The `stepper:set_dir_inverted` event name is fixed, so phase tracking is refreshed after direction inversion. Enable and disable are serialized with a reactor mutex. The driver error checks that the stock firmware disables are active again. | [8dd798e](https://github.com/Klipper3d/klipper/commit/8dd798e), [8ea7be5](https://github.com/Klipper3d/klipper/commit/8ea7be5) | [`aafafaa`](https://github.com/DrA1ex/klipper-ad5m/commit/aafafaa24681d39c1d78d6ce3461dd0d7321b64c), [`4b157f0`](https://github.com/DrA1ex/klipper-ad5m/commit/4b157f0fd090a64b31ffd190c0285ae3cb3f3646), [`3096df9`](https://github.com/DrA1ex/klipper-ad5m/commit/3096df9cd57f1c6da1ceb11c27ad6f12cc5bf59d) |
| Servo PWM | `servo.py` and `mcu.py` align servo updates to software-PWM cycle boundaries, adapted to the older direct-PWM API. | [2b4c55f](https://github.com/Klipper3d/klipper/commit/2b4c55f) | [`873e6fc`](https://github.com/DrA1ex/klipper-ad5m/commit/873e6fcd5af68eadccf3872a8284acdd3a5f615b) |
| Buttons | `buttons.py` keeps button events in order when a callback blocks. `gcode_button.py` does not queue empty rendered templates. | [92fe8f1](https://github.com/Klipper3d/klipper/commit/92fe8f1), [b8c936f](https://github.com/Klipper3d/klipper/commit/b8c936f) | [`c3a9072`](https://github.com/DrA1ex/klipper-ad5m/commit/c3a90724ea57fe27d8c4a043dcfef1c00ab5e8e7), [`f951415`](https://github.com/DrA1ex/klipper-ad5m/commit/f95141584217d02b34c50db97f8161ede84fd4de) |
| Heaters | `heaters.py` scales the minimum PWM change with `max_power`, so heaters configured below 0.05 power start. An internal, runtime-only cold-extrusion override (no G-code command) is used by the guided extruder calibration. | [01f089e](https://github.com/Klipper3d/klipper/commit/01f089e) | [`6d97dc6`](https://github.com/DrA1ex/klipper-ad5m/commit/6d97dc6fe56b3ec818bbb7a523782749ffcbf1ea), [`685ba54`](https://github.com/DrA1ex/klipper-ad5m/commit/685ba545e7bb01283abeeadda8ae83f8f1774d87) |
| Virtual SD | `virtual_sdcard.py` counts file positions in UTF-8 bytes (non-ASCII object names, recovery positions), hides dot-files and dot-directories from the file list, has `load_file()` for Power Loss Recovery, and reads the estimated print time from slicer footers. | [600e89a](https://github.com/Klipper3d/klipper/commit/600e89a) | [`446e35a`](https://github.com/DrA1ex/klipper-ad5m/commit/446e35aa63a5ddd256bda63af1941a5b5480273a), [`9fad044`](https://github.com/DrA1ex/klipper-ad5m/commit/9fad044c29d8fd0f596365fe4605b2bede1e152c), [`1d84fa5`](https://github.com/DrA1ex/klipper-ad5m/commit/1d84fa599944a43d9776393ccb76acddcc22d7e5), [`80ca713`](https://github.com/DrA1ex/klipper-ad5m/commit/80ca713302ed399bfd000a89b2d0cc8de6e294c6), [`2f558b9`](https://github.com/DrA1ex/klipper-ad5m/commit/2f558b992b91cf4d2240f39a6e434cd91a12d5be) |
| Input shaper | `square_corner_velocity` from the printer configuration is used for the `max_accel` recommendation. A large result no longer hangs `SHAPER_CALIBRATE` on the multiprocessing pipe. The calculated shapers are saved as JSON so the plot is drawn without calculating them again. | [72b301a](https://github.com/Klipper3d/klipper/commit/72b301a) (partly); the pipe fix predates [baf188bd](https://github.com/Klipper3d/klipper/commit/baf188bd) | [`db5f9b5`](https://github.com/DrA1ex/klipper-ad5m/commit/db5f9b58040d127b257c641ed24fa351a7a58726), [`4d15e5c`](https://github.com/DrA1ex/klipper-ad5m/commit/4d15e5c33113bb7d99386ef920dc0c0ac9cd57b2), [`f389672`](https://github.com/DrA1ex/klipper-ad5m/commit/f389672cefa00fe8d7b6616042d455c01a2559c7), [`626a034`](https://github.com/DrA1ex/klipper-ad5m/commit/626a034c5556d8830bb718ac35352102fb113bf7) |
| Adaptive Pressure Advance | `kinematics/extruder.py`, `chelper/__init__.py`, and `kin_extruder.c` allow pressure-advance values to change at an exact print time. This needs the matching prebuilt `c_helper.so`; it is not MCU firmware. | [c84d78f3](https://github.com/Klipper3d/klipper/commit/c84d78f3) | [`eeb125a`](https://github.com/DrA1ex/klipper-ad5m/commit/eeb125a9c77c00f3055376f231e976541e74b100) |
| G-code parser | Extended command names are validated, the `M112` pattern is a raw string, and the registered commands with their help are exposed in the `gcode` status (used by Moonraker and Mainsail). Errors are reported once. Immediate commands (`M108`, `TONE`, `ALARM`, `BEEP`, and commands registered by plugins) run ahead of the G-code mutex. Commands from the G-code pipe are processed without "ok" replies. | [5493c60](https://github.com/Klipper3d/klipper/commit/5493c60), [0087f04](https://github.com/Klipper3d/klipper/commit/0087f04), [6676c1d](https://github.com/Klipper3d/klipper/commit/6676c1d) | [`9ec940b`](https://github.com/DrA1ex/klipper-ad5m/commit/9ec940bd8e7275aeafeb5e75223a21834731fcc3), [`c3756cf`](https://github.com/DrA1ex/klipper-ad5m/commit/c3756cf944ea3824040fcb945172ca6a2fccb06b), [`fc17c58`](https://github.com/DrA1ex/klipper-ad5m/commit/fc17c58c4f63ba04002176a0abb560793d02b96a), [`2fd14f4`](https://github.com/DrA1ex/klipper-ad5m/commit/2fd14f4f4efee1b4153201c6181de8c86b7190f1), [`f68efc6`](https://github.com/DrA1ex/klipper-ad5m/commit/f68efc62df647e1f9e33c0d0ca64d3bfd41b9ea3), [`e27ef0b`](https://github.com/DrA1ex/klipper-ad5m/commit/e27ef0b625105fdb6f184127ef31e02cf2c02f00), [`5512d34`](https://github.com/DrA1ex/klipper-ad5m/commit/5512d3450eea3daca3204d7dbf21d78d1bfb6bf3) |
| G-code coordinates | `gcode_move.py` adds `RESET_GCODE_ORIGIN`, which clears temporary `G92` shifts without moving, exposes `base_position`, and logs the coordinates after homing and in `RESTORE_GCODE_STATE`. | Forge-X | [`b01ec56`](https://github.com/DrA1ex/klipper-ad5m/commit/b01ec56006d88182039c4a77b101147700c9c618), [`2025b89`](https://github.com/DrA1ex/klipper-ad5m/commit/2025b89f7fa9dcea9e289f4dd1981ce83c8aa504), [`407fad2`](https://github.com/DrA1ex/klipper-ad5m/commit/407fad287387953ccc3006183cbff9d99f94b4c4) |
| Sensor threshold actions | `temperature_sensor.py` runs G-code when a sensor reaches a configured value. Forge-X uses it for the load-cell bed collision protection. | Forge-X | [`6993c4d`](https://github.com/DrA1ex/klipper-ad5m/commit/6993c4d512564baeee1cc14af40cf9f750ab2913) |
| Shell commands | `gcode_shell_command.py` is added (it is not in the stock firmware). Besides synchronous commands it has background, queued, streaming, and daemon modes, timeouts, and clean shutdown. | Forge-X | [`05bed2a`](https://github.com/DrA1ex/klipper-ad5m/commit/05bed2aea443efbabd268c06d767e9fe10862669) |
| Configuration, LEDs, statistics | `configfile.py`: `log_config` option. `led.py`: `invert` option. `statistics.py`: `disabled` option, while the stats callbacks keep running. | Forge-X | [`5c69309`](https://github.com/DrA1ex/klipper-ad5m/commit/5c69309879ebc98aff5fca44a871f00501a1aaa7), [`ddfeb0b`](https://github.com/DrA1ex/klipper-ad5m/commit/ddfeb0ba25804386ba6873665d61e84274889ad3), [`5fef454`](https://github.com/DrA1ex/klipper-ad5m/commit/5fef454dd0128cd9683685712629ecb06f9099a4) |
| Library path | `chelper/__init__.py` finds `c_helper.so` next to the stock module, without following the symlink to the Forge-X directory. | Forge-X | [`0c9d23e`](https://github.com/DrA1ex/klipper-ad5m/commit/0c9d23ee0b14933cabc96e19b8c3bcf6a63beda0) |

## Source history of the patches

The files in `.py/klipper/patches/` are also published as a Git history in [DrA1ex/klipper-ad5m](https://github.com/DrA1ex/klipper-ad5m), a copy of Klipper with these commits on top of each other:

1. upstream Klipper `v0.11.0` (`e02b7256`), the base of the FlashForge Klipper;
2. one commit with the changes the stock FlashForge firmware makes to it;
3. the Forge-X commits, one topic per commit.

You can read the exact diff of every change there. Upstream commits that apply cleanly are cherry-picked with the original author. Commits that had to be adapted to the 0.11 code say so in their message and name the upstream commit they are based on.

- [What Forge-X changes compared with the stock firmware](https://github.com/DrA1ex/klipper-ad5m/compare/c6e78dffa3d16a25710942a379b019debbacd5af...main)
- [What the stock firmware changes compared with upstream `v0.11.0`](https://github.com/DrA1ex/klipper-ad5m/compare/e02b725602067a2cd098a62be9a4bb10fc74a9bd...c6e78dffa3d16a25710942a379b019debbacd5af)
- [Description of the repository](https://github.com/DrA1ex/klipper-ad5m/blob/main/AD5M.md)

> [!NOTE]
> The patches were written as whole files, not as a series of commits on top of Klipper. The history was reconstructed by topic afterwards, so the order of the commits is chosen for reading and is not the order of the original work. What is exact: the final files are byte-identical to the files in this repository, and the stock commit has the same MD5 sums as the stock firmware for the four files it covers. The stock `virtual_sdcard.py` from a printer is identical to upstream `v0.11.0`, so it is not part of the stock commit; the repository description explains the difference to the stock file list.

To check it yourself, run `python3 tests/verify_klipper_fork.py --fork https://github.com/DrA1ex/klipper-ad5m`. It clones the commit recorded in [`docs/klipper-ad5m-manifest.json`](klipper-ad5m-manifest.json) and compares every Python file with `.py/klipper/patches/`. Without `--fork` it only checks the SHA-256 sums in the manifest, which is also done by the host tests (`tests/test_klipper_fork_manifest.py`). The prebuilt `c_helper.so` is listed in the manifest with its SHA-256. The C sources in the history repository are the sources it is built from.

## Replaced files

Forge-X overlays Klipper files on a supported AD5M installation. They are all in the table above, and the full list with SHA-256 sums is in [`docs/klipper-ad5m-manifest.json`](klipper-ad5m-manifest.json). All of them replace a stock file, except `klippy/extras/gcode_shell_command.py`, which is not part of the stock firmware and is added through the same mechanism.

The Python pressure-advance backport and `c_helper.so` are one matched change. Forge-X plugins (Feather, power-loss recovery, load-cell tare, `mod_params`, checksum support) are separate additions under `.py/klipper/plugins/`; they do not replace Klipper modules.

## Applying and removing the patches

Patching is reversible. During initialization every stock file that Forge-X replaces is first saved as a neighboring `.bak` file, and the Forge-X version is linked into its place. The stock MCU firmware is not reflashed.

Uninstall does the reverse: it removes the plugins, restores every patched file from its `.bak`, restores the configuration through the backup and restore path, and only then removes the runtime. If Forge-X cannot start, the early boot guard can start the stock firmware, and the recovery menu and the USB recovery images can uninstall it, so removal does not depend on a working Forge-X or Klipper.

See [Uninstall](UNINSTALL.md), the [Firmware Recovery Guide](RECOVERY.md), [Dual boot and recovery menu](DUAL_BOOT.md), and, for the overlay itself, [Built-in Klipper patching](../openwiki/workflows/klipper-patching.md).
