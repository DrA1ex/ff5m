# Contributing to Forge-X

Forge-X controls real printer hardware, so changes are expected to come with
validation proportional to their risk.

Start with:

- [Development, testing, and release validation](/docs/DEVELOPMENT.md)
- [Testing and change guide](/openwiki/testing-and-change-guide.md)
- [Architecture overview](/openwiki/architecture.md)
- [Source map](/openwiki/source-map.md)

## Pull request checklist

For a behavior-changing pull request:

- add or update a regression test when the behavior can be reproduced on the
  host;
- run the relevant host test suite;
- document what changed and what was tested;
- state clearly if real-printer validation has **not** been performed;
- run visual regression for Feather/UI changes;
- require hardware validation before release for motion, heating, probing,
  mesh/Z-offset, pause/resume/cancel, recovery, timing, installer, update, or
  rollback changes;
- preserve backup, uninstall, and recovery paths when modifying stock files or
  installation state.

If a contributor does not have an Adventurer 5M/5M Pro available, that does not
prevent a pull request. Mark the hardware-validation gap explicitly so a
maintainer can complete it before release.

Do not remove safety bounds, preconditions, confirmations, or rollback behavior
without explaining why the existing contract is wrong and adding regression
coverage for the replacement.
