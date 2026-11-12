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

`forge-x-streamer` is a single-process V4L2-to-HTTP MJPEG service designed for the AD5M's 128 MiB host.
It combines capture, bounded frame publication, HTTP serving, camera controls, and recovery in one executable instead of relying on a runtime plugin stack.
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

Endpoints: `/?action=stream` (MJPEG; `POST /stream` also works), `/?action=snapshot` (JPEG), `/healthz` (`503` while the camera is offline), `/control.htm`, and `/controls` (read, apply, and save image controls). The stream format matches the previous MJPEG streamer, so existing clients keep working.
Defaults are 640×480, 15 FPS, one V4L2 capture buffer, and a five-client limit (`--clients 5` in `S98camera`); requests over the limit get HTTP `429`. Each worker thread has a 128 KiB stack, frame buffers grow only to the largest frame seen, and the control page is a 6 KiB static page.
`REDUCE_MEMORY` maps to `--frame-cap`, which limits the capture buffer for cameras that report a 1080p-sized buffer at every resolution.
The bundled binary encodes YUYV, UYVY, RGB24, RGB565, and framebuffer input with the printer's hardware JPEG backend (G2D/CedarC); camera MJPEG/JPEG frames pass through without re-encoding. Optional software builds use libjpeg ([notice](../LICENSE-LIBJPEG.txt)). A camera that starts in an unsupported format is asked to switch to a supported one when it is reopened, and a failed `CAMERA_RELOAD` keeps the last valid settings.
The streamer builds on other Linux systems with V4L2 and is licensed GPL-2.0-or-later; its CI builds both variants and runs the tests.

The separate forge-x-streamer repository contains tests for capture recovery and device rediscovery, JPEG bounds/normalization, HTTP backpressure and routes, control persistence, NO SIGNAL behavior, publisher lifecycle, optional raw-format encoding, and process shutdown.
Actual RAM and CPU use still depends on camera mode, negotiated buffers, resolution, and connected clients, so target measurements should be used for performance claims.

Camera is enabled through the persistent `camera` parameter. Keep these constraints explicit:

- do not run stock and Forge-X camera paths simultaneously;
- higher image rate/resolution can consume scarce RAM, so verify memory under expected print load;
- port 8080 and its write-capable control panel have no separate authentication, so keep them on a trusted network or behind an authenticated proxy/tunnel.

## Screen streaming and web interface entries

### Ownership

Everything here follows the parameters in `variables.cfg`, and each concern has one owner:

| Concern | Owner |
| --- | --- |
| Inputs | `variables.cfg`: `display`, `camera`, `screen_stream`, `touch_interface`, `screen_stream_fps`, `screen_stream_quality` |
| Screen stream policy | [`.shell/screen_policy.sh`](../.shell/screen_policy.sh), `screen_policy` |
| Applying stream settings | [`.shell/commands/zstreams.sh`](../.shell/commands/zstreams.sh) |
| Touch input policy | [`.shell/commands/ztouch.sh`](../.shell/commands/ztouch.sh) |
| Touch boot and shutdown | [`.shell/S99root`](../.shell/S99root) |
| Processes | `S98camera`, `S98screen`, `S35tslib`: each owns its pid and arguments |
| Existence of the `4gx_camera` / `4gx_screen` entries | SQL migration [`sql/00022-forge-x-webcams.sql`](../sql/00022-forge-x-webcams.sql) |
| `enabled` flag of those entries | [`.py/set_webcams.py`](../.py/set_webcams.py), a Moonraker API client without policy |
| Every other field of the entries | The user, through Fluidd or Mainsail; Forge-X never rewrites it |

`S98screen` and `zstreams.sh` use `screen_policy` to validate `screen_stream` and resolve whether the stream is enabled.
With both preferences on `AUTO`, the display mode determines the defaults.

| Screen | Stream | Managed tslib touch |
| --- | --- | --- |
| Feather / Guppy | On | On, even with stream explicitly off |
| Stock | On | Off; the Stock application owns its native input |
| Headless | Off | Off |

