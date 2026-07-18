# Feather runtime, Typer, and Klipper plugin wiring

This is the low-level runtime reference for Feather. For display selection, operator behavior, and recovery, see [Screen modes and first-party Feather](screens-and-feather.md).

## Ownership model

Feather is not an init service and neither is Typer. The long-lived owner is the Klippy process:

```text
display=FEATHER
  -> printer.cfg includes config/feather.cfg
  -> [feather_screen] loads the feather_screen Klipper extra
  -> config load starts /root/printer_data/bin/typer
  -> Feather shows Klipper startup/recovery state
  -> Typer renders /dev/fb0 and transports touch events
```

`feather_screen.py` owns UI state, page transitions, safety validation, and printer actions. `typer` only renders display-list commands and maps named hitboxes to opaque touch-event strings. It does not execute G-code, control the MCU, or launch shell commands.

`ts_uinput` is the only separate supporting service. The init-style [`S35tslib`](../../.root/S35tslib) helper starts it, calibrates/translates physical touch input, and maintains `/dev/input/guppy`, the stable device Typer reads.

## How the plugin is installed and loaded

During initialization, [`.shell/S00init`](../../.shell/S00init) runs `apply_klipper_patches()`, linking all files from [`.py/klipper/plugins/`](../../.py/klipper/plugins/) into `/opt/klipper/klippy/extras/`. Feather is therefore a standard Klipper extra, not a copied module or a separate Python service.

[`.shell/commands/zdisplay.sh`](../../.shell/commands/zdisplay.sh) activates [`.cfg/init.display.feather.cfg`](../../.cfg/init.display.feather.cfg), which adds `config/feather.cfg` to `/opt/config/printer.cfg` and removes competing display roots. `config/feather.cfg` declares `[feather_screen]`; Klipper calls `load_config(config)` in `feather_screen.py`.

The plugin registers `klippy:ready`, `klippy:shutdown`, and `klippy:disconnect`. It also registers `FEATHER_PRINT_STATUS`, an internal Forge-X macro-to-screen status bridge. Its optional config values are read at Klipper config-load time; defaults deliberately live in Python for upgrade compatibility until Klipper restarts.

## Boot and restart chain

On a non-Stock boot, [`.shell/boot/boot.sh`](../../.shell/boot/boot.sh) starts network setup in the background, then for Feather starts `S35tslib` before booting the MCU and launching [`.shell/commands/zstart_klipper.sh`](../../.shell/commands/zstart_klipper.sh). The latter executes `/opt/klipper/start.sh`, optionally under `chrt -r 5`.

A switch to `display=FEATHER` reaches the same state through `zdisplay.sh feather`. Its `apply_display_off()` function stops `ffstartup-arm`, `firmwareExe`, and Guppy, starts `S35tslib`, draws the splash, reloads `S00init`, and runs `restart_klipper.sh --hard`. The hard restart terminates `klippy.py` and launches Klipper directly again.

`S35tslib` starts `/usr/bin/ts_uinput` with tslib variables, creates `/var/run/ts_uinput.pid`, discovers the generated event node under `/sys/class/input`, and symlinks it as `/dev/input/guppy`. Repeated `start` calls are idempotent while that PID is alive.

## Klippy and Typer lifecycle

When Klipper constructs `[feather_screen]` during config load, Feather starts `FeatherRenderer` immediately rather than leaving the previous framebuffer content visible until `klippy:ready`. The renderer executes Typer as a Klippy child:

```sh
/root/printer_data/bin/typer \
  --double-buffered \
  --touch-device /dev/input/guppy \
  --event-pipe /tmp/feather-events \
  batch --pipe /tmp/typer
```

The event FIFO is registered with Klipper's reactor. Before `klippy:ready`, a short reactor timer redraws a modal startup panel with a pulsating circle and an explicit `KLIPPER IS LOADING` message. This makes the not-yet-ready state visible and keeps the touch transport alive without allowing printer actions.

At `klippy:ready`, `FeatherScreen._init()` resolves Klipper objects (toolhead, heaters, virtual SD, print statistics, pause state, display status, optional resurrection, and others), stops the startup animation, creates the normal timers, and renders an initial recovery or home page. The existing periodic timer then owns state refresh. If Typer dies, the timer rate-limits restart attempts to once per five seconds.

