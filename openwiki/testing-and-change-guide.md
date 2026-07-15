# Testing and change guide

## Validation posture

No repository-owned automated test suite or build/test configuration was identified in the inspected tree. The only GitHub Actions workflow, [`.github/workflows/stale.yml`](../.github/workflows/stale.yml), maintains inactive issues; it does not test firmware, macros, or packages. Therefore the effective validation model is **targeted static review plus controlled on-device testing**.

This is not a gap to hide: Forge-X changes early boot, services, printer motion, calibration, and low-memory behavior on specific hardware. A unit-like syntax check cannot prove the crucial outcomes.

## Minimum checks by change area

| Change area | Static review | On-device / integration validation |
|---|---|---|
| `.shell/S00init`, `.shell/S55boot`, `.shell/S99root`, mounts | Trace normal, soft-skip, hard-failure, and first-run branches; verify every referenced deployed path | Cold boot default stock mode; a controlled skip/fallback; first-run DB/bootstrap where feasible; retain logs |
| `.root/*` service scripts / `moonraker.conf` | Confirm chroot mount/path ownership, start-stop symmetry, PID handling, port/path consistency, config include ownership, and updater semantics; see [Chroot environment and web runtime](workflows/chroot-and-web-runtime.md) | Moonraker readiness on 7125, static UI reachability on 80, browser-to-Moonraker connection, service disable switches, display gates, and reboot persistence |
| `mod_params.json` / `zchanges.sh` | Confirm schema, defaults, migration/deprecation, macro declaration, and each keyed side effect | Toggle parameter, verify expected restart/reboot/service action and persistence; restore default |
| `config/` and `macros/` | Trace includes, renamed macros, shell command arguments, safe motion preconditions | Controlled homing, calibration, pause/resume/cancel, start/end print path using safe test conditions; recalibrate before production prints |
| `.py/klipper/patches` and `plugins` | Confirm target stock-module path/layout, `.bak` backup and stale-link cleanup behavior, plugin vs replacement classification, and optional tuning interactions; see [Built-in Klipper patching](workflows/klipper-patching.md) | Exercise the affected Klipper feature and inspect relevant logs; validate supported firmware version and uninstall/rollback path where the deployment changes |
| Swap/ZRAM/memory tuning | Review defaults, bundled modules/scripts, and interactions with UI/services | Reboot, confirm selected swap mode, observe service stability under representative load |
| Camera, screen, remote access | Review mode-specific config routing, process/pipe ownership, port boundaries, user-state ownership, and stock fallback; see [Screen modes and Feather](workflows/screens-and-feather.md) | Enable/disable cleanly between stock, Feather, headless, and affected Guppy modes; verify DHCP/network prerequisites, display status/error output, browser control, fallback, and recovery route |

Use the operator docs as test procedures where applicable—especially [`docs/PRINTING.md`](../docs/PRINTING.md), [`docs/CALIBRATION.md`](../docs/CALIBRATION.md), [`docs/SCREEN.md`](../docs/SCREEN.md), and [`docs/RECOVERY.md`](../docs/RECOVERY.md).

## Required safety gates

1. **Do not test new motion behavior on an uncalibrated printer.** Installation/uninstallation and config tuning can invalidate bed mesh/Z offset.
2. **Retain a recovery route before touching boot code.** Confirm the documented dual-boot/USB recovery route and preserve logs.
3. **Exercise stock mode before alternate modes.** Stock is the default and fallback path.
4. **Treat calibration/macro edits as hardware-risk changes.** Review the full macro chain; test with conservative conditions.
5. **Avoid destructive broad tests.** Do not wipe Moonraker state, overwrite user-owned `mod_data`, or alter private SSH material simply to validate a source change.

## Recent history: why these checks matter

Recent Git history provides concrete regression themes:

- **Firmware compatibility:** `adfed5c` added stock firmware 5.0.x/5.1.x support, expanding `S00init`, docs, and parameter behavior. Validate against the supported firmware range rather than assuming all stock releases work.
- **Memory constraints:** `b9322c4` added compressed ZRAM swap; `e637aff` capped glibc malloc arenas for Moonraker and GuppyScreen. Resource changes must be tested with the relevant services/screens running.
- **Boot/UI stability:** `0acb2ae` suppresses a stock slicer-upgrade nag only in stock-screen use; `c327a47` made Dropbear arguments configurable. Changes often have mode-specific effects.
- **Print safety:** `c243f88` resets mesh before nozzle cleaning to avoid a bad measured Z/potential scratching when mesh validation and cleaning coexist. Earlier commits refined cooldown conditions and mesh-profile defaults. Macro changes need physical workflow validation.
- **Klipper correctness:** `de8e6ab` fixes a shaper-calibration hang by draining a child result pipe. Patch changes require exercising the exact affected action, not merely booting.
- **Scheduling:** `5426a14` adds optional `klipper_rt` (`SCHED_RR`) behavior through parameter and restart paths. Validate enable/disable and service recovery.

An advanced pressure-advance patch was added and then reverted in nearby history. This reinforces the rule: do not retain a patch merely because it applies; prove it is safe and compatible in this target stack.

## Documentation expectations for future changes

Update this wiki when a change alters one of the following:

- boot/fallback/recovery topology;
- persistent-state ownership or migration;
- supported firmware/hardware envelope;
- externally visible service endpoint, updater, or authentication posture;
- display-mode, slicer, calibration, or macro contract;
- required validation or recovery procedure.

Keep the detailed operator instructions in `docs/` current as well. If validation cannot be performed on hardware, state that limitation in the change/release notes rather than implying coverage.