`screen_stream` supports `AUTO/ON/OFF`, `touch_interface` supports `AUTO/ON`. Screen `ON/OFF` override automatic
selection; touch `ON` starts `ts_uinput` on any screen. There is no override that disables input a screen requires,
and neither parameter changes how the stream injects remote touch.
A custom screen runs under Headless and sets `screen_stream=ON` (and `touch_interface=ON` if it consumes the shared
calibrated device); returning to `AUTO` removes these requests.

### Applying

- `ztouch.sh apply` starts or stops the shared touch input according to the settings. `S99root` calls it before
  starting Buildroot services. At runtime, `zchanges.sh` calls it for `touch_interface` and `zdisplay.sh` calls it
  when applying a display change.
- `ztouch.sh start` and `ztouch.sh stop` act regardless of the settings. `S99root` calls `stop` on shutdown and
  restart.
- `zstreams.sh apply` restarts the screen stream and updates the entries. `zchanges.sh` calls it for `screen_stream`,
  and `zdisplay.sh` calls it when applying a display change before restarting Klipper.
- `zstreams.sh webcams` updates the entries. `S99root` runs it with `--wait 10` after the environment starts, because
  `S98screen` and `S98camera` have already started themselves from the same parameters.

Changing `camera` starts or stops `S98camera` in `zchanges.sh`, which also guards against a running stock camera, and
then runs `webcams`. Changing `screen_stream_fps` or `screen_stream_quality` restarts only the stream.

Updating the entries is optional web integration: a failure is printed and never fails the caller. The next change or
boot corrects it, because every run of `set_webcams.py` compares `enabled` with the parameters and writes the entries
that differ. It skips an entry that is missing (the user deleted it) or defined in the Moonraker configuration. A manual toggle of
`enabled` in the web interface is therefore reset by the next run; Forge-X owns that flag.

### Entries

Moonraker keeps webcams created through its API in the database `webcams` namespace, keyed by UUID4. The migration
inserts both entries when no entry of that name exists, hidden (`enabled: false`) until `webcams` shows them, with
the proxied URLs `/webcam/` and `/screen/` (an `iframe` service, 5:3, for the screen). Nothing re-creates an entry the
user deleted. On a first-ever boot `S99root` starts and stops the environment once so that Moonraker creates its
database, then applies the migrations before the normal start, so the entries exist from the first boot. Touch stays
running through this intermediate service restart.

`4gx_camera` stays hidden while `camera=0`, which also covers a Stock screen using the stock camera. The old
`[webcam cam]` section showed that camera too; such a user now adds their own entry.

Older releases shipped a read-only `[webcam cam]` section in `user.moonraker.conf`. Once Moonraker is up,
`.root/start.sh` copies the settings Moonraker parsed from it to `4gx_camera` through the API, leaving `enabled` alone,
and then comments the section out, so the next Moonraker start shows one camera entry instead of two. If the copy fails
or the entry does not exist yet, the section stays and the next boot tries again. This block is marked
`TODO: remove in the next major release`.

### Screen stream process

[`.shell/S98screen`](../.shell/S98screen) runs a separate `forge_x_streamer` in framebuffer mode: 800×480, five
clients (`--clients 5`), port 8081, bound to `127.0.0.1` so that the `/screen/` proxy on port 80 is the only way to reach it. The
proxy has no authentication either: anyone who can reach port 80 can send touch input. `screen_stream_fps` defaults
to 30 and `screen_stream_quality` to 90. On the AD5M framebuffer, the bundled binary encodes the visible page directly
with CedarC. Hardware layouts that need padded rows or dimensions use the G2D conversion path.

