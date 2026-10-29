# Optional Phobos updates

Moonraker Update Manager can update the source-built Phobos Cortex-A7 package
from `maci0/phobos`. This is an optional **ZIP installation**, not a switch of
the API daemon: Moonraker stays the default.

## First installation

1. Download `phobos-arm-linux-musleabihf.zip` and `SHA256SUMS` from the same
   Phobos release and verify the ZIP against the manifest.
2. Extract it into a new, dedicated `/data/phobos` directory. It contains the
   `phobos` executable, `.version`, `release_info.json`, and `LICENSE`.
3. Restart Moonraker when it is safe to do so. The `[update_manager phobos]`
   entry in `moonraker.conf` registers only once `/data/phobos` exists; while
   it is missing, the entry is skipped without a startup warning.

Never use `/data` itself as the update path: a ZIP update replaces the whole
application directory. Keep configuration, database, and logs outside
`/data/phobos`.

An existing versioned-binary or manual Phobos installation is not migrated. To
run the managed copy, point the Phobos launcher you chose at
`/data/phobos/phobos` as described in Phobos's own setup instructions.

## Update behavior

- The entry requests the asset `phobos-arm-linux-musleabihf.zip` by name, so it
  does not depend on GitHub's asset order and cannot pick an AArch64 build or
  a raw executable. Rollback uses the same selector. A missing or duplicated
  asset name fails before any installed file is touched.
- With `asset_name` set, the reported version is the release tag, not the
  release title.
- `is_system_service: False`: the updater starts and restarts no daemon. After
  an update, restart the API service that runs Phobos yourself. No Klipper
  restart or printer reboot is needed.

`enable_if_path_exists` and `asset_name` are options added to the bundled
Moonraker. The Phobos entry sets `enable_if_path_exists: True`. Any other
entry that omits it gets the default `False` and is registered as before, and
a web or ZIP entry without `asset_name` keeps taking the first release asset.
