# Contributing to Forge-X

Forge-X controls real printer hardware, so changes are expected to come with
validation proportional to their risk.

Start with:

- [Development, testing, and release validation](/docs/DEVELOPMENT.md)
- [Testing and change guide](/openwiki/testing-and-change-guide.md)
- [Architecture overview](/openwiki/architecture.md)
- [Source map](/openwiki/source-map.md)

## Pull request checklist

- **Bug fixes and behavior changes:** add or update a regression test that
  fails without the change, whenever the behavior can be reproduced on the
  host. Keep that test in the suite so the bug stays fixed.
- **Host tests:** run the relevant suites
  (`python tests/run_host_tests.py --verbose`).
- **Feather / UI changes:** run the UI tests and visual regression. For
  workflows with several states, check the important states, not only the
  default page.
- **Printer regression:** pull requests that change any of the following must
  include a [printer regression](#printer-regression) run:
  - toolhead or bed motion, homing, probing, parking;
  - Z-offset or bed mesh;
  - heaters, fans, or filament handling;
  - pause, resume, or cancel;
  - Power Loss Recovery;
  - Klipper scheduling or timing;
  - boot, installation, update, recovery, or rollback.
- **Installation, update, migration, patching, backup, or uninstall:** keep a
  working rollback path. Patched stock files must stay restorable. A change
  that cannot be rolled back needs a design discussion before it is merged.
- **Safety checks:** do not remove or relax bounds, preconditions,
  confirmations, cleanup, or recovery guards without explaining why the current
  behavior is wrong and adding regression tests for the new behavior.

## Printer regression

The printer regression runs real workflows on an Adventurer 5M / 5M Pro: homing
and movement, heating, bed screws, bed mesh, Z-offset, KAMP, nozzle cleaning,
and optionally a real test print with pause, resume, cancel, and Power Loss
Recovery. See [Development, testing, and release validation](/docs/DEVELOPMENT.md#printer-regression)
for the full list.

**Before you start**

- The printer is calibrated, idle, in Feather mode, and the bed is empty.
- Someone watches the printer for the whole run. The tests move and heat it.
- Your changes are deployed to the printer. After changing Feather Python
  modules, restart the Klipper process completely (a normal `RESTART` can keep
  the old module loaded):

  ```sh
  /opt/config/mod/.shell/restart_klipper.sh --hard
  ```

**Run from a computer (recommended)**

This records the camera, collects the results from the printer, and builds a
report. From the repository root:

```bash
python3 -m tests.printer_regression \
  --printer <printer-host> \
  --suite core \
  --confirm-unattended-physical-test
```

- `--suite core` runs the main physical suite.
- `--suite all` runs `core` and then a real test print. Use it for changes that
  can affect printing or Power Loss Recovery. The printed model stays on the
  bed afterward.
- `--suite material` runs the filament workflows. It cannot be combined with
  another physical suite.
- Camera recording needs FFmpeg on the computer. Without it, add `--no-camera`.
- `--output <dir>` sets where the report and recordings are saved.

**Run from the printer console**

```gcode
_FEATHER_UI_TEST ACTION=RUN SUITE=FULL CONFIRM=1
```

Single phases can be run with `SUITE=UI`, `RENDER`, `MOTION`, `HEAT`, `SCREWS`,
`MESH`, or `Z`. Use `_FEATHER_UI_TEST ACTION=STATUS` to check progress and
`_FEATHER_UI_TEST ACTION=ABORT` to stop the run.

**In the pull request**, list the suites you ran, the printer model, and the
result. Attach the report or the log of any failed phase.

All options, safety checks, and output formats are described in the
[Testing and change guide](/openwiki/testing-and-change-guide.md).

## Pull request description

For non-trivial changes, include:

- what behavior changed;
- which tests were added or updated;
- which test suites were run;
- which printer regression suites were run, and the result;
- whether visual regression was needed and done;
- anything that could not be tested.

"Works for me" is useful, but it does not replace a repeatable test.

If you do not have an Adventurer 5M / 5M Pro, you can still open a pull
request. Say clearly that the printer regression was not run, and a maintainer
will run it before the change is merged.