The encoder runs only while a stream or snapshot client is connected. The first client starts it and waits for a real
frame. Five seconds after the last client leaves, the encoder and its DMA buffers are freed. At 800×480 the direct
path uses 376 KiB of ION up to quality 90 and 752 KiB above it, where `S98screen` keeps the streamer's default JPEG
limit. The G2D path needs another 564 KiB for its conversion buffer. `/healthz` stays 200 between viewers, and `MEM`
reports ordinary process memory. A still screen repeats its frame every 2 s because
the BusyBox proxy never reads from its client, so only a failed write reveals a closed viewer.

Remote touch is written to the raw `/dev/input/event0`, converted from screen pixels to raw axes with the stock
`/opt/tslib-1.12/etc/pointercal`. It reaches every screen like a physical press: Stock through its own tslib, Feather
and Guppy through `ts_uinput`. The stream therefore depends neither on `touch_interface` nor on when `S35tslib` starts.
The calibration is read when the stream starts; if it is missing or invalid, the streamer logs that and keeps streaming
without remote touch.

`S98camera` and `S98screen` pass `--log-file` with the same `/data/logFiles/streamer.log`, which the debug archive
collects. Lines use the `logged` layout, `date | level | pid | source | message`, with `camera` or `screen` as the
source. The streamer owns the file: at 100 KB it renames it to `streamer.log.YYYY-MM-DD_HH-MM-SS.mmm`, starts a new one
and keeps the five newest renamed files, which the same debug archive pattern collects. A file lock keeps the two
processes from rotating at once or corrupting each other's lines, so `rotate_logs` must not touch this file. A failure
is written once when it starts and once more when it ends, not with every retry. Output of vendor libraries does not reach this file:
`start-stop-daemon -b` sends it to `/dev/null`.

The Buildroot image supplies `P:/webcam/:localhost:8080/` and `P:/screen/:localhost:8081/` in the chroot's
`/etc/httpd.conf`. `.root/start.sh` has a temporary compatibility block that appends only missing routes for older
images; existing routes, including customized backends, are preserved. The camera keeps the `/webcam/` proxy and the
screen uses the same-origin `/screen/` proxy as an iframe entry, so relative image and touch URLs stay under the proxy
prefix without storing a printer IP. Timelapse continues to take snapshots from camera port 8080.

### Touch input

The calibrated input device is published as `/dev/input/fgx_touch` by `S35tslib`; `/dev/input/guppy` is a
compatibility symlink to the same device. Feather and recovery use the primary name. The Guppy wrapper manages
the UI process. `S35tslib` keeps the PID file and device names until the touch process exits.

### Deployment and tests

Remove the old deployed `mjpg_streamer` binary manually. Reboot after installing the update so the new init service
and proxy configuration take effect. Tests are `test_stream_services.py`, `test_stream_startup.py`,
`test_touch_service.py`, `test_set_webcams.py`, and `test_webcam_migration.py` under `tests/`.

## Timelapse

