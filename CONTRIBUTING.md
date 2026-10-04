# Contributing to Forge-X

Forge-X controls real printer hardware, so changes are expected to come with
validation proportional to their risk.

Start with:

- [Development, testing, and release validation](/docs/DEVELOPMENT.md)
- [Testing and change guide](/openwiki/testing-and-change-guide.md)
- [Architecture overview](/openwiki/architecture.md)
- [Source map](/openwiki/source-map.md)

## Pull request checklist

Use the [pull request template](/.github/pull_request_template.md). It asks
for the problem, how to reproduce it, the versions you tested on, and the test
results. How to run each check is described in
[Testing](/docs/DEVELOPMENT.md#testing).

- **Bug fixes and behavior changes:** add or update a regression test that
  fails without the change, whenever the behavior can be reproduced on the
  host. Keep that test in the suite so the bug stays fixed.
- **Host tests:** run `python tests/run_host_tests.py --verbose`.
- **Printer regression:** pull requests that change any of the following must
  attach a [printer regression](/docs/DEVELOPMENT.md#printer-regression) run
  as a `.zip` archive. A video recording is not required.
  - toolhead or bed motion, homing, probing, parking;
  - Z-offset or bed mesh;
  - heaters, fans, or filament handling;
  - pause, resume, or cancel;
  - Power Loss Recovery;
  - Klipper scheduling or timing;
  - boot, installation, update, recovery, or rollback.
- **Feather / UI changes:** collect
  [screenshots from the printer](/docs/DEVELOPMENT.md#collect-screenshots-from-the-printer)
  and attach them as a `.zip` archive. For workflows with several states, cover
  the important states, not only the default page.
- **New mechanisms or uncovered behavior:** add
  [test coverage](/docs/DEVELOPMENT.md#adding-test-coverage) in the same pull
  request. A new Feather page or state also needs a text description for the
  automated visual check.
- **Installation, update, migration, patching, backup, or uninstall:** keep a
  working rollback path. Patched stock files must stay restorable. A change
  that cannot be rolled back needs a design discussion before it is merged.
- **Safety checks:** do not remove or relax bounds, preconditions,
  confirmations, cleanup, or recovery guards without explaining why the current
  behavior is wrong and adding regression tests for the new behavior.

"Works for me" is useful, but it does not replace a repeatable test.

If you do not have an Adventurer 5M / 5M Pro, you can still open a pull
request. Say clearly that the printer regression was not run, and a maintainer
will run it before the change is merged.
