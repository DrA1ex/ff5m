# Forge-X Recovery Integration Notes

This file records the Recovery configuration reset contract and the remaining hardware validation work.

## RESET CONFIGURATION

`RESET CONFIGURATION` is implemented as a semantic hand-off from
`.py/recovery.py` to `.shell/boot/recovery.sh`. The lifecycle invokes
`.shell/commands/zreset_config.sh` with the already validated stock machine
identity; Python does not write configuration files.

The reset sources live under `.cfg/default`. Stock printer configuration is
selected from one of these explicit model directories:

- `printer/Adventurer5M`;
- `printer/Adventurer5MPro`.

Both sets came from the 3.1.3 / 2.2.3 factory images dated 2025-01-07. At the
time of integration their `printer.cfg` and `printer.base.cfg` files were
byte-identical, but they remain separated so model selection stays explicit
and later vendor divergence does not change the reset interface.

The shared `user.cfg`, `user.moonraker.conf`, and `variables.cfg` defaults came
from the unpacked Forge-X 1.4.2-beta-2 installer. Existing canonical defaults
for backup parameters, camera, SSH tunnel, and web UI selection remain the
source of truth. Reset restores:

- `printer.cfg` and `printer.base.cfg`;
- `mod_data/backup.params.cfg`, `camera.conf`, `ssh.conf`, and `web.conf`;
- `mod_data/user.cfg`, `user.moonraker.conf`, and `variables.cfg`.

It removes `printer.base.cfg.bak`, because normal initialization would
otherwise reapply the pre-reset calibration values over the factory printer
configuration. It deliberately leaves print files, network state, Moonraker's
database, and generated SSH key material alone.

Every source and target is validated as a regular non-symlink file. Defaults
are staged on the configuration filesystem before the first replacement; an
interrupted or failed multi-file update restores the previous files. Success
returns to Recovery with a reboot-required notice. The confirmation page has
`BACK`, `CREATE BACKUP`, and red `RESET` actions.

Before release, verify both model selections on hardware. For the first live
test, create and download a backup, exercise cancel/back, run reset only with
explicit destructive-test approval, reboot, and confirm that normal
initialization accepts the restored stock files.

## CHECK FILESYSTEM

The user-visible `CHECK FILESYSTEM` operation remains diagnostic-only and invokes `fsck -n`. It is separate from the existing Recovery/bootstrap exception in `mount_data_partition`, which may run `fsck -y /dev/mmcblk0p7` before mounting `/data`.

The operation was validated on an Adventurer 5M. Its `/proc/mounts` exposes the
root source as `/dev/root`, but that alias cannot be opened by `fsck`; the actual
root partition is `/dev/mmcblk0p6`. Recovery resolves that one stock alias before
invoking the checker. Both `/dev/mmcblk0p6` and the `/data` partition
`/dev/mmcblk0p7` use ext4 and returned exit code zero from the installed e2fsck
1.44.5 during read-only checks while mounted. The mounted-filesystem warning and
skipped journal recovery are expected in this diagnostic mode.

Validate `CHECK FILESYSTEM` on Adventurer 5M Pro hardware before release. Verify at minimum:

- the actual filesystem types used by the relevant partitions;
- availability and behavior of the corresponding `fsck` helpers;
- behavior while the relevant filesystems are mounted;
- clean-filesystem exit codes;
- error-filesystem exit codes;
- whether the current 45-second timeout is suitable.

Do not add speculative filesystem-specific handling before this hardware validation.
