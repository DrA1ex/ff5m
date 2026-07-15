# Chroot environment and web runtime

# Chroot environment and web runtime

Forge-X keeps the vendor root filesystem running, then mounts a second Buildroot-style root at `/data/.mod/.forge-x` and enters it with `chroot`. This is an execution boundary, not a VM or container: processes in the second root use the same kernel, devices, network stack, and host process namespace as the stock environment. The boundary supplies Forge-X’s userspace, Python/Moonraker environment, web assets, and service scripts while preserving a stock boot path.

> **Operational boundary:** the chroot has deliberately shared host mounts. A command run inside it can affect shared printer state, including `/opt/config`, `/opt/klipper`, `/data`, `/tmp`, and devices. Treat it as privileged printer administration, not an isolated sandbox.

## How the second environment is assembled

[`.shell/S00init`](../../.shell/S00init) initializes the normal boot path. Its `init_buildroot` function prepares the root at `/data/.mod/.forge-x` and mounts core kernel interfaces into it:

| Chroot path | Source / purpose |
|---|---|
| `/proc`, `/sys`, `/dev`, `/run`, `/tmp` | Host mounts established by `init_chroot`; permit ordinary process/device/runtime interaction. |
| `/data` | Bind mount of the persistent data partition. |
| `/opt/config` | Bind mount of the printer configuration/mod deployment tree. |
| `/opt/klipper` | Bind mount of the stock host Klipper installation; this is why the Klipper overlay targets the stock tree. |
| `/root/printer_data` | A host-built compatibility tree bind-mounted into the chroot. It maps G-codes, logs, configuration, Moonraker database, bundled Python/scripts, and Moonraker configuration into paths expected by the web stack. |

The compatibility tree is important:

```text
/root/printer_data/gcodes    -> /data
/root/printer_data/logs      -> /data/logFiles
/root/printer_data/config    -> /opt/config
/root/printer_data/database  -> /opt/config/mod_data/database
/root/printer_data/bin       -> /opt/config/mod/.bin/exec
/root/printer_data/py        -> /opt/config/mod/.py
/root/printer_data/scripts   -> /opt/config/mod/.shell
/root/printer_data/config/moonraker.conf -> tracked moonraker.conf
```

It also links the vendored Moonraker application into the chroot Python environment and prepares `/root/www` for the UI assets. The real `/opt/klipper` bind mount means Klipper itself remains stock-side software; see [Built-in Klipper patching](klipper-patching.md) for the reversible overlay applied to it before services start.

`S99root` is the stock-side service coordinator. It skips the chroot entirely in `SKIP_MOD` modes. On first run it starts and stops the chroot once to create Moonraker’s database, applies SQL migrations, restores OTA state, then starts the normal service set. Stop/restart operations call the chroot’s own `stop.sh`; the coordinator waits on known PID files before starting again.

## What runs inside it

[`.root/start.sh`](../../.root/start.sh) owns the runtime sequence. It starts services in this order, subject to settings, and [`stop.sh`](../../.root/stop.sh) stops the display services, Moonraker, HTTP server, and time service in the reverse dependency direction.

| Service | Launcher | Role | Condition / notes |
|---|---|---|---|
| NTP | [`.root/S45ntpd`](../../.root/S45ntpd) | BusyBox `ntpd` syncs time with `pool.ntp.org` and runs `fake-hwclock` after sync. | Always started by the runtime script. |
| Moonraker | [`.root/S65moonraker`](../../.root/S65moonraker) | Printer API, WebSocket/API bridge to Klipper, file/history/update management. | Skipped only when `disable_moonraker=1`; startup waits up to roughly 30 seconds for `http://localhost:7125`. |
| Web server | [`.root/S70httpd`](../../.root/S70httpd) | BusyBox `httpd` serves static UI assets from `/root/www` on TCP port 80. | Skipped only when `disable_web=1`. |
| Guppy screen support | [`.root/S35tslib`](../../.root/S35tslib), [`.root/S80guppyscreen`](../../.root/S80guppyscreen) | Touch/input support and the optional Guppy screen process. | Started only when the persisted `display` setting is `GUPPY`. |
| User services | `/etc/init.d/S*` inside the chroot | Extension point for user-installed runtime service scripts. | Started after the core services; stop uses reverse lexical traversal. |