Pipe-mode Typer configures Linux `PR_SET_PDEATHSIG=SIGTERM`, so it exits if the Klippy process actually dies. A Klippy shutdown or disconnect event does not necessarily end that process, so Feather stops operational timers, network work, and joystick motion but deliberately keeps Typer and its event FIFO available for recovery UI. It classifies common MCU communication, scheduling, and shutdown messages, displays an explicit error panel, and offers `FIRMWARE_RESTART` when that is the appropriate recovery command. A config error detected before `klippy:ready` offers `RESTART`; a plain disconnect is displayed as a waiting state instead of looking like an unresponsive last page. If the process exits or Feather performs normal renderer cleanup, `FeatherRenderer.stop()` allows two seconds before escalating to `kill()`.

## Runtime files and devices

| Path | Creator / type | Purpose and lifetime |
|---|---|---|
| `/dev/fb0` | kernel framebuffer | Typer's render target. `--double-buffered` uses the second framebuffer page as scratch storage when possible, otherwise a heap buffer; `flush` copies a dirty rectangle and does not page-flip. |
| `/dev/input/guppy` | `S35tslib` symlink | Calibrated 800×480 touch device. Removed by `S35tslib stop`. |
| `/var/run/ts_uinput.pid` | `S35tslib` | PID file for the support process, not for Feather or Typer. |
| `/tmp/typer` | FIFO | Klippy writes complete display-list frames; Typer reads them. Typer unlinks it on normal exit. |
| `/tmp/feather-events` | FIFO | Typer writes logical touch events; Klipper's reactor reads them. Typer unlinks it on normal exit. |
| `/tmp/feather-wifi-<pid>-<sequence>` | temporary file | Private Wi-Fi input from the plugin to `znetwork.sh`; retired after the operation. |
| `/tmp/feather-wifi-*`, `/tmp/feather-wpa-*.conf`, `/tmp/feather-udhcpc-*.log`, `/tmp/feather-resolv.*` | temporary network-helper files | Scan/connect/DHCP artifacts managed by `znetwork.sh` and the network helpers, not by Typer. |

`/tmp/typer` is a FIFO, not a regular command file or a service socket. Do not replace it, remove it under a live renderer, or add a concurrent writer.

## FIFO protocol and concurrency

Before a start, `FeatherRenderer` kills any existing `typer`, waits for exit, unlinks both FIFO paths, recreates them with mode `0666`, opens the event FIFO read/write non-blocking, spawns Typer, and opens the draw FIFO read/write non-blocking. The ordering prevents the Python and C++ sides opening different FIFO inodes under the same paths.

Each draw frame is a newline-delimited batch protocol ending in `--end`, for example:

```text
--batch clear-hitboxes
--batch fill -p 0 0 -s 800 442 -c 030607
--batch hitbox --id 18:print.pause -p 20 315 -s 175 100
--batch flush
--end
```

Typer buffers data through `--end`, tokenizes it as an argument protocol rather than a shell command, and processes its `--batch` operations in sequence. `flush` makes accumulated changes visible.

Feather keeps frames below 3,584 bytes (`PIPE_BUF`) and caps queued draw data at 256 KiB. A full pipe schedules a reactor retry rather than blocking Klippy. Queue overflow clears pending drawing and terminates Typer so the lifecycle timer can restore a known-good renderer. Typer likewise rejects frames larger than 256 KiB and locks the draw FIFO to reject a competing Typer daemon.

## Touch transport

Every page starts with `clear-hitboxes`. Feather prefixes each action ID with a monotonically increasing page generation, for example `18:print.pause`; late events from a replaced page are discarded by the plugin.

Typer polls the draw FIFO and `/dev/input/guppy` together. A normal tap generates `tap 18:print.pause`. A continuous hitbox generates records such as:

```text
touch 18:move.joy.xy begin 400 210
touch 18:move.joy.xy move 410 213
touch 18:move.joy.xy end 410 213
```

For continuous input, Typer emits a `move` heartbeat every 100 ms while a finger is stationary and emits a final `end` when the touch fd fails. If input/event fds disappear, it retries opening them in its poll loop.

`FeatherScreen._process_touch_events()` handles partial FIFO reads, validates generation and format, wakes a dimmed panel on the first touch, debounces actions, and applies page/state gates. Continuous motion is further limited to the Move page, idle state, correct homing, and active joystick mode; actual motion remains inside Klipper's planner/toolhead path.

Startup and error pages clear the normal page hitboxes. The only actionable shutdown control is the generation-tagged `FIRMWARE_RESTART` button that Feather exposes after classifying an MCU recovery condition; it still routes through Klipper's normal G-code command path.

## Component boundaries

