# Optional Phobos updates

The update manager supports the source-built Phobos Cortex-A7 package from
`maci0/phobos`. This is an optional **ZIP installation**, not an automatic API
backend switch. Moonraker remains the default.

For the initial installation, download `phobos-arm-linux-musleabihf.zip` and
`SHA256SUMS` from the same Phobos release. Verify the ZIP against the manifest,
then extract it into a new, dedicated `/data/phobos` directory. It contains an
executable named `phobos`, `.version`, `release_info.json`, and `LICENSE`.
Never configure `/data` itself as an update path: a ZIP update replaces the
application directory. Keep configuration, database and logs outside this directory.

Restart only the API service when it is safe to do so to register the optional
entry. It appears automatically once `/data/phobos` exists; missing installations
are skipped without startup warnings. The existing versioned-binary/manual Phobos
installation is not migrated automatically. Point an explicitly chosen Phobos
launcher at `/data/phobos/phobos` using Phobos's own setup instructions.

The entry requests `phobos-arm-linux-musleabihf.zip` explicitly. It does not depend
on GitHub's asset order and cannot accidentally select an AArch64 binary or a raw
executable. The same selector is used for rollback. Missing or duplicate named
assets fail before installation files are touched. Existing web/ZIP entries without
`asset_name` retain their current first-asset behavior.

`enable_if_path_exists: True` is optional and defaults to False for existing client
entries. After first installing an optional package, reload/restart Moonraker to
register it. `is_system_service: False` prevents this package from starting or
restarting any daemon; restart the API service you explicitly use to run an updated
Phobos binary. No Klipper restart or printer reboot is required by this updater.
