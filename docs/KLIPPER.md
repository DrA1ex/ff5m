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

- [What Forge-X changes compared with the stock firmware](https://github.com/DrA1ex/klipper-ad5m/compare/182e96ab8394201923e095ede450f2468f293d16...main)
- [What the stock firmware changes compared with upstream `v0.11.0`](https://github.com/DrA1ex/klipper-ad5m/compare/e02b725602067a2cd098a62be9a4bb10fc74a9bd...182e96ab8394201923e095ede450f2468f293d16)
- [Description of the repository](https://github.com/DrA1ex/klipper-ad5m/blob/main/AD5M.md)

> [!NOTE]
> The patches were written as whole files, not as a series of commits on top of Klipper. The history was reconstructed by topic afterwards, so the order of the commits is chosen for reading and is not the order of the original work. What is exact: the final files are byte-identical to the files in this repository, and the stock commit has the same MD5 sums as the stock firmware for the four files it covers. One stock file, `virtual_sdcard.py`, is not reconstructed yet; see the description of the repository.

To check it yourself, run `python3 tests/verify_klipper_fork.py --fork https://github.com/DrA1ex/klipper-ad5m`. It clones the commit recorded in [`docs/klipper-ad5m-manifest.json`](klipper-ad5m-manifest.json) and compares every Python file with `.py/klipper/patches/`. Without `--fork` it only checks the SHA-256 sums in the manifest, which is also done by the host tests (`tests/test_klipper_fork_manifest.py`). The prebuilt `c_helper.so` is listed in the manifest with its SHA-256. The C sources in the history repository are the sources it is built from.

Where to find each change:

| Change | Commits in the history repository |
| --- | --- |
| Stock FlashForge changes (reconstructed) | [`182e96a`](https://github.com/DrA1ex/klipper-ad5m/commit/182e96ab8394201923e095ede450f2468f293d16) |
| `toolhead.py`: lookahead flush time and `tune_klipper` | [`86fe15e`](https://github.com/DrA1ex/klipper-ad5m/commit/86fe15e8550a473898e3eb544a7fbb328f8274e1) |
| `toolhead.py`: `buffer_time_high` 1.5 s | [`954456b`](https://github.com/DrA1ex/klipper-ad5m/commit/954456b1294752b2e88c937ea5ea709ee89d3f3d) |
| `toolhead.py`: yield while buffering (50cb362) | [`ccde55d`](https://github.com/DrA1ex/klipper-ad5m/commit/ccde55ddaaacb9d245a2a67a055bf705f65553d2) |
| `toolhead.py`: low-buffer flush logging | [`7938c6a`](https://github.com/DrA1ex/klipper-ad5m/commit/7938c6ad559de47fedf03a686dab2640376fafc3) |
| `reactor.py`: fd event dispatch (bb88985 and prerequisites) | [`c126afb`](https://github.com/DrA1ex/klipper-ad5m/commit/c126afbe750e160e43ea5c5e6201a7234bf65443), [`ff5f2e5`](https://github.com/DrA1ex/klipper-ad5m/commit/ff5f2e539a74370e262db3de7dd6c100069d26ca), [`e43fa2c`](https://github.com/DrA1ex/klipper-ad5m/commit/e43fa2cce44be45ba85a04d7297c224c01296cb9) |
| `reactor.py`: `fileno()` kept, per-batch snapshot | [`a28433f`](https://github.com/DrA1ex/klipper-ad5m/commit/a28433f5312994c17a62b47f4cc2a3d391bc92e6), [`a628234`](https://github.com/DrA1ex/klipper-ad5m/commit/a6282342ff2cdee65266a0895827c2405df6f1ea) |
| `mcu.py`: TRSYNC reporting and scheduling (dab39c02, 1ea9f3aa, 8e6e467) | [`bf9dcfe`](https://github.com/DrA1ex/klipper-ad5m/commit/bf9dcfe8f51c27b688ed35cfd5f267704ce522fc), [`cc7aab5`](https://github.com/DrA1ex/klipper-ad5m/commit/cc7aab5f081dbfa922c966f5e05d89af4f47d7ca), [`256df48`](https://github.com/DrA1ex/klipper-ad5m/commit/256df4861685e6c51dcec1f18bd536a1a1b44abd) |
| `mcu.py`: `tune_klipper` TRSYNC timeout, homing logs | [`bde126b`](https://github.com/DrA1ex/klipper-ad5m/commit/bde126be1108d0b7ac58d8465ec3c2f83618a01c), [`9049ea1`](https://github.com/DrA1ex/klipper-ad5m/commit/9049ea136bce33612b7d7c514d9f899525bc2fb3) |
| `servo.py` / `mcu.py`: PWM cycle alignment (2b4c55f) | [`1cd00fa`](https://github.com/DrA1ex/klipper-ad5m/commit/1cd00fafbf55ed96618b87d4af46d782394cf496) |
| `tmc.py`: direction-inversion event (8dd798e), mutex (8ea7be5), driver checks | [`b7a8c09`](https://github.com/DrA1ex/klipper-ad5m/commit/b7a8c09b3f3578809288b2abe3e32939317696a9), [`c9f616b`](https://github.com/DrA1ex/klipper-ad5m/commit/c9f616baf0a60c3aa31d3bc736a850dec0d77495), [`b6e0cae`](https://github.com/DrA1ex/klipper-ad5m/commit/b6e0cae1cbae3fc9bd8d2d58424122c2b3adaf72) |
| `buttons.py` (92fe8f1), `gcode_button.py` (b8c936f) | [`d821f55`](https://github.com/DrA1ex/klipper-ad5m/commit/d821f55a847e53ca99fc6745d8d012de728927ac), [`93c5dda`](https://github.com/DrA1ex/klipper-ad5m/commit/93c5dda6c5b34a4cd4fd0818be2e200af0637f4f) |
| `probe.py`: `PROBE_ACCURACY` (a353efa) | [`dced81f`](https://github.com/DrA1ex/klipper-ad5m/commit/dced81f0f1bc1240b7c2df6dd357f8ccb72d8ab8) |
| `heaters.py`: low `max_power` (01f089e), cold-extrusion override | [`07b86ba`](https://github.com/DrA1ex/klipper-ad5m/commit/07b86babb06bb8bf7a521f38c08faa8f64f62363), [`4a2c60a`](https://github.com/DrA1ex/klipper-ad5m/commit/4a2c60a3b1b87ffdc29530d869978f128aa281b6) |
| `virtual_sdcard.py`: file offsets (600e89a), hidden files, `load_file()`, print-time metadata, Python 2 | [`ad06680`](https://github.com/DrA1ex/klipper-ad5m/commit/ad066800d110bee61d5e7bdcd3400e7e3fd5e715), [`0cac54c`](https://github.com/DrA1ex/klipper-ad5m/commit/0cac54c65236bfdb9d287d7df810ccb187f1eeff), [`faabdef`](https://github.com/DrA1ex/klipper-ad5m/commit/faabdef72bcca1d6477185634e8159f5c99fb6ff), [`03259fd`](https://github.com/DrA1ex/klipper-ad5m/commit/03259fd07cb790901de7a5ab5fcc151799d877cb), [`58f90b1`](https://github.com/DrA1ex/klipper-ad5m/commit/58f90b13ef9165027b22e9ce13cffb26225092da) |
| `shaper_calibrate.py`, `resonance_tester.py`: `square_corner_velocity`, pipe deadlock, plot data | [`ad6cf26`](https://github.com/DrA1ex/klipper-ad5m/commit/ad6cf263b62dbf02e0b317a79bb486aa64852a74), [`6ce567d`](https://github.com/DrA1ex/klipper-ad5m/commit/6ce567d42c8708bbe8774f513e3fa77e6806aab7), [`ef171c9`](https://github.com/DrA1ex/klipper-ad5m/commit/ef171c903df563d352b269f6b31d82c726b7032f), [`db4ecb8`](https://github.com/DrA1ex/klipper-ad5m/commit/db4ecb8c5602369d7954328363f7ce384709831d) |
| `kinematics/extruder.py`, `chelper/`: dynamic pressure advance (c84d78f3) | [`f75b42d`](https://github.com/DrA1ex/klipper-ad5m/commit/f75b42d053b9c9733c2e09dc9fdc47cd9b40a0c7) |
| `gcode.py`: command names (5493c60), regex (0087f04), status (6676c1d) | [`b684880`](https://github.com/DrA1ex/klipper-ad5m/commit/b6848803ed3e63fa09d20a81de1a3af5c8a390b6), [`f9580e8`](https://github.com/DrA1ex/klipper-ad5m/commit/f9580e8e549b5fe3ad0c4ff96085345ef998c512), [`9512876`](https://github.com/DrA1ex/klipper-ad5m/commit/95128762cf0188d50e20b696639c467dfee40a57), [`69e18ca`](https://github.com/DrA1ex/klipper-ad5m/commit/69e18ca700c54e46628c6322f7c299cd3d6c301f) |
| `gcode.py`: error reporting, immediate commands, pipe input | [`8fa6abb`](https://github.com/DrA1ex/klipper-ad5m/commit/8fa6abb17451c5266ee07a9a643bbd6ff7c1cc4a), [`e71e28b`](https://github.com/DrA1ex/klipper-ad5m/commit/e71e28b05b8d6b6aaad16b2223cc2f1f4edd433e), [`a383eb0`](https://github.com/DrA1ex/klipper-ad5m/commit/a383eb088fb9f828a5a1f7f125b8299ef5260b06) |
| `gcode_move.py`: `base_position`, restore log, `RESET_GCODE_ORIGIN` | [`06d31c1`](https://github.com/DrA1ex/klipper-ad5m/commit/06d31c1cf2b7541824fd7a0b23c7049b289b441a), [`ae45352`](https://github.com/DrA1ex/klipper-ad5m/commit/ae453523da7ae29c44266db9deb1819a4c7dfb71), [`364f552`](https://github.com/DrA1ex/klipper-ad5m/commit/364f552ae71fa541b48a94fa52af9ae1c5a43c09) |
| `homing.py`: logging | [`66c91a9`](https://github.com/DrA1ex/klipper-ad5m/commit/66c91a9cce5f7aab7c9886c17479dcd8a394ecd0) |
| `temperature_sensor.py`: threshold G-code | [`a20c2ae`](https://github.com/DrA1ex/klipper-ad5m/commit/a20c2aeeb513614d0ea3530797f004a6f32e08d5) |
| `gcode_shell_command.py` | [`3fa2b0f`](https://github.com/DrA1ex/klipper-ad5m/commit/3fa2b0f97ce3dda8253518ff52e14544a7d026da) |
| `configfile.py`, `led.py`, `statistics.py` | [`c2516bb`](https://github.com/DrA1ex/klipper-ad5m/commit/c2516bb140cc98fe2aa3deb3744cf5414de86542), [`e11ec5b`](https://github.com/DrA1ex/klipper-ad5m/commit/e11ec5b327b917eb037162d3b83d3638d535bc27), [`6137800`](https://github.com/DrA1ex/klipper-ad5m/commit/6137800612d25500bb7ef481667ceaf90a97a0d0) |
| `chelper/__init__.py`: library path | [`ef95592`](https://github.com/DrA1ex/klipper-ad5m/commit/ef955923d5aef700982b05bf1f024cc4fd5d3ba0) |

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