The stock-side boot script separately chooses the display/network path and starts the MCU/Klipper path for non-stock display modes. In stock mode, restart logic normally asks the vendor application to restart Klipper; if that application is unavailable, [`.shell/restart_klipper.sh`](../../.shell/restart_klipper.sh) falls back to Moonraker’s printer-restart API. A `--hard` restart kills `klippy.py` and invokes the stock Klipper start script directly.

## Available commands and tooling

The chroot is a minimal appliance environment, not a general development workstation. Repository evidence establishes these useful interfaces:

- **Shell/session environment:** `S00init` bind-mounts the repository’s Oh My Zsh setup and profile into the stock root for interactive sessions. The tracked profile uses `/bin:/sbin:/usr/bin:/usr/sbin:/opt/bin:/opt/sbin`; it selects Zsh for SSH sessions. This config describes the interactive PATH, not a promise that every conventional Linux tool is installed.
- **Git:** the runtime start script explicitly sets `HOME=/root` so Git uses the chroot user’s `.gitconfig`. Forge-X version/update paths also use the repository at `/root/printer_data/config/mod/` (the bound `/opt/config/mod/`). Thus `git` is an available and intentional maintenance tool in this environment, particularly for Moonraker’s configured Forge-X updater.
- **Python:** Moonraker is executed by `/root/moonraker-env/bin/python3`. The repository’s helper Python sources are exposed at `/root/printer_data/py`, but they are not a general package installation contract.
- **BusyBox/process controls:** the tracked launchers rely on `httpd`, `ntpd`, `start-stop-daemon`, `curl`, `find`, `sed`, and standard shell utilities. Use the shipped scripts rather than assuming Debian/systemd tooling exists.
- **Forge-X controls:** run `/opt/config/mod/.root/S45ntpd`, `S65moonraker`, `S70httpd`, `S35tslib`, or `S80guppyscreen` with `start`, `stop`, or (where implemented) `restart`. The stock-side `/etc/init.d/S99root` coordinates the complete chroot environment. `zmoon.sh` sends selected local Moonraker requests, and `restart_klipper.sh` selects the safe stock/Moonraker restart route.

Do not document an exhaustive binary list from these sources: the actual command set depends on the flashed Buildroot image and optional `/opt` packages. On a printer, verify a command with `command -v <name>` inside the intended environment before making operational dependencies on it.

## Moonraker, Klipper, and Fluidd topology

```text
Browser
  │ HTTP :80 (static files)
  ▼
BusyBox httpd in chroot ── serves /root/www/fluidd and /root/www/mainsail
  │                         UI configuration defaults to Moonraker port 7125
  │
  └──────────────────────────────────────────────────────┐
                                                         ▼
Moonraker in chroot — HTTP/WebSocket API :7125 — Unix socket /tmp/uds — stock Klipper (klippy)
       │                     │
       │                     ├─ file/history/database paths under /root/printer_data
       │                     └─ machine/update scripts under /opt/config/mod/.root
       └─ does not proxy the UI files in this deployment
```

### Moonraker

[`.root/S65moonraker`](../../.root/S65moonraker) launches the vendored application using its virtual-environment Python interpreter and passes `-d /root/printer_data`. Before launch it clears `/root/printer_data/tmp`, sets `TMPDIR` there so temporary files do not consume the RAM-backed `/tmp`, and caps `MALLOC_ARENA_MAX=2` to reduce memory overhead on the constrained T113 platform. Its PID is recorded at `/run/moonraker.pid` inside the chroot.