The optional component is copied from the Mainsail Crew
[Moonraker Timelapse](https://github.com/mainsail-crew/moonraker-timelapse)
component and macro (GPLv3). Forge-X adaptations stay in
[`timelapse.py`](../.root/moonraker/components/timelapse.py) and
[`timelapse.cfg`](../macros/timelapse.cfg). The upstream installer is not
used: it assumes apt and systemd, while Forge-X runs Moonraker in a chroot.

`timelapse` in `mod_params.json` owns the persistent on/off decision. Its UI
control is visible only when `camera` is enabled; visibility does not change a
previously saved timelapse value.
The default for a new variables file is off; a persisted on value remains on.
Only the adapted timelapse component uses Moonraker's internal Klippy request
path for its housekeeping commands: setup, frame release, hyperlapse control,
and render completion. They execute without command-history entries. The setup
macro reports the active mode, interval, parking, and final frame setting in
the console; Moonraker does not emit a separate startup response. Other
Moonraker G-code requests retain their normal history.
`timelapse_mode` selects layer, elapsed print time, or virtual SD progress
capture. Progress intervals are floats and default to 0.5%. The interval
parameter is visible only for the selected mode. The `timelapse_park` setting
is off by default; the capture macro reads the current
X/Y maxima from `MOVE_SAFE` for every parked photo, retracts, lifts Z by at most
2 mm before XY travel, then resumes at the saved position and unretracts.
`timelapse_final_frame` is
on by default. On normal `END_PRINT`, both stock and alternative-screen macros
raise Z by the configured `park_dz` within `MOVE_SAFE`'s ceiling and park at
its X/Y maxima before virtual SD finishes. They leave existing end parking unchanged when
the final frame or per-print capture is disabled. Moonraker captures the
final frame after virtual SD reports the end of file and after any pending
frame completes, before frame export or rendering. If axes are unhomed, the
parking move is skipped. A relative Z lift at or above the safe ceiling does
not reverse direction or move the bed.

The common start macro sets the per-print `TIMELAPSE_PRINT` override from the
`START_PRINT` parameter (enabled by default; only the Stock-screen
`START_PRINT` in `config/stock.cfg` reads it, `macros/headless.cfg` does not), starts the time/progress poller
after print preparation, requests an initial frame at the end of `START_PRINT`
in every mode, and cancels the poller on stop. `START_PRINT TIMELAPSE=0`
skips that frame and the rest of the print; disabling with
`TIMELAPSE_PRINT ENABLE=0` after `START_PRINT` skips
only later frames. Time intervals are measured from the initial frame's
scheduled start. The `SET_PRINT_STATS_INFO` wrappers in stock.cfg and
client.cfg forward layer updates only when layer timelapse is enabled; stock
does not include client.cfg. The layer capture macro ignores the first-layer
update (already covered by the start frame) and follows the selected interval
from the next layer onward. Layer mode requires slicer-generated
`SET_PRINT_STATS_INFO` commands; without them, no layer-change frames are
requested.

[`S65moonraker`](../.root/S65moonraker) applies it before Moonraker starts:
`cfg_backup.py` adds the `[timelapse]` section from
[`default/timelapse.moonraker.conf`](../.cfg/default/timelapse.moonraker.conf)
to `mod_data/user.moonraker.conf` when enabled, or removes only that section
when disabled. `S65moonraker` passes these two one-line rules through
`--params-string`; other user sections remain intact. The Klipper
parameter-change macro requests an immediate Moonraker restart when
`idle_timeout` is not `Printing`. During printing it does not restart Moonraker:
the updated Klipper parameter gates capture immediately, while Moonraker's
component availability changes only on a later start.

The component uses the existing camera snapshot endpoint. Capture requests
are skipped while a print is paused. Frames and finished videos live under
`/root/printer_data/gcodes/timelapse/` on the persistent
data partition. Capture has a 1,000-frame limit and requires 128 MiB free
before each frame. A failed capture leaves no numbered partial frame. Render
requires an idle virtual SD and completed, cancelled, or standby print status. Selecting
another file leaves existing frames intact. The start guard waits for finalization
or explicitly requests cancellation before preparation clears the old frames. The FFmpeg command uses one thread and the ultrafast
x264 preset. Normal progress output is disabled so Moonraker does not buffer
FFmpeg's carriage-return status stream during a long render.
Moonraker reports when video generation starts and when it succeeds or fails
in the G-code console for both automatic and manual renders. A rejected or
skipped render does not announce a start.
For headless display configurations with timelapse enabled, `START_PRINT`
checks whether the previous timelapse is still finishing, capturing, exporting
frames, or rendering through a short local Moonraker request. If it is busy, or
Moonraker cannot confirm it is idle, the virtual SD file pauses before print
preparation and shows Wait, Cancel print, and Cancel timelapse actions.
After Wait, a dedicated progress screen keeps all three choices and the
emergency ABORT action available. A delayed G-code check starts print
preparation only when Moonraker confirms the timelapse is idle. The virtual SD
file remains paused through preparation; Cancel print closes the held file and
records cancellation without resuming it. Cancel timelapse starts print
preparation, which interrupts the earlier render. If the initial frame parks the
head, the file resumes only after the snapshot finishes and the head returns.
The capture uses the base pause's `PAUSE_STATE`, saved before retracting or
changing coordinate modes. It restores the head, compensates the capture
retract, and restores the original G-code modes before either resuming SD or
handing the pause to the normal client pause setup. A user pause requested
during capture stays paused; the first-frame screen shows that pause and
enables Resume after restoration, even before `print_started` becomes true.
Rejected Resume attempts keep the file held. Cancel uses the standard forced
SD release followed by cancellation; late capture callbacks cannot move or
resume a stopped print, and completed captures cannot restore twice.
Moonraker owns the previous print's finalization until it finishes or receives
`timelapse_cancel_render` from the start guard. Changing the selected file or
`_START_PRINT.print_active` cannot cancel it. While the next file waits, Moonraker
can finish the previous timelapse. Cancellation invalidates old results before
awaiting cleanup; the new print starts a separate frame sequence. Klipper's reactor remains responsive during the short check.
[`timelapse_ffmpeg.sh`](../.root/timelapse_ffmpeg.sh) runs it at
nice level 19. A render waits while the printer regression's
`/tmp/feather-ui-test-timelapse-hold` flag exists (at most 10 minutes), so the
regression can test starting a print during a real render; normally the flag
is absent and rendering starts immediately. [`S99root`](../.shell/S99root) binds the stock FFmpeg,
x264, loader and library directories into separate paths in the Moonraker
chroot. The wrapper invokes the stock ARM loader with an explicit library path;
the chroot's own libraries remain unchanged. The bind mounts are prepared on
every Forge-X start, independent of the timelapse parameter.

Optional frame ZIP export is disabled by default. A manual export starts only
when printing has stopped and sufficient disk space remains; ZIP writing runs
in a worker thread and the completed archive replaces the temporary file
atomically. A new print stops the export after the current frame. If automatic
ZIP export and rendering are both enabled, they run sequentially.

On the AD5M tested on 2026-09-25, the stock FFmpeg executable at
`/opt/ffmpeg-4.0.2/bin/ffmpeg` could encode H.264 with its bundled
libraries. Its own loader was necessary inside the Moonraker chroot. A
two-frame synthetic encode completed both on the stock shell and inside the
Moonraker chroot in standby with zero heater targets and inactive virtual SD.
The ELF flags identify stock FFmpeg and `/lib/ld-linux.so.3` as ARM soft-float
(`0x200`), while chroot Python and its loader are hard-float (`0x400`). The
kernel can run either as a separate process; the wrapper keeps the soft-float
loader and libraries together for FFmpeg.
This does not measure memory use for a full video; the first real print needs
resource observation before relying on automatic rendering.

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

Moonraker Update Manager provides Forge-X, Fluidd, Mainsail, and Guppy update entries. Operator instructions are in [`docs/INSTALL.md`](../docs/INSTALL.md); the supported stock-firmware range is in [`docs/COMPATIBILITY.md`](../docs/COMPATIBILITY.md).

`block_cloud` is an optional mod parameter processed in [`.shell/init-main.sh`](../.shell/init-main.sh). When enabled, it adds mod-marked loopback host entries for selected vendor cloud/MQTT/model-sharing/OTA/video hosts and removes only its own marked entries when disabled. It is not a firewall or complete air-gap solution, and it can break stock cloud features by design.

## Integration change checklist

1. Keep port/service ownership explicit: Moonraker 7125, mod camera 8080, screen stream 8081 (loopback only), web UI HTTP service on port 80.
2. Test behavior with stock, Feather, Guppy, and headless display modes as appropriate.
3. Verify memory effects on the target hardware, particularly camera and UI changes.
4. Document external prerequisites (remote host, network reachability, SSH policy) without embedding credentials.
5. Test both enable and disable/rollback paths for tunnels, cloud blocking, and optional services.
