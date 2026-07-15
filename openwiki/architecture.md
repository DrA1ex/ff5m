# Architecture overview

Forge-X adapts the stock Flashforge appliance instead of replacing it with a conventional Linux installation. A stock-side boot layer prepares a persistent Buildroot chroot and then starts Klipper-adjacent services from that isolated runtime. This layout preserves a stock fallback while adding Moonraker, web clients, patches, and printer-specific macros.

## Runtime layers

```text
Vendor init
  ├─ .shell/S00init       prepare data, swap, chroot, links, patches, config, DB
  ├─ .shell/S55boot       select display/network boot path; start MCU + Klipper
  └─ .shell/S99root       initialize database if needed; start/stop chroot services
       └─ .root/start.sh  NTP, Moonraker, HTTP, optional Guppy, user init scripts
```

### Stock-side boot boundary

[`.shell/S00init`](../.shell/S00init) is the main initializer. It first honors special boot flags: requested skip and boot-failure paths avoid loading the mod and let the stock firmware proceed. In the normal path it mounts `/data`, rotates logs, initializes swap, sets up the Buildroot environment, creates vendor-init symlinks, records version metadata, applies Klipper patches, repairs configuration, and migrates an existing Moonraker database.

`S00init` also applies optional `block_cloud` handling. When enabled it adds *marked* `/etc/hosts` entries and removes only entries it previously added when disabled. It is intentionally opt-in because it breaks stock cloud-dependent features; it is not a firewall.

[`.shell/S55boot`](../.shell/S55boot) selects the active display path. In Feather, Guppy, and headless modes it performs DHCP setup, starts the MCU utility and Klipper, and falls back to stock display if network initialization fails. Stock mode retains the vendor application path. See [Screen modes and Feather](workflows/screens-and-feather.md) for configuration selection, first-party Feather rendering, and mode-specific workflow consequences.

[`.shell/S99root`](../.shell/S99root) is the service coordinator. On first use it starts/stops the chroot to create the Moonraker database, applies migrations, restores OTA state, then starts normal services. It bypasses this entirely when a skip flag is active.

## Chroot services and public surfaces

[`.root/start.sh`](../.root/start.sh) starts NTP, Moonraker unless `disable_moonraker=1`, the HTTP server unless `disable_web=1`, Guppy dependencies when selected, and user init scripts. The chroot shares kernel resources and deliberate bind mounts with the stock root; Moonraker uses the shared `/tmp/uds` socket to reach stock Klipper, while BusyBox `httpd` serves Fluidd/Mainsail static assets and browsers call Moonraker directly. See [Chroot environment and web runtime](workflows/chroot-and-web-runtime.md) for mounts, service ownership, tooling, controls, and network boundaries.

[`moonraker.conf`](../moonraker.conf) binds Moonraker to `0.0.0.0:7125` and connects it to Klipper through `/tmp/uds`. The repository serves Fluidd and Mainsail through the HTTP runtime. The tracked Moonraker config disables API-key authentication and includes mutable operator overrides from `mod_data/user.moonraker.conf`; network exposure is therefore a deliberate high-trust boundary.

Recent low-memory work is architectural rather than cosmetic: `.root` service wrappers cap glibc allocator arenas, and [`.shell/boot/init_swap.sh`](../.shell/boot/init_swap.sh) provides ZRAM alongside eMMC/USB swap modes.

## Configuration and persistence

| Concern | Tracked source | On-device mutable state |
|---|---|---|
| Setting schema/defaults | [`mod_params.json`](../mod_params.json) | `/opt/config/mod_data/variables.cfg` |
| Display-specific Klipper entry points | [`config/`](../config/) and [`.cfg/`](../.cfg/) | selected runtime config |
| Shared motion/safety macros | [`macros/base.cfg`](../macros/base.cfg) | macro variables/config backups |
| Moonraker behavior | [`moonraker.conf`](../moonraker.conf) | `mod_data/user.moonraker.conf`, database, logs |
| UI provisioning | [`sql/`](../sql/) | Moonraker SQLite database/checkpoint |

`[mod_params]` in [`macros/base.cfg`](../macros/base.cfg) writes persistent values to `variables.cfg`, validates them against `mod_params.json`, and invokes the parameter-change hook. The hook in [`.shell/commands/zchanges.sh`](../.shell/commands/zchanges.sh) owns immediate effects such as display switching, swap initialization, camera/tunnel lifecycle, restart/reboot behavior, tuning, and recovery-state reset. Keep schema, macro use, hook behavior, and documentation aligned.

## Klipper overlay and migrations

[`.py/klipper/`](../.py/klipper/) is a patch/plugin overlay, not a complete local Klipper checkout. During boot `S00init` symlinks plugins into the stock `extras/` directory, replaces selected stock modules with symlinked overlay files while retaining `.bak` originals, then applies separately toggleable `tune_klipper` edits. Uninstall reverses those links/backups and tuning values. See [Built-in Klipper patching](workflows/klipper-patching.md) for the lifecycle, ownership rules, and change procedure.

[`.shell/migrate_db.sh`](../.shell/migrate_db.sh) applies lexically ordered SQL migrations from [`sql/`](../sql/) to Moonraker’s SQLite state and advances its checkpoint only after success. Changes must cover a new install, normal upgrade, and failed/retried migration.

## Change checklist

1. Trace normal boot **and** requested/failure skip paths.
2. Test stock plus every affected alternative display mode; non-stock startup depends on networking.
3. Preserve ordering: swap/chroot setup, patch/config repair, database migration, then services.
4. Do not assume `/opt/config/mod_data` is versioned source; do not document or inspect private user material.
5. Before release, exercise reboot, fallback, service restart, and recovery/uninstall on supported hardware/firmware.
