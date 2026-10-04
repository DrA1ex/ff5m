# User Klipper plugins and patch overrides

User packages live under `/opt/config/mod_data/plugins/` and survive repository
updates. The existing [overlay library](../../.shell/klipper_overlay.sh) applies
them after Forge-X's own files during [normal initialization](../../.shell/init-main.sh).
This implementation extends the 1.4.2 overlay and config batch; it adds no boot
guard, daemon or separate patch application path.

## Package layout

```text
mod_data/
├── user.cfg                    # Existing tuning and macros
├── plugins.cfg                 # Generated aggregate; do not edit
└── plugins/
    └── my_plugin/
        ├── config.cfg          # Optional package configuration
        ├── settings.cfg        # Optional relative include
        ├── plugins/
        │   └── my_plugin.py    # New extra module
        └── patches/
            └── extras/
                ├── fan.py             # Replace an original Klipper module
                └── virtual_sdcard.py  # Replace a Forge-X patch (requires option)
```

All three components are optional. A missing `plugins/` root
also works: the aggregate is created empty.

| Present components | Result |
|---|---|
| None | Empty aggregate, no user links |
| `config.cfg` | Include its config |
| `plugins/` | Link permitted new extras |
| `patches/` | Replace original Klipper modules; optionally override Forge-X patches |
| Any combination | Apply every present component if the whole package passes validation |

Package names allow letters, digits, underscores and hyphens; Python module
names must be valid identifiers. Only top-level `plugins/*.py` and recursive
`patches/**/*.py` files are linked. `plugins/__init__.py` is reserved; existing
Klipper package initializers may be replaced through `patches/`. Symlinked packages,
component directories, code files and `config.cfg` are rejected. A package's
`disabled` file excludes that package on the next normal initialization.

Linking an extra makes it available to import. Extras that require a config
section still need one, usually in `config.cfg`; linking alone does not load them.
Patch files replace the imported module and can work without any package config.

## Configuration and batching

The existing `cfg_backup.py` batch installs one `[include plugins.cfg]` into
`mod_data/user.cfg`. **The aggregate is created before that include is installed**,
even when no packages exist. Config repair preserves an existing aggregate.
If directory/file creation fails, it logs `@@`, omits the new plugin include and
still runs the display/base/tuning batch. An already installed include cannot be
repaired when its target is unwritable; the log exposes that filesystem problem.
Failures in the optional user overlay are also logged without aborting the built-in
overlay. On failure the aggregate is emptied so it cannot retain configs for
modules whose links were removed; a failure to empty it is logged separately.
If replacing a stock module fails, the new backup is removed and the stock file
stays in place. Temporary link and aggregate files are cleaned on both success
and failure.

The overlay builds all absolute `[include .../config.cfg]` entries in memory,
compares them with the current aggregate and writes it, through an atomic rename,
only when its contents change. There is no config-editing process per package.
Klipper reads each config at its original path, so `[include settings.cfg]`
resolves within that package.

Keep configuration depending on a user module in its package config or its
relative includes. A dependent section placed directly in `user.cfg` remains
active when package configs are cleared. Other tuning, calibration and macros
in `user.cfg` are preserved.

## Conflicts and patch priority

The overlay checks every component of a package before linking its Python files
or including its config. A conflict or invalid component produces an `@@` error
and skips the **entire package**, including its config. Other packages and mod
initialization continue. When a previously accepted package develops a conflict,
its old links are rolled back and its config is omitted on the next normal boot.
Missing optional components are valid. The overlay validates paths and ownership;
it does not check Python or Klipper config contents for runtime errors.

| File placement / target | Option `0` (default) | Option `1` |
|---|---|---|
| `plugins/new_module.py`, unused module name | Allowed | Allowed |
| `patches/<path>.py`, original Klipper module | Allowed | Allowed |
| `patches/<path>.py`, currently linked Forge-X patch | Reject whole package | Allowed |
| Existing Forge-X plugin, another user package or foreign symlink | Reject whole package | Reject whole package |

`plugins/` adds new top-level extras. Existing extras/core module names and Python
package names cannot be replaced through this directory; use `patches/` for stock
modules instead. `patches/` follows paths relative to `/opt/klipper/klippy/`, such
as `gcode.py`, `extras/fan.py` or `kinematics/cartesian.py`. New modules belong
in `plugins/`; a patch must have an existing target (or its surviving stock backup).
Forge-X's own extra plugins stay protected. Symlinked target directories and
invalid backups are rejected to keep changes inside Klipper and reversible.

Experimental `user_plugins_override_patches` (default `0`) only controls replacing
Forge-X patches. Enable it with `SET_MOD PARAM=user_plugins_override_patches VALUE=1`,
then reboot the printer. The option is declared in
[`mod_params.json`](../../mod_params.json) under EXPERIMENTAL. Packages are processed
in filename order; the first accepted package owns a target, and a later package
with the same target is rejected in full, even when overrides are enabled.

Stock replacements follow the Forge-X `.bak` convention: the stock file is kept
in adjacent `.bak` and restored when its user patch is removed. Replacing a
Forge-X patch changes only symlink priority; it never replaces the stock `.bak`.
Removing that override returns to Forge-X. If Forge-X removed its patch, the user
patch can keep replacing the restored stock module without the override option.

