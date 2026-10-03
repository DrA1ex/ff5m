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

[`.shell/S98camera`](../.shell/S98camera) owns the Forge-X camera service on port 8080.
The camera implementation is maintained separately as **forge-x-streamer**:
[DrA1ex/forge-x-streamer](https://github.com/DrA1ex/forge-x-streamer).
The installed executable may still retain the historical `mjpg_streamer` filename for compatibility; that filename does not describe the current implementation.

`forge-x-streamer` is a single-process V4L2-to-HTTP MJPEG service designed for the AD5M's 128 MiB host.
It combines capture, bounded frame publication, HTTP serving, camera controls, and recovery in one executable instead of loading the traditional mjpg-streamer plugin stack.
Normal MJPEG/JPEG operation does not link libjpeg. The default capture request is one V4L2 buffer, while publisher/client memory, client count, and worker stacks are bounded.

`S98camera` creates the persistent camera configuration when needed and passes resolution/FPS, memory limits, the controls-file path, and explicit or automatic device selection to the streamer.
`VIDEO=auto` is resolved by forge-x-streamer itself: it scans `/dev/video0..63` and selects a device that exposes usable V4L2 streaming capability.

Camera recovery is owned by the streamer rather than by Klipper or the web UI.
Capture timeouts, disconnects, I/O failures, and device changes close the capture side and enter a bounded reopen/rediscovery loop.
The HTTP server remains alive and existing clients receive a generated **NO SIGNAL** image until a real frame source returns.
Capture buffers are requeued before logging or client socket work, so a slow client cannot retain the camera's capture buffer.

The built-in `/control.htm` page and `/controls` API expose V4L2 image controls supported by the active camera, including brightness, contrast, gain, gamma, hue, saturation, sharpness, power-line frequency, white balance, backlight compensation, and exposure controls.
The active driver's valid ranges and menu values are queried instead of assumed.
Changes can be applied live; **Save** atomically updates the known `camera.conf` entries while preserving unrelated content.
`CAMERA_RELOAD` uses `SIGHUP` to reread saved controls without restarting the HTTP service, and saved controls are reapplied after the first completed frame on every camera open/reconnect.

The separate forge-x-streamer repository contains tests for capture recovery and device rediscovery, JPEG bounds/normalization, HTTP backpressure and routes, control persistence, NO SIGNAL behavior, publisher lifecycle, optional raw-format encoding, and process shutdown.
Actual RAM and CPU use still depends on camera mode, negotiated buffers, resolution, and connected clients, so target measurements should be used for performance claims.

Camera is enabled through the persistent `camera` parameter. Keep these constraints explicit:

- do not run stock and Forge-X camera paths simultaneously;
- higher image rate/resolution can consume scarce RAM, so verify memory under expected print load;
- port 8080 and its write-capable control panel have no separate authentication, so keep them on a trusted network or behind an authenticated proxy/tunnel.

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

Moonraker Update Manager provides Forge-X, Fluidd, Mainsail, and Guppy update entries. Operator instructions and the supported stock-firmware range are in [`docs/INSTALL.md`](../docs/INSTALL.md).

`block_cloud` is an optional mod parameter processed in [`.shell/init-main.sh`](../.shell/init-main.sh). When enabled, it adds mod-marked loopback host entries for selected vendor cloud/MQTT/model-sharing/OTA/video hosts and removes only its own marked entries when disabled. It is not a firewall or complete air-gap solution, and it can break stock cloud features by design.

## Integration change checklist

1. Keep port/service ownership explicit: Moonraker 7125, mod camera 8080, web UI HTTP service.
2. Test behavior with stock, Feather, Guppy, and headless display modes as appropriate.
3. Verify memory effects on the target hardware, particularly camera and UI changes.
4. Document external prerequisites (remote host, network reachability, SSH policy) without embedding credentials.
5. Test both enable and disable/rollback paths for tunnels, cloud blocking, and optional services.