[`moonraker.conf`](../../moonraker.conf) binds Moonraker to **all interfaces** on `0.0.0.0:7125` and connects it to Klipper at `/tmp/uds`. The socket is shared because `/tmp` is mounted into the chroot from the stock environment. Moonraker’s `simple` machine provider uses scripts under `/opt/config/mod/.root/`; file-manager exclusions protect Forge-X implementation, logs, and database from normal file browsing. The configuration includes the mutable printer-side `mod_data/user.moonraker.conf` last, allowing operator extensions without changing tracked defaults.

The built-in local control bridge, [`.shell/commands/zmoon.sh`](../../.shell/commands/zmoon.sh), POSTs to `localhost:7125` for `printer/restart`, `printer/firmware_restart`, and Forge-X update recovery. This confirms the control flow is API-based rather than invoking a system service manager inside the chroot.

### Fluidd and Mainsail

Fluidd and Mainsail are **static single-page web applications** in `/root/www/fluidd` and `/root/www/mainsail`. `S70httpd` serves that parent directory on port 80; it does not reverse-proxy API calls. Browser JavaScript connects directly to Moonraker on port 7125.

The tracked [`.root/config.json`](../../.root/config.json) supplies UI defaults: a Mainsail theme, Moonraker port `7125`, and Moonraker-backed instance storage. `moonraker.conf` separately registers web updaters for Fluidd and Mainsail. Startup also rewrites specific Mainsail release metadata to use the project’s configured owner/version values, so do not assume the static release metadata is upstream-default at runtime.

### Network and security implications

The default surface is two network listeners: static HTTP on port **80** and unauthenticated Moonraker on port **7125**. `moonraker.conf` explicitly sets `enable_api_key: false`. This is appropriate only for a trusted local network; it is not a hardened remote-management configuration. Any change to network exposure, API authorization, user Moonraker includes, or UI update behavior must be reviewed with [Integrations](../integrations.md), operator network expectations, and the actual deployment topology in mind.

## Safe change and diagnosis path

1. **Determine the boundary first.** Is the behavior stock-side (`.shell/`, stock Klipper) or chroot-side (`.root/`, Moonraker/UI)? Shared mounts can make a path look local to both.
2. **Use the owning lifecycle script.** Change Moonraker in `S65moonraker`, static web delivery in `S70httpd`, and complete environment sequencing in `S99root`/`start.sh`; do not add a competing service manager.
3. **Preserve startup/stop symmetry.** If adding a service, define its PID/lifecycle, decide its correct position relative to Moonraker and the web UI, and ensure `stop.sh`/`S99root` can wait for it when necessary.
4. **Test setting gates and modes.** Check `disable_moonraker`, `disable_web`, `GUPPY`, normal stock mode, and relevant non-stock boot paths. Confirm first-run database initialization still works.
5. **Validate the full browser-to-printer path.** Confirm port 80 serves the expected UI, the UI reaches Moonraker on 7125, Moonraker reaches `/tmp/uds`, and a controlled Klipper operation succeeds. Also test clean stop/restart and reboot persistence.
6. **Protect mutable state.** Do not overwrite `/opt/config/mod_data`, the Moonraker database, or `user.moonraker.conf` merely to test source changes.

## Investigation entry points

- **Mounts and first-run lifecycle:** [`.shell/S00init`](../../.shell/S00init), [`.shell/common.sh`](../../.shell/common.sh), and [`.shell/S99root`](../../.shell/S99root).
- **Runtime services:** [`.root/start.sh`](../../.root/start.sh), [`.root/stop.sh`](../../.root/stop.sh), and the numbered service launchers in [`.root/`](../../.root/).
- **API/UI contract:** [`moonraker.conf`](../../moonraker.conf), [`.root/config.json`](../../.root/config.json), [`.root/S65moonraker`](../../.root/S65moonraker), and [`.root/S70httpd`](../../.root/S70httpd).
- **Klipper control:** [`.shell/restart_klipper.sh`](../../.shell/restart_klipper.sh), [`.shell/commands/zmoon.sh`](../../.shell/commands/zmoon.sh), and [Built-in Klipper patching](klipper-patching.md).