Every switch is a single `rename()`, so the module name never disappears, even on
power loss. Installing a stock replacement first hard-links the stock file to
`.bak`, then renames a temporary symlink over the module; removal renames `.bak`
back over the link. An interrupted switch leaves either the old or the new
module; the next normal boot finishes it or rolls it back, removes a temporary
`.fx-new` link and drops a `.bak` that is still the same inode as the module.
Bytecode caches are cleared only when a link actually switches, which avoids
reusing code from a file with an identical timestamp and size.

**Before downgrading to a version without user patch support**, disable packages
containing patches (create their `disabled` files) and reboot normally while still
running the new version. This restores original Klipper modules and Forge-X patch
links before switching versions. Setting
`SET_MOD PARAM=user_plugins_override_patches VALUE=0` and rebooting removes Forge-X
overrides, but packages that only replace stock modules remain enabled. An older
overlay treats a remaining user override of a Forge-X patch as unmanaged, returns
an error and sends subsequent boots to stock. User extras are left in place by the
older overlay, but it does not reconcile their files/configs.

## Reboot, stock fallback and uninstall

Every normal printer reboot reconciles user packages. The overlay first plans
which package owns each target, from the package sources and the Forge-X file
lists, without touching the filesystem. Only links that differ from that plan are
added, retargeted or removed, and the aggregate is written only when it changes.
When nothing changed, a reboot writes nothing to the Klipper tree or `mod_data`.
Ownership does not depend on the previous boot: the same packages always produce
the same owners. Deleting a Python file, `config.cfg`, a component directory,
package or the entire plugins root rolls back those deleted components. Existing
components keep working.
An ordinary Klipper `RESTART` does not scan directories or update links.

For an explicit manual update while idle, use `init-main.sh reload`, then
`restart_klipper.sh --hard`, both under `/opt/config/mod/.shell/`.

In a stock boot (including Skip and initialization-failure fallback),
[`S55boot`](../../.shell/S55boot) **creates or empties `plugins.cfg` before stock
configuration restoration**. After successful clearing, the empty file remains
valid even if display/chroot restoration fails or times out. If clearing itself
fails, `@@` is logged and stock display preparation still runs. `user.cfg` tuning
and Python links remain intact.
Recovery retains the existing 1.4.2 flow; this feature adds no early Recovery
cleanup. On a later normal reboot, the overlay writes package configuration again.

This minimal design has no Klipper readiness monitor or automatic failed-start
quarantine/retry. In particular, user patches left in place can still affect
stock imports. Remove or disable a broken package and reboot normally to reconcile
its links. Stock fallback clears package configs; it does not restore Python code.

[Uninstall](../../.shell/uninstall.sh) uses the same overlay cleanup in removal
mode. It restores adjacent backups through owned symlinks even after source
packages were deleted, and removes owned links, the managed config include and
aggregate. It does not scan arbitrary `.bak` files, create modules from unowned
backups or replace regular files from them. In removal mode, it also restores
`.bak` files for surviving Forge-X or user `patches/` paths when their targets
are absent, including Forge-X native helpers. If both the ownership link and
that patch source are gone, the backup is retained for manual recovery.
Soft uninstall retains package sources and other user settings; hard uninstall
removes `mod_data`. Hard uninstall also deletes nonstock `gcode_shell_command.py`
and its bytecode even if generic backup restoration restored that file first.
`TAR_BACKUP` includes package sources.

## Known limitations

- Python reuses `__pycache__` bytecode while a source keeps the same size and
  modification time (to the second). Replacing a package file with a different
  one of equal size while preserving its timestamp (`cp -p`, `rsync -t`, archive
  extraction) can keep the old code. Run `touch` on the file or delete the
  adjacent `__pycache__` before restarting Klipper. Forge-X's own patches behave
  the same way.
- Links are switched one at a time. A filesystem write error in the middle of a
  package (full or read-only storage) can leave that package partially applied.
  Every module stays present, the aggregate is emptied and `@@` is logged. Fix the
  storage, then remove or disable the package and reboot normally.

## Validation

Use the project's virtual environment. Install pytest there for these focused
checks; the existing host runner and CI are unchanged:

```bash
.venv/bin/python -m pip install pytest
.venv/bin/python -m pytest tests/test_klipper_plugins.py tests/test_klipper_overlay.py tests/test_boot_recovery.py -q
```

The existing unittest host runner also discovers the new module. The bytecode
priority test explicitly enables bytecode writes, so it also runs with
`PYTHONDONTWRITEBYTECODE=1`.

Tests cover every optional component combination, missing/deleted roots and
components, 50-package batching, actual Klipper parsing with relative includes,
actual config include repair, stock clearing before failed restoration,
whole-package conflict rejection,
stock core/extra/package-initializer patches with the option disabled,
unchanged/foreign backups, bytecode priority, downgrade preparation, plugin
write failures, legacy reload behavior and complete soft/hard uninstall with
deleted sources. A repeated boot with unchanged packages must leave every inode,
mtime and ctime intact. Power loss is simulated after each filesystem change of
install, retarget, disable and delete transitions: modules must stay present and
the next boot must converge to the uninterrupted result. They also retain 1.4.2 recursive resources and native helper behavior.
Hardware operations are stubbed; actual printer/MCU validation remains separate.