| Component | Owns | Does not own |
|---|---|---|
| `feather_screen.py` | Klipper lifecycle, UI state machine, safety gates, reactor timers/fds, G-code/macro dispatch, shared status/error handling | Page-specific rendering or a separate motion process |
| `feather_screen_pages.py` | Dashboard, files, print status, settings, themes, mod parameters, bounded network helpers, and recovery pages | Klipper lifecycle, motion planning, and direct display access |
| `feather_screen_controls.py` | Move, heat, filament, live Z adjustment, screws, and mesh workflows | Network child processes and renderer lifecycle |
| `feather_z_calibration.py` | Idle Z-calibration state, formula, zone aggregation, pressure hysteresis, pages, motion, and exact mesh/runtime restoration | Live-print Z adjustment or unrestricted G-code |
| `feather_ui.py` | Layout primitives, frame construction, FIFO and Typer child lifecycle, generation-tagged hitboxes | Klipper state decisions and printer commands |
| `feather_joystick.py` | Touch normalization, ramps/braking, bounded motion queue planning | Rendering or direct touch-fd I/O |
| `feather_mod_settings.py` | Mod-settings editor helpers | Persistence side effects |
| `typer` | Framebuffer, batch parsing, hitboxes, touch-to-event transport | Klipper, Moonraker, G-code, or printer policy |
| `S35tslib` / `ts_uinput` | Calibrated input device | Feather page/UI behavior |

`mod_params`, `resurrection`, and patched G-code logic are related Klipper-resident plugins/patches installed by the same overlay. They are not child services started by Feather.

The screen class uses two bounded behavior mixins rather than forwarding proxy
objects. This keeps the existing `FeatherScreen` action API and one state owner
while limiting each production Python file to fewer than 1,500 lines. Do not
reintroduce copies of controller state into the page modules.

## Memory budget and measurement

Measure resident processes from `/proc/<pid>/smaps`, not by adding raw RSS
values. RSS counts clean libc/libstdc++ pages in every process that maps them;
PSS divides those shared pages between their users, while `Private_Clean` plus
`Private_Dirty` describes the process-only cost.

A representative idle measurement on the 128 MiB target after the component
split and size-oriented Typer build was:

| Measurement | Before | After |
|---|---:|---:|
| Complete Klippy PSS | 19,812 KiB | 19,773 KiB |
| Typer PSS | 1,895 KiB | 1,800 KiB |
| Typer private memory | 1,684 KiB | 1,600 KiB |
| Typer `/proc/status` RSS | 2,432 KiB | 2,284 KiB |
| Deployed Typer binary | 1,089,196 bytes | 984,948 bytes |

The raw RSS line remains above 2 MiB because it includes reclaimable/shared
libc and libstdc++ text. Typer's attributed PSS and private working set are both
below the 2 MiB budget. Its heap is about 104 KiB; the framebuffer-backed second
page is a device mapping and does not allocate a 1.5 MiB heap backbuffer.

The complete repository Feather Python source set is about 262 KiB
(268,243 bytes), below the 500 KiB source budget. Splitting it adds a few module
headers but does not duplicate controller state. The file browser uses compact
slot-backed entries so a directory with many G-code files does not retain one
Python dictionary per row.

Moving page policy or printer actions into Typer is not currently justified:
Typer's C++ runtime/shared-library footprint is already larger than its heap,
while the measured Klippy PSS did not grow after the Python split. Such a move
would also add a second state owner and a wider IPC protocol. Revisit it only
with a before/after PSS profile demonstrating a net process-total reduction.

## Engineering and diagnosis

- Keep one interactive Typer owner of `/dev/fb0`; manual `typer -db` invocations can overwrite the UI or force heap fallback.
- Never add blocking I/O to Feather reactor callbacks. Use non-blocking FIFO retries, timers, or bounded child helpers.
- New UI actions require a hitbox plus page/state validation in the Klipper plugin; do not treat Typer events as trusted printer commands.
- Preserve padding through the shared hint/dialog primitives. Dynamic hint widths include their horizontal inset, and dialog lines are clipped to the padded content area.
- For an unresponsive screen, check: active Feather include, `klippy.py`, Typer child, `/dev/input/guppy`, FIFO types (`test -p /tmp/typer`; `test -p /tmp/feather-events`), then `[feather_screen]` messages in the Klipper log.

Primary implementation references: [`feather_screen.py`](../../.py/klipper/plugins/feather_screen.py), [`feather_ui.py`](../../.py/klipper/plugins/feather_ui.py), [`typer/main.cpp`](../../.bin/src/typer/main.cpp), [`typer/interactive.cpp`](../../.bin/src/typer/interactive.cpp), and [`S35tslib`](../../.root/S35tslib). For Typer's command interface, see [`docs/TYPER.md`](../../docs/TYPER.md).
