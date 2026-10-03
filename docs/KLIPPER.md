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

**Why not a full Klipper 0.13?** Forge-X deliberately keeps the vendor-compatible Klipper and MCU pair and brings over the newer fixes and features that matter for this printer. See [Why doesn't Forge-X use Klipper 0.13?](FAQ.md#why-doesnt-forge-x-use-klipper-013)

**Details:**
[Source history](#source-history-of-the-patches) ·
[Why Klipper is not replaced with 0.13](#design-approach) ·
[`Timer too close`](#the-timer-too-close-problem) ·
[Backported upstream fixes](#upstream-klipper-fixes-backported-by-forge-x) ·
[AD5M-specific changes](#ad5m-specific-klipper-fixes-and-adaptations) ·
[List of replaced files](#complete-replacement-inventory) ·
[How patches are applied and recovered](#how-patches-are-applied-and-recovered)

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

Where to find each change:

| Change | Commits in the history repository |
| --- | --- |
| Stock FlashForge changes (reconstructed) | [`c6e78df`](https://github.com/DrA1ex/klipper-ad5m/commit/c6e78dffa3d16a25710942a379b019debbacd5af) |
| `toolhead.py`: lookahead flush time and `tune_klipper` | [`5ada55e`](https://github.com/DrA1ex/klipper-ad5m/commit/5ada55e9b814b1afe8f6689d70ce56a13b32e771) |
| `toolhead.py`: `buffer_time_high` 1.5 s | [`4aefa48`](https://github.com/DrA1ex/klipper-ad5m/commit/4aefa48a08efc059c74d2c8f44ea42905cca8009) |
| `toolhead.py`: yield while buffering (50cb362) | [`ed2d6bb`](https://github.com/DrA1ex/klipper-ad5m/commit/ed2d6bb6b5ab2173bafbed96eda183a3e6d0b5b4) |
| `toolhead.py`: low-buffer flush logging | [`c239a63`](https://github.com/DrA1ex/klipper-ad5m/commit/c239a63b7d9578dd2af3c5c222d3eadb3ef311e0) |
| `reactor.py`: fd event dispatch (bb88985 and prerequisites) | [`7a25e9d`](https://github.com/DrA1ex/klipper-ad5m/commit/7a25e9d596f383fd8f7ff1e48643fcca941ff5c7), [`173fa1e`](https://github.com/DrA1ex/klipper-ad5m/commit/173fa1eb1c3032c7d711444a9a93eb1a463d468a), [`0cf76eb`](https://github.com/DrA1ex/klipper-ad5m/commit/0cf76eb02001fbb881522622b0b3c80f4ecd6025) |
| `reactor.py`: `fileno()` kept, per-batch snapshot | [`b814e88`](https://github.com/DrA1ex/klipper-ad5m/commit/b814e88d60028b02b15edd8b89c80d17b0f2e3c4), [`2fe2ca9`](https://github.com/DrA1ex/klipper-ad5m/commit/2fe2ca95e3490da6997e4bfb823e6a8af7a081fd) |
| `mcu.py`: TRSYNC reporting and scheduling (dab39c02, 1ea9f3aa, 8e6e467) | [`1a12435`](https://github.com/DrA1ex/klipper-ad5m/commit/1a12435e4afd0716ff746b09f0ee325766d5f736), [`92bbdff`](https://github.com/DrA1ex/klipper-ad5m/commit/92bbdff6cc55dcc28fbd677db7e99a7a89d8ab82), [`451cdb3`](https://github.com/DrA1ex/klipper-ad5m/commit/451cdb339fa74f2615a31a7ffff730118c3a85bf) |
| `mcu.py`: `tune_klipper` TRSYNC timeout, homing logs | [`0c34084`](https://github.com/DrA1ex/klipper-ad5m/commit/0c34084e492ecf0a3ddf2645a12da733daa5e0a5), [`3601c70`](https://github.com/DrA1ex/klipper-ad5m/commit/3601c700e03fdc4e3a369fdf8cb39bc66903b7f1) |
| `servo.py` / `mcu.py`: PWM cycle alignment (2b4c55f) | [`873e6fc`](https://github.com/DrA1ex/klipper-ad5m/commit/873e6fcd5af68eadccf3872a8284acdd3a5f615b) |
| `tmc.py`: direction-inversion event (8dd798e), mutex (8ea7be5), driver error checks | [`aafafaa`](https://github.com/DrA1ex/klipper-ad5m/commit/aafafaa24681d39c1d78d6ce3461dd0d7321b64c), [`4b157f0`](https://github.com/DrA1ex/klipper-ad5m/commit/4b157f0fd090a64b31ffd190c0285ae3cb3f3646), [`3096df9`](https://github.com/DrA1ex/klipper-ad5m/commit/3096df9cd57f1c6da1ceb11c27ad6f12cc5bf59d) |
| `buttons.py` (92fe8f1), `gcode_button.py` (b8c936f) | [`c3a9072`](https://github.com/DrA1ex/klipper-ad5m/commit/c3a90724ea57fe27d8c4a043dcfef1c00ab5e8e7), [`f951415`](https://github.com/DrA1ex/klipper-ad5m/commit/f95141584217d02b34c50db97f8161ede84fd4de) |
| `probe.py`: `PROBE_ACCURACY` (a353efa) | [`70cdc93`](https://github.com/DrA1ex/klipper-ad5m/commit/70cdc937c41b3645befdb08710664713e42b29f6) |
| `heaters.py`: low `max_power` (01f089e), cold-extrusion override | [`6d97dc6`](https://github.com/DrA1ex/klipper-ad5m/commit/6d97dc6fe56b3ec818bbb7a523782749ffcbf1ea), [`685ba54`](https://github.com/DrA1ex/klipper-ad5m/commit/685ba545e7bb01283abeeadda8ae83f8f1774d87) |
| `virtual_sdcard.py`: file offsets (600e89a), hidden files, `load_file()`, print-time metadata, Python 2 | [`446e35a`](https://github.com/DrA1ex/klipper-ad5m/commit/446e35aa63a5ddd256bda63af1941a5b5480273a), [`9fad044`](https://github.com/DrA1ex/klipper-ad5m/commit/9fad044c29d8fd0f596365fe4605b2bede1e152c), [`1d84fa5`](https://github.com/DrA1ex/klipper-ad5m/commit/1d84fa599944a43d9776393ccb76acddcc22d7e5), [`80ca713`](https://github.com/DrA1ex/klipper-ad5m/commit/80ca713302ed399bfd000a89b2d0cc8de6e294c6), [`2f558b9`](https://github.com/DrA1ex/klipper-ad5m/commit/2f558b992b91cf4d2240f39a6e434cd91a12d5be) |
| `shaper_calibrate.py`, `resonance_tester.py`: `square_corner_velocity`, pipe deadlock, plot data | [`db5f9b5`](https://github.com/DrA1ex/klipper-ad5m/commit/db5f9b58040d127b257c641ed24fa351a7a58726), [`4d15e5c`](https://github.com/DrA1ex/klipper-ad5m/commit/4d15e5c33113bb7d99386ef920dc0c0ac9cd57b2), [`f389672`](https://github.com/DrA1ex/klipper-ad5m/commit/f389672cefa00fe8d7b6616042d455c01a2559c7), [`626a034`](https://github.com/DrA1ex/klipper-ad5m/commit/626a034c5556d8830bb718ac35352102fb113bf7) |
| `kinematics/extruder.py`, `chelper/`: dynamic pressure advance (c84d78f3) | [`eeb125a`](https://github.com/DrA1ex/klipper-ad5m/commit/eeb125a9c77c00f3055376f231e976541e74b100) |
| `gcode.py`: command names (5493c60), regex (0087f04), status (6676c1d) | [`9ec940b`](https://github.com/DrA1ex/klipper-ad5m/commit/9ec940bd8e7275aeafeb5e75223a21834731fcc3), [`c3756cf`](https://github.com/DrA1ex/klipper-ad5m/commit/c3756cf944ea3824040fcb945172ca6a2fccb06b), [`fc17c58`](https://github.com/DrA1ex/klipper-ad5m/commit/fc17c58c4f63ba04002176a0abb560793d02b96a), [`2fd14f4`](https://github.com/DrA1ex/klipper-ad5m/commit/2fd14f4f4efee1b4153201c6181de8c86b7190f1) |
| `gcode.py`: error reporting, immediate commands, pipe input | [`f68efc6`](https://github.com/DrA1ex/klipper-ad5m/commit/f68efc62df647e1f9e33c0d0ca64d3bfd41b9ea3), [`e27ef0b`](https://github.com/DrA1ex/klipper-ad5m/commit/e27ef0b625105fdb6f184127ef31e02cf2c02f00), [`5512d34`](https://github.com/DrA1ex/klipper-ad5m/commit/5512d3450eea3daca3204d7dbf21d78d1bfb6bf3) |
| `gcode_move.py`: `base_position`, restore log, `RESET_GCODE_ORIGIN` | [`b01ec56`](https://github.com/DrA1ex/klipper-ad5m/commit/b01ec56006d88182039c4a77b101147700c9c618), [`2025b89`](https://github.com/DrA1ex/klipper-ad5m/commit/2025b89f7fa9dcea9e289f4dd1981ce83c8aa504), [`407fad2`](https://github.com/DrA1ex/klipper-ad5m/commit/407fad287387953ccc3006183cbff9d99f94b4c4) |
| `homing.py`: logging | [`42f5e5d`](https://github.com/DrA1ex/klipper-ad5m/commit/42f5e5d0c9a90ac6b311ac6db51cf437316540cf) |
| `temperature_sensor.py`: threshold G-code | [`6993c4d`](https://github.com/DrA1ex/klipper-ad5m/commit/6993c4d512564baeee1cc14af40cf9f750ab2913) |
| `gcode_shell_command.py` | [`05bed2a`](https://github.com/DrA1ex/klipper-ad5m/commit/05bed2aea443efbabd268c06d767e9fe10862669) |
| `configfile.py`, `led.py`, `statistics.py` | [`5c69309`](https://github.com/DrA1ex/klipper-ad5m/commit/5c69309879ebc98aff5fca44a871f00501a1aaa7), [`ddfeb0b`](https://github.com/DrA1ex/klipper-ad5m/commit/ddfeb0ba25804386ba6873665d61e84274889ad3), [`5fef454`](https://github.com/DrA1ex/klipper-ad5m/commit/5fef454dd0128cd9683685712629ecb06f9099a4) |
| `chelper/__init__.py`: library path | [`0c9d23e`](https://github.com/DrA1ex/klipper-ad5m/commit/0c9d23ee0b14933cabc96e19b8c3bcf6a63beda0) |

## Design approach

Forge-X deliberately keeps the FlashForge host Klipper shipped with the Adventurer 5M / 5M Pro and backports selected fixes from newer upstream Klipper versions instead of replacing the complete host and MCU stack with Klipper 0.13.

This is a deliberate engineering choice for the AD5M.

The AD5M is an older printer with older hardware. Klipper 0.13 is a good release, but for most tasks the Klipper that FlashForge ships, together with the fixes and features backported by Forge-X, is enough. At the moment there is no need for a full 0.13. Forge-X therefore keeps the vendor-compatible Klipper/MCU protocol and brings over the fixes that matter for this printer. If a real need for 0.13 appears, Forge-X can move to it.

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

Forge-X also carries the newer reactor event-dispatch fixes described below. Together, these changes address the host scheduling failure mode that caused heavy files to reproduce `Timer too close` on the AD5M.

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
| Button callback ordering | Backports the upstream button-state fix that prevents blocked callbacks from causing lost or out-of-order button events. This was the original reason Forge-X began replacing `buttons.py`. | [92fe8f1](https://github.com/Klipper3d/klipper/commit/92fe8f15b82d7c7ccb7f8ac6552259adeac471fb) / [upstream PR #6440](https://github.com/Klipper3d/klipper/pull/6440) |
| Empty G-code button templates | Does not queue an empty rendered button template on the G-code mutex. | [b8c936f](https://github.com/Klipper3d/klipper/commit/b8c936f7b86013677fe3b4b59fcb6d626531a962) |
| Low `max_power` heaters | Scales the minimum PWM-change threshold by `max_power`, fixing heaters configured below 0.05 power not starting correctly. | [01f089e](https://github.com/Klipper3d/klipper/commit/01f089e71039c1f9e63b8f1def218e6f465b41da) |
| Virtual SD file offsets | Counts UTF-8 byte length rather than Python string character count when advancing virtual-SD file position. This is important for non-ASCII G-code comments/object names and recovery positions under Python 3. | [600e89a](https://github.com/Klipper3d/klipper/commit/600e89ae8c759613a3c6fc2b24d0a62d00e6baf2) |
| Input-shaper calibration parameters | Uses `square_corner_velocity` from the printer configuration when calculating shaper recommendations. | [72b301a](https://github.com/Klipper3d/klipper/commit/72b301a2859c3f7ed26d802dd52fc495eef6c353) |
| Input-shaper background-process deadlock | Drains a large calibration result from the multiprocessing pipe before waiting for the child process to exit, preventing `SHAPER_CALIBRATE` from hanging when the result exceeds the OS pipe buffer. Forge-X carried this fix before it later appeared upstream. | [baf188bd](https://github.com/Klipper3d/klipper/commit/baf188bd62bb9c82775d679cf0db30a72f9e9173) |
| Dynamic pressure advance | Backports the host-side support that allows pressure-advance values to change at an exact print time while queued motion already exists. The backport includes matching Python/CFFI pieces and a compatible `c_helper.so`. | [c84d78f3](https://github.com/Klipper3d/klipper/commit/c84d78f3f169bc5163d11b74837f9880b0b7dba4) |
| G-code command names | Rejects invalid extended command names at registration. | [5493c60](https://github.com/Klipper3d/klipper/commit/5493c60) |
| G-code regular expression | Uses a raw string for the `M112` pattern, which removes a Python warning. | [0087f04](https://github.com/Klipper3d/klipper/commit/0087f04) |

## AD5M-specific Klipper fixes and adaptations

Not every Forge-X replacement is a literal upstream cherry-pick. Some changes adapt newer Klipper behavior to the FlashForge 0.11-era tree, while others solve AD5M-specific integration or diagnostic problems.

| Area | Forge-X behavior |
| --- | --- |
| Lookahead tuning / E0017 | The FlashForge Klipper tree uses a `0.5 s` lookahead flush time. Modern upstream Klipper later reduced this to `0.150 s` in [16fc46fe](https://github.com/Klipper3d/klipper/commit/16fc46fe5ff0dbbc5188ee6a7829eee5976c1eb9), specifically to improve responsiveness and make print stalls less likely. Forge-X can select that modern `0.150 s` behavior through `tune_klipper`. On the resource-constrained AD5M, keeping a much larger amount of motion buffered can increase queue pressure and contribute to the familiar `E0017 / Move queue overflow` failure mode. |
| Buffer time | `toolhead.py` uses `buffer_time_high` = 1.5 s by default instead of the 2.0 s of Klipper 0.11. This value was chosen by testing on the printer. A `buffer_time_high` set in the printer configuration still takes priority. Newer Klipper no longer has this mechanism in the same form, because its toolhead was reworked around a separate motion-queuing module. |
| Low-buffer flush log | `toolhead.py` logs a `LOW_BUFFER_FLUSH` warning with the timing values each time the print buffer falls below `buffer_time_low` and the lookahead queue is flushed. It shows after the fact that the host fell behind. |
| TRSYNC timeout / E0011 | The AD5M uses two MCUs, so homing and probing depend heavily on reliable TRSYNC communication. Forge-X backports the newer upstream multi-MCU improvements that stagger reports and improve report timing ([dab39c02](https://github.com/Klipper3d/klipper/commit/dab39c02cd5681d530388fbaa82d0dc7f31d2e26), [1ea9f3aa](https://github.com/Klipper3d/klipper/commit/1ea9f3aa35d7232ee5d106541c5a98c4348c6e47), [8e6e467](https://github.com/Klipper3d/klipper/commit/8e6e467ebc16f93ab01ed63c55d24af52b020b54)). In addition, `tune_klipper` uses an AD5M-specific `0.05 s` multi-MCU timeout instead of the stock `0.025 s`: a compromise that gives this hardware more communication margin without making timeout detection excessively slow. |
| Virtual-SD file loading | `virtual_sdcard.py` has `load_file()`, which selects a file without starting the print. The Power Loss Recovery plugin uses it to select the interrupted file again before the position is restored. |
| Homing diagnostics | `homing.py` retains the vendor-era homing calculations but records the details needed to investigate real AD5M incidents: homing/probing moves, start/trigger/halt step positions, retract and second-pass setup, and final toolhead coordinates. This is especially useful because the printer has two MCUs and the synchronization path is a recurring source of hard-to-diagnose homing/probing failures. |
| MCU / TRSYNC diagnostics | `mcu.py` logs homing setup, endstop trigger timestamps, and final TRSYNC stop reasons without flooding logs with every periodic report. These diagnostics are specifically intended to make intermittent two-MCU synchronization and homing incidents on the AD5M observable after the fact. |
| G-code coordinate safeguard | `gcode_move.py` adds `RESET_GCODE_ORIGIN`, which clears temporary `G92` coordinate shifts while preserving configured offsets. This provides a safe recovery path for rare cases where a stale coordinate shift survives into a later workflow and would otherwise produce incorrect movement or a bad print. |
| Coordinate diagnostics | `gcode_move.py` exposes and logs base/origin state around homing and `RESTORE_GCODE_STATE`. The AD5M has a comparatively complex motion/synchronization path, and users occasionally encounter coordinate-state incidents that are impossible to diagnose from normal Klipper logs alone. |
| G-code command discovery | `gcode.py` backports the newer `gcode/commands` status interface expected by current Moonraker/Mainsail integrations (upstream commit [6676c1d](https://github.com/Klipper3d/klipper/commit/6676c1d)). |
| G-code error handling | The Forge-X G-code replacement avoids repeated error output in selected exception paths and contains AD5M/Forge-X immediate-command handling changes. |
| Statistics with logging disabled | `statistics.py` continues calling subsystem stats callbacks even when periodic stats logging is disabled. This prevents internal producers from accumulating undrained state while still allowing expensive/noisy periodic log output to be disabled on a low-resource host. |
| Shaper calculation path | The resonance/shaper replacements pass the correct calibration parameters and reuse already calculated shaper data instead of repeating unnecessary calculations. This reduces work and noticeably speeds up shaper calculation/plot generation on the AD5M's slow host CPU. |
| Shell-command execution | `gcode_shell_command.py` is substantially extended with background, queued, streaming, and daemon-style execution, timeout handling, and clean shutdown of long-running processes. This is what allows Forge-X to build more complex workflows without forcing every external operation to block Klippy synchronously. |
| Generic sensor threshold actions | `temperature_sensor.py` can execute controlled G-code when a generic sensor crosses a configured threshold. Forge-X currently uses this for load-cell based bed/nozzle collision protection, but the mechanism is intentionally generic and can also support automatic safety actions such as shutting down equipment on an abnormal sensor or overheating condition. |
| LED inversion | `led.py` supports inverted LED outputs required by the printer hardware/configuration. |
| Controlled cold-extrusion override | `heaters.py` contains an internal runtime-only extrusion-temperature override used by guided extruder calibration. It allows a simple controlled calibration move that stock behavior would reject; Forge-X enables the bypass only for that operation and immediately restores the normal extrusion-temperature safety rule afterward. It is deliberately not exposed as a general-purpose G-code command. |
| Hidden virtual-SD entries | Forge-X keeps its own data under storage that is also exposed through the printer's G-code/virtual-SD path. Hidden Forge-X/system files are therefore filtered from G-code enumeration so the stock screen and print path do not try to treat unrelated mod data as printable files. Besides keeping the file list clean, this avoids unnecessary reads and problematic attempts to process files that were never G-code. |
| Print-time metadata | `virtual_sdcard.py` extracts estimated printing/extrusion time from supported slicer footers for local UI progress reporting. |
| Configuration/logging compatibility | `configfile.py` carries the Forge-X-compatible configuration parser/logging behavior used with the FlashForge config layout. |

## Complete replacement inventory

Forge-X 1.4.2 overlays the following Klipper files on a supported AD5M installation. All of them replace a stock file, except `klippy/extras/gcode_shell_command.py`, which is not part of the stock firmware and is added through the same mechanism:

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

## How patches are applied and recovered

Forge-X treats Klipper hotpatching as a reversible operation.

During initialization, every stock Klipper file that Forge-X replaces is first preserved as a neighboring `.bak` file. The Forge-X version is then linked into the original location. The stock MCU firmware is not reflashed as part of this process.

A normal uninstall performs the reverse operation automatically:

1. Forge-X Klipper plugins are removed.
2. Every patched Klipper file with a saved `.bak` is replaced by its original copy.
3. Forge-X changes to the printer configuration are restored through the configuration backup/restore path.
4. The mod runtime is removed only after those restoration steps have run.

This means the Klipper patch set is both auditable and directly reversible; uninstall does not depend on reconstructing the original files from memory or downloading a matching Klipper tree later.

### Recovery if Forge-X cannot start normally

The rollback path is not limited to a running Forge-X installation.

Forge-X 1.4.2 includes an **early boot recovery environment** that runs before the normal Forge-X runtime. The boot guard tracks interrupted initialization and recovery states and can fail open to stock firmware instead of repeatedly trying to start a broken mod.

From the recovery environment it is possible to:

- boot the stock firmware while bypassing Forge-X;
- uninstall Forge-X and restore its patched/configured files;
- reset configuration;
- enable recovery SSH;
- install a supported firmware/recovery image.

The recovery lifecycle uses the stock-side runtime where possible and can invoke the uninstall script directly, so removing the mod does **not** require a successful normal Forge-X/Klipper startup.

Forge-X also publishes separate USB recovery images:

- a **Dry Run** image that verifies system files and reports corruption without changing them;
- a **Full Recovery** image that carries a system-data backup and restores corrupted files;
- dedicated uninstall/factory recovery paths for more severe cases.

So there are several independent layers of recovery: per-file Klipper backups, normal uninstall rollback, early boot recovery, and external recovery images. A failed Klipper hotpatch is therefore not intended to leave the printer dependent on that same patched Klipper instance in order to undo the change.

See [Uninstall](UNINSTALL.md) and [Firmware Recovery Guide](RECOVERY.md) for the user-facing recovery procedures. For contributor-level details of the overlay itself, see [Built-in Klipper patching](../openwiki/workflows/klipper-patching.md).
