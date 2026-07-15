# Integrations and external surfaces

## Moonraker and web clients

[`moonraker.conf`](../moonraker.conf) is the integration hub:

- Moonraker listens on `0.0.0.0:7125` and communicates with Klipper over the shared `/tmp/uds`; BusyBox `httpd` independently serves Fluidd/Mainsail static assets on port 80. See [Chroot environment and web runtime](workflows/chroot-and-web-runtime.md) for the complete service and browser/API topology.
- Its file manager excludes mod internals, logs, and database state.
- Update Manager tracks Forge-X as a Git repository and Fluidd, Mainsail, and Guppy through their respective release mechanisms.
- It includes a mutable on-device override: `mod_data/user.moonraker.conf`.

Fluidd and Mainsail are available through the printer HTTP service; the README documents their default paths. Moonraker startup is intentionally optimized for memory: [`.root/S65moonraker`](../.root/S65moonraker) cleans its disk-backed temporary directory and caps allocator arenas.

**Security and change caution:** API keys are disabled in the tracked configuration, and the mod’s macro layer can bridge into root shell commands. Treat network exposure, Moonraker overrides, and G-code upload/control access as a high-trust boundary. Do not broaden exposure casually.

## Camera

[`.shell/S98camera`](../.shell/S98camera) controls optional `mjpg_streamer` on port 8080. It creates a default persistent camera configuration when needed, supports explicit or auto-selected V4L2 devices, applies configured resolution/FPS, and can reduce internal frame-buffer memory. It also attempts configured V4L2 visual controls after start/reload.

Camera is enabled through the persistent `camera` parameter; source comments and [`docs/CAMERA.md`](../docs/CAMERA.md) make two constraints clear:

- do not run stock and mod camera paths simultaneously;
- higher image rate/resolution can consume scarce RAM, so verify memory under expected print load.

## Screen modes

Screen integration spans [`.cfg/`](../.cfg/), [`config/`](../config/), [`macros/`](../macros/), and internal services. Stock mode retains vendor control paths. Feather, Guppy, and headless modes change both UI and print-control assumptions; Guppy runs through [`.root/S80guppyscreen`](../.root/S80guppyscreen), while **Feather is a Forge-X-developed Klipper-plugin/`typer` renderer path**. See [Screen modes and Feather](workflows/screens-and-feather.md) for the canonical mode and implementation guide.

Use [`docs/SCREEN.md`](../docs/SCREEN.md) for operator constraints. In particular, a non-stock screen is network-dependent and should never be switched during a print.

## Telegram and reverse SSH

[`telegram/`](../telegram/) configures Moonraker Telegram Bot on a separate Docker-capable host, not on the printer. The documented deployment can connect directly on LAN or through the printer’s reverse SSH mechanism.

[`.shell/S98zssh`](../.shell/S98zssh) manages Dropbear key/tunnel behavior and can forward remote-host ports back to printer-local Moonraker (7125) and camera (8080). It can also run an optional remote command when the tunnel comes up.

Operational considerations:

- use a dedicated, restricted remote account/key and confirm the SSH server’s forwarding policy;
- reverse forwarding creates a remote access path to printer controls/camera;
- the Telegram installer is intentionally invasive: it is Debian/Ubuntu-oriented, changes container tooling, and uses host networking. Review [`telegram/telegram.sh`](../telegram/telegram.sh) before following or changing it.

## OTA and stock-cloud interaction

Moonraker Update Manager provides Forge-X, Fluidd, Mainsail, and Guppy update entries. Operator instructions are in [`docs/INSTALL.md`](../docs/INSTALL.md); compatibility rationale for stock firmware is in [`docs/FIRMWARE_5x_COMPAT.md`](../docs/FIRMWARE_5x_COMPAT.md).

`block_cloud` is an optional mod parameter processed in [`.shell/S00init`](../.shell/S00init). When enabled, it adds mod-marked loopback host entries for selected vendor cloud/MQTT/model-sharing/OTA/video hosts and removes only its own marked entries when disabled. It is not a firewall or complete air-gap solution, and it can break stock cloud features by design.

## Integration change checklist

1. Keep port/service ownership explicit: Moonraker 7125, mod camera 8080, web UI HTTP service.
2. Test behavior with stock, Feather, Guppy, and headless display modes as appropriate.
3. Verify memory effects on the target hardware, particularly camera and UI changes.
4. Document external prerequisites (remote host, network reachability, SSH policy) without embedding credentials.
5. Test both enable and disable/rollback paths for tunnels, cloud blocking, and optional services.
