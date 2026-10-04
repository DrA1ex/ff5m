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
- **Hardware validation:** changes to any of the following need a test on a
  real printer before release:
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

## Pull request description

For non-trivial changes, include:

- what behavior changed;
- which tests were added or updated;
- which test suites were run;
- whether the change was tested on a real printer;
- whether visual regression was needed and done;
- anything that could not be tested.

"Works for me" is useful, but it does not replace a repeatable test.

You do not need an Adventurer 5M / 5M Pro to open a pull request. If the change
was not tested on hardware, say so, and a maintainer will do it before release.
