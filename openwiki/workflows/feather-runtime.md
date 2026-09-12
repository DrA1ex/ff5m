# Feather runtime, Typer, and Klipper plugin wiring

This is the low-level runtime reference for Feather. For display selection, operator behavior, and recovery, see [Screen modes and first-party Feather](screens-and-feather.md).

## Framework dependency and updates

The UI framework is vendored at `.py/klipper/plugins/ui` from the
`feather-ui-designer` repository (`framework-v2.0.0`). It must remain a real
repository subtree: do not replace it with a symlink, pip package, or Designer
checkout on the printer.

```sh
git subtree add --prefix .py/klipper/plugins/ui <feather-ui-designer-repository> framework-v2.0.0 --squash
git subtree pull --prefix .py/klipper/plugins/ui <feather-ui-designer-repository> framework-v2.x.y --squash
```

Product pages/controllers belong in `.py/klipper/plugins/ff5m_ui`, not in the
framework subtree. The Typer transport is framework-owned in `ui/renderer.py`
and `ui/render_worker.py`; after a subtree update, explicitly preserve or
reconcile the matching renderer-worker implementation.

## Typer command reference

`/root/printer_data/bin/typer` draws to the 800×480 framebuffer. Its normal
commands are `text`, `fill`, `stroke`, `line`, `clear`, `flush`, and `batch`.
Use `--list-fonts` to inspect installed fonts and `--font-manifest` to emit the
machine-readable metrics used by Feather. `--double-buffered` requires an
explicit `flush`.

`text` accepts a position, string, color, font, scale and alignment; it also
supports width/height limits, wrapping and ellipsis truncation. The shape
commands accept their expected positions, sizes, colors, and (where relevant)
line widths. Exact syntax is intentionally supplied by the deployed binary:

```sh
/root/printer_data/bin/typer --help
/root/printer_data/bin/typer <command> --help
```

`batch` accepts repeated `--batch <command> ...` entries or reads frames from a
named `--pipe`. End each pipe frame with `--end`; only one owner may write the
pipe. In double-buffered mode the renderer uses the non-visible framebuffer
page where possible and falls back safely to a heap buffer.

Interactive mode uses `--touch-device /dev/input/guppy` and
`--event-pipe /tmp/feather-events`. A `hitbox` maps a rectangle to an opaque,
restricted action ID; `clear-hitboxes` replaces the previous page's regions.
Regular hitboxes emit `tap <id>`. A `--continuous` hitbox emits
`touch <id> begin|move|end <x> <y>` plus stationary heartbeats. Typer never
interprets these IDs or executes printer actions: the Klipper plugin must
revalidate state and own all motion/safety policy.

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

`feather_safety.py` composes named Klipper activity providers, bounded reference-counted operation leases, and armed-page reasons. Active printing, explicitly owned long-running G-code, motion, heating, temperature waits, joystick motion, and loaded-feature activity expose `global.abort` on every live page except Home. Direct heat and material controls expose it before an operation begins; movement controls do so only after at least one usable axis has been homed. Short bookkeeping G-code never toggles the emergency action, which prevents transient header redraws. Provider failures are fail-safe and cannot silently remove the M112 path; the renderer only receives the final visibility boolean.

`_run_blocking_gcode()` owns a controller-level interaction lock for homing, probing, positioning, filament moves, Live Z saves, and similar loader operations. The loader is a new renderer generation, clears the entire page header and all previous hitboxes, and exposes only the global emergency action when safety policy requires it. The controller rechecks the lock both when a touch arrives and after delayed button feedback, so a queued Back event cannot escape the workflow underneath the loader. Calibration and recovery progress pages have no Back action and retain the command-depth gate for their long dispatcher-owned macros.

Entering the idle Move or Heat page cancels the pending motor-stop, automatic-reboot, and SSH keepalive delayed G-code timers. This fixed timer-only macro uses Feather's immediate command path, so page navigation never waits for the normal G-code mutex even when another command owns it.

The Heat page binds its part-fan status directly to `fan_generic fanM106` and
uses `SET_FAN_SPEED FAN=fanM106`; it must not infer availability from a generic
`fan` object that this printer does not expose. Filament load, unload, and purge
hitboxes remain disabled until the nozzle is at least the configured extrusion
minimum and within 2 °C of its active target. The action handler repeats the
same predicate so a stale touch event cannot bypass the visual lock.

`ts_uinput` is the only separate supporting service. The init-style [`S35tslib`](../../.root/S35tslib) helper starts it, calibrates/translates physical touch input, and maintains `/dev/input/guppy`, the stable device Typer reads.

## How the plugin is installed and loaded

During initialization, [`.shell/S00init`](../../.shell/S00init) runs `apply_klipper_patches()`, linking all files from [`.py/klipper/plugins/`](../../.py/klipper/plugins/) into `/opt/klipper/klippy/extras/`. Feather is therefore a standard Klipper extra, not a copied module or a separate Python service.

[`.shell/commands/zdisplay.sh`](../../.shell/commands/zdisplay.sh) activates [`.cfg/init.display.feather.cfg`](../../.cfg/init.display.feather.cfg), which adds `config/feather.cfg` to `/opt/config/printer.cfg` and removes competing display roots. `config/feather.cfg` declares `[feather_screen]`; Klipper calls `load_config(config)` in `feather_screen.py`.

The plugin registers `klippy:ready`, `klippy:shutdown`, and `klippy:disconnect`. Managed workflow status is read directly from the `operation_context` snapshot during the normal display update cycle. Optional config values are read at Klipper config-load time; defaults deliberately live in Python for upgrade compatibility until Klipper restarts.

`feather_update_notification.py` owns the process-local update-notification and active-installation UI state. Five seconds after `klippy:ready`, and only while authoritative print state is idle, it sends a fire-and-forget Klipper remote-method call to Moonraker. The optional `[feather_updates]` Moonraker component returns a bounded projection of the already-cached status selected by its `update_key` option through `feather/update_status`; it uses Moonraker's `internal_transport` API for both status and installation, so it neither refreshes Git for a notification nor changes Moonraker's update-manager implementation. Missing or malformed responses fail closed and schedule the existing bounded retry without allowing an optional WebRequest endpoint to shut down Klipper. Only the idle Home page may present the modal. Starting or pausing a print removes it without setting the version-specific in-memory dismissal. The Update action repeats Feather's idle check, and the adapter revalidates idle state and the expected version before delegating the same configured key to the canonical `machine.update.client` OTA path. While that request is active, the adapter forwards only matching `update_manager:update_response` messages to Feather; the UI displays them in its existing non-interactive loader, reports a structured refusal or failure, and treats the managed-service restart as the expected terminal stage.

## Boot and restart chain

On a non-Stock boot, [`.shell/boot/boot.sh`](../../.shell/boot/boot.sh) starts `netd --adopt-existing` when the mod-owned `network.conf` already exists. The daemon preserves an already-working connection only when its transport, exact Wi-Fi SSID where applicable, address, and DHCP client all match that configuration. It takes ownership of the matching processes and removes the other transport without rewriting network configuration. If the live state cannot be accepted completely, `netd` performs a fresh cleanup and starts the saved target normally. An older installation without `network.conf` starts netd without the adoption flag so the existing one-shot bootstrap can create the mod target first. Feather then boots the MCU and launches [`.shell/commands/zstart_klipper.sh`](../../.shell/commands/zstart_klipper.sh) without waiting for connectivity. The later `S99root` stage invokes [`.root/start.sh`](../../.root/start.sh), which owns `S35tslib` startup for Feather and Guppy as part of the restartable Buildroot service lifecycle. Typer tolerates that startup order by retrying a missing touch device until it appears. Guppy and Headless instead invoke `netd-cli wait --timeout 180` before continuing, preserving their Stock fallback behavior. The three-minute limit belongs to boot only; the daemon has no retry quota for its desired network. The Klipper launcher executes `/opt/klipper/start.sh`, optionally under `chrt -r 5`.

A controlled `netd` shutdown stops its started or adopted supplicant and DHCP clients. Startup has three explicit contracts. `--migrate-existing` is the one-shot live Stock → Feather path: it determines the working vendor transport and may copy that selection and Wi-Fi profile into mod storage. `--adopt-existing` never migrates configuration: it accepts only the live connection matching the existing mod target and removes the other transport. With neither flag, or when requested adoption cannot be completed, startup stops existing network processes, clears both interfaces, and establishes the saved target afresh.

A switch to `display=FEATHER` reaches the same state through `zdisplay.sh feather`. Its `apply_display_off()` function stops `ffstartup-arm`, `firmwareExe`, and Guppy, starts `S35tslib`, draws the splash, reloads `S00init`, and runs `restart_klipper.sh --hard`. The hard restart terminates `klippy.py` and launches Klipper directly again.

`S35tslib` starts `/usr/bin/ts_uinput` with tslib variables, creates `/var/run/ts_uinput.pid`, discovers the generated event node under `/sys/class/input`, and symlinks it as `/dev/input/guppy`. Repeated `start` calls are idempotent while that PID is alive.

## Klippy and Typer lifecycle

When Klipper constructs `[feather_screen]`, it builds the frontend first and
defers `TyperRenderWorker` startup to a reactor callback. The daemon worker then
executes Typer as a Klippy child; the reactor never launches or waits for the
process:

```sh
/root/printer_data/bin/typer \
  --deferred-page-publish auto \
  --present-guard-us 3000 \
  --double-buffered \
  --touch-device /dev/input/guppy \
  --event-pipe /tmp/feather-events \
  batch --pipe /tmp/typer
```

Feather enables vsync-driven deferred page publication by default. The
3 ms guard is based on the measured printer timing margin; `auto` preserves the
synchronous fallback when page flipping or a usable vsync source is absent.
Typer enters its resident FIFO loop before vsync calibration completes and
publishes frames synchronously during calibration. Once the timing model has
enough valid samples, later frames switch to deferred publication without a
renderer restart or an unpublished startup frame.

The worker hands the event FIFO to Klipper's reactor through
`register_async_callback`; touch remains a direct reactor FD for low-latency
emergency and joystick input. Before `klippy:ready`, a short reactor timer
publishes a modal startup panel and keyed pulse frames without allowing printer
actions.

At `klippy:ready`, `FeatherScreen._init()` resolves Klipper objects (toolhead,
heaters, virtual SD, print statistics, pause state, display status, optional
resurrection, and others), stops the startup animation, creates the normal
timers, and publishes an initial recovery or home page. If Typer exits or its
draw FIFO stalls, the worker performs TERM/KILL, bounded backoff, FIFO handoff,
restart, and requests a current-surface redraw through the thread-safe reactor
callback. The periodic UI timer does not own renderer recovery.

Pipe-mode Typer configures Linux `PR_SET_PDEATHSIG=SIGTERM`, so it exits if the
Klippy process actually dies. A Klippy shutdown or disconnect event does not
necessarily end that process, so Feather stops operational producers, submits
one final critical error batch, freezes the frontend, and deliberately leaves
the worker alive to deliver that screen and recovery hitbox. Process waiting
and the two-second TERM/KILL escalation occur only in the worker.

## Runtime files and devices

| Path | Creator / type | Purpose and lifetime |
|---|---|---|
| `/dev/fb0` | kernel framebuffer | Typer's render target. `--double-buffered` uses the second framebuffer page as scratch storage when possible, otherwise a heap buffer; `flush` copies a dirty rectangle and does not page-flip. |
| `/dev/input/guppy` | `S35tslib` symlink | Calibrated 800×480 touch device. Removed by `S35tslib stop`. |
| `/var/run/ts_uinput.pid` | `S35tslib` | PID file for the support process, not for Feather or Typer. |
| `/tmp/typer` | FIFO | Klippy writes complete display-list frames; Typer reads them. Typer unlinks it on normal exit. |
| `/tmp/feather-events` | FIFO | Typer writes logical touch events; Klipper's reactor reads them. Typer unlinks it on normal exit. |
| `/run/netd.sock` | `netd` stream socket, mode `0660` | The only network control channel. Carries `GET`, `SUBSCRIBE`, `SCAN`, `CONNECT_WIFI`, `USE_ETHERNET`, and explicit `CANCEL` for Feather and the thin CLI. EOF only removes that client; it never cancels a daemon-owned operation. `.shell/common.sh` bind-mounts `/run`, so the path is the same inside and outside the chroot. |
| `/data/logFiles/netd.log` | `netd` daemon log | Event-oriented startup, adoption/migration decision, process lifecycle, connection-state and user network-action log. It is opened directly by `netd` so BusyBox daemonization cannot discard it and rotated by `S00init` at boot. Lines use the same timestamp/level/PID/process/message format as `logged`. User actions include the selected transport and SSID, but never credentials or scan-result contents. `zbackup.sh --tar-debug` includes this file and its rotated copies. |
| `/opt/config/mod_data/network.conf` | `netd` | Persistent desired transport and selected SSID. Vendor files are consulted only during one-shot bootstrap or `--migrate-existing`; `--adopt-existing` never changes this file. |
| `/opt/config/mod_data/wpa_supplicant.conf` | `netd` | Mod-owned saved Wi-Fi definitions. New credentials are persisted only after association and DHCP succeed. |
| `/tmp/net_ip` | `netd` | The published address. Written on a state transition only — never from a read, which is what made a 1 Hz `status` poll a mutation. |
| `/tmp/wifi_connected_f` | `netd` compatibility marker | Tells the stock shell status bar whether to draw the Wi-Fi icon as connected. Ethernet state has no marker; Feather and other current consumers use the daemon snapshot. |
| `/data/USB` | Feather-owned mount or bind mount | Exposes one supported USB filesystem to Feather, Fluidd, and Klipper. It exists only while supported USB storage is attached. |
| `/tmp/forge-x-usb-operation` | atomic lock directory with owner PID | Serializes Feather attach, destructive `PREPARE_USB`, and USB-swap initialization. Removed by the owning helper's exit trap; a later operation can reclaim it if that PID no longer exists. |

`/tmp/typer` is a FIFO, not a regular command file or a service socket. Do not replace it, remove it under a live renderer, or add a concurrent writer.

## USB file browsing

While the printer is idle, Feather subscribes a non-blocking `NETLINK_KOBJECT_UEVENT` socket to the Klipper reactor and filters kernel `add`, `remove`, `change`, and `move` events to the USB block subsystem. It does not add an init service, udev rule, watcher thread, persistent process, or periodic sysfs scan. Event bursts are coalesced for 400 ms before [`.shell/commands/zusb_mount.sh`](../../.shell/commands/zusb_mount.sh) starts as a non-blocking child; the reactor only polls process completion. The helper is terminated after a bounded timeout, and failed or lock-contended attaches use bounded backoff.

Feather closes the uevent socket and stops an in-flight reconciliation helper in `PREPARING`, `PRINTING`, and `PAUSED`. It deliberately performs no USB discovery in those states and leaves an existing mount in place for an active USB print. Returning to `IDLE` recreates the subscription and forces one complete helper reconciliation, so a device event missed while printing is recovered without background polling.

The helper reuses a supported existing mount through a bind mount when Stock, USB swap, or preparation already mounted that partition. Otherwise it mounts the largest supported candidate read/write directly at `/data/USB`. Because Forge-X binds `/data` into its chroot before Feather starts, the helper also mirrors that same mount at the chroot's `/data/USB`; this keeps Klipper and Moonraker on the same filesystem while preserving one logical path. Detach removes the mirror first and then unmounts only the Feather target, never a reused source mount. If an active swap file lives below that target, detach retains both mounts. Inserting a second drive does not replace the currently attached drive; removing the attached drive allows the next supported candidate to take its place.

The virtual-SD placement lets Klipper, Moonraker, and Fluidd address removable files as `USB/<path>`. `feather_files.py` excludes `USB` from the internal scan, then scans it separately into the same compact flat entries and recency order. History keys include the `USB/` prefix, so identically named internal and removable files do not collide. Traversal remains limited to two levels, and removal during a directory scan produces an empty USB page instead of stopping Feather's periodic reactor callback.

Formatting, swap initialization, and browser attach share the atomic `/tmp/forge-x-usb-operation` lock. Lock acquisition occurs before any swap mount cleanup or formatter erase. Browser attach reports `BUSY` and retries; destructive preparation reports a user-visible failure instead of racing another owner.

## FIFO protocol and concurrency

Before a start, `TyperRenderWorker` terminates any owned or orphaned `typer` and
waits for exit off-reactor. On restart it first posts an unregister request for
the old event FD and waits for reactor acknowledgement; only then does it close
the descriptor, unlink both FIFO paths, recreate them with mode `0666`, spawn
Typer, and hand the new event FD back to the reactor. This prevents the Python
and C++ sides opening different FIFO inodes and prevents a stale registered FD.

Each draw frame is a newline-delimited batch protocol ending in `--end`, for example:

```text
--batch clear-hitboxes
--batch fill -p 0 0 -s 800 442 -c 030607
--batch hitbox --id 18:print.pause -p 20 315 -s 175 100
--batch flush
--end
```

Typer buffers data through `--end`, tokenizes it as an argument protocol rather than a shell command, and processes its `--batch` operations in sequence. `flush` makes accumulated changes visible.

`FeatherRenderer.send()` converts commands to an immutable batch and performs
only a non-blocking publication to a queue capped at 16 batches and 64 KiB of
characters per batch. Complete surfaces supersede older generations, keyed
animation is latest-wins, and critical restart/error/shutdown batches evict
untouched ordinary work. The worker encodes frames below 3,584 bytes
(`PIPE_BUF`) and blocks in `poll(POLLOUT)` when the FIFO is full; no retry loop
is scheduled on the reactor. Typer also locks the draw FIFO to reject a
competing daemon.

Raster acceleration is an explicit runtime choice. Typer defaults to
`--raster-acceleration scalar`; `[feather_screen]` accepts
`raster_acceleration: scalar|neon` and only adds the Typer option for `neon`.
The ARM backend checks the kernel NEON capability before accepting the mode and
otherwise fails startup instead of silently changing rendering behavior. It
vectorizes solid opaque and source-alpha spans used by fills, strokes, lines,
and scaled glyph rectangles. Framebuffer publication and individual glyph
coverage pixels remain on their existing paths.

The `[feather_screen]` status object exposes `worker_state`, queue depth/capacity
and high-watermark, submitted/rendered/coalesced/dropped batch counters,
`typer_restarts`, `worker_last_error`, `touch_available`, and
`touch_warning_visible` for on-printer diagnosis.

## Touch transport

Every page starts with `clear-hitboxes`. Feather prefixes each action ID with a monotonically increasing page generation, for example `18:print.pause`; late events from a replaced page are discarded by the plugin.

Typer polls the draw FIFO and `/dev/input/guppy` together. A normal tap generates `tap 18:print.pause`. A continuous hitbox generates records such as:

```text
touch 18:move.joy.xy begin 400 210
touch 18:move.joy.xy move 410 213
touch 18:move.joy.xy end 410 213
```

For continuous input, Typer emits a `move` heartbeat every 100 ms while a finger is stationary and emits a final `end` when the touch fd fails. It reports `touch-device unavailable` after a missing or lost device and `touch-device connected` after opening one. The event FIFO remains open while only touch is unavailable, and Typer retries the stable device path in its poll loop.

`FeatherScreen._process_touch_events()` handles partial FIFO reads, validates generation and format, wakes a dimmed panel on the first touch, debounces actions, and applies page/state gates. A lost device replaces an ordinary interactive page or a button-bearing modal with a non-interactive warning. Pre-ready startup and operation loaders are not covered. Reconnection canonically renders the current page again, restoring any page-owned modal and preserving a frozen shutdown screen's ownership. Continuous motion is further limited to the Move page, idle state, correct homing, and active joystick mode; actual motion remains inside Klipper's planner/toolhead path.

During a normal operating-system reboot or poweroff, init invokes [`.shell/S99root`](../../.shell/S99root) through its `K99root` link. That kill path publishes the neutral `action:forge_x_shutting_down` lifecycle marker before stopping Buildroot services; the manual `STOP_MOD` path invokes `S99root` and does not publish it. Feather observes the marker and freezes its existing critical startup surface with a shutdown message. The shared service stop sequence removes `/dev/input/guppy` last, after display and network services have stopped, so the marker is handled before touch teardown without teaching the touch transport about system lifecycle. Forced reboot, kernel panic, and power loss bypass this graceful lifecycle.

After `S99root start` or a manual `S99root stop` finishes writing service
progress to the framebuffer, it publishes `action:forge_x_redraw`. Feather
then renders the current page again after invalidating its persistent footer
cache, because the external progress renderer may have overwritten that
region. The signal is skipped when the Klipper command FIFO is unavailable.
`K99root` does not publish it, so the frozen shutdown surface remains
unchanged.

Startup and error pages clear the normal page hitboxes. The only actionable shutdown control is the generation-tagged `FIRMWARE_RESTART` button that Feather exposes after classifying an MCU recovery condition; it still routes through Klipper's normal G-code command path.

## Theme catalog lifecycle

`ui/theme_catalog.py` owns theme discovery, JSON Schema validation,
descriptions, and user-over-bundled override rules. `ui/theme.py` defines the
typed base colors and contextual roles, applies conservative role defaults, and
resolves each theme into one immutable physical palette. `FeatherRenderer`
consumes that resolved palette and never infers semantic meaning from a HEX
value.

The complete bundled and user catalog is loaded when the renderer is created
and reloaded on `klippy:ready`, which covers Klipper restarts. Opening the color
theme picker refreshes only `/opt/config/mod_data/themes` once and stores a
stable option snapshot. Paging, selecting, and applying use that snapshot and
do not rescan either directory. Bundled files are treated as immutable during a
Klipper process lifetime.

Every theme file is validated against `ui/themes/theme.schema.json`. Version 2
keeps required physical values under `colors` and optional contextual overrides
under `roles`. A role may reference a base color by name or provide its own HEX
value; role-to-role references are rejected. Invalid files are logged and
skipped. If the schema or bundled files cannot be read, the in-code
`FALLBACK_THEME` is resolved with conservative role defaults so Feather can
still render a usable interface.

## Component boundaries

| Component | Owns | Does not own |
|---|---|---|
| `feather_screen.py` | Klipper lifecycle, UI state machine, safety gates, reactor timers/fds, G-code/macro dispatch, shared status/error handling | Page-specific rendering or a separate motion process |
| `feather_safety.py` | Bounded activity providers, operation leases, armed-page composition, safety diagnostics | Page rendering, feature loading, or printer commands |
| `feather_feature_manager.py` | Lazy feature ownership, single-instance construction, loaded-only lifecycle and safety hooks | Importing cold features during update/shutdown |
| `feather_screen_pages.py` | Dashboard, files, USB browser presentation, print status, settings, themes, mod parameters, bounded network helpers, and recovery pages | Klipper lifecycle, USB mount ownership, motion planning, and direct display access |
| `ui/theme_catalog.py` | Theme schema, fallback palette, bundled/user catalogs, validation, override order, and refresh policy | Drawing commands, page state, or Klipper lifecycle events |
| `feather_files.py` | Compact file entries, print recency history, bounded USB discovery/helper lifecycle | Page rendering, destructive formatting, or direct block-device mounting |
| `feather_screen_controls.py` | Move, heat, filament, live Z adjustment, screws, and mesh workflows | Network child processes and renderer lifecycle |
| `feather_feature_ui_test.py`, `feather_ui_test/` | Lazy command facade plus one-run lifecycle, page/action sequencing, reversible printer/context fixtures, exact operation-context traces, framebuffer artifact worker, stale-run cleanup, and bounded `/data` retention | Normal Feather startup, Headless, persistent calibration saves, or renderer ownership |
| `feather_z_calibration.py` | Idle Z-calibration state, formula, zone aggregation, pressure hysteresis, pages, motion, and exact mesh/runtime restoration | Live-print Z adjustment or unrestricted G-code |
| `feather_ui.py` | Layout primitives, frame construction, FIFO and Typer child lifecycle, generation-tagged hitboxes | Klipper state decisions and printer commands |
| `feather_joystick.py` | Touch normalization, ramps/braking, bounded motion queue planning | Rendering or direct touch-fd I/O |
| `feather_mod_settings.py` | Mod-settings editor helpers | Persistence side effects |
| `typer` | Framebuffer, batch parsing, hitboxes, touch-to-event transport | Klipper, Moonraker, G-code, or printer policy |
| `S35tslib` / `ts_uinput` | Calibrated input device | Feather page/UI behavior |

`mod_params`, `resurrection`, and patched G-code logic are related Klipper-resident plugins/patches installed by the same overlay. They are not child services started by Feather.

Calibration, Z-offset, extruder calibration, settings, and the on-printer test
harness are lazy feature objects reached through `LazyFeatureManager`. The test
harness has no page ownership and remains cold until `_FEATHER_UI_TEST
ACTION=RUN`; status/abort queries use `peek()` and do not import it. Its small
feature facade owns only the current run reference; each invocation creates an
isolated run object and publishes it only after successful initialization.
Each product feature keeps its own scenario state and receives shared
printer/renderer services through `FeatureHostProxy` where page ownership is
required.
Lifecycle and safety broadcasts only visit loaded instances, so idle startup,
update, shutdown, and disconnect never import cold feature modules.

The Heat/Fan screen is a declarative `ff5m_ui.heat` page. Its heater and part
fan rows use explicit grid tracks for label, live value, and controls, so text
width cannot consume a neighboring button or hitbox. Material presets are
constructed from the active Heating slot order. Screen actions are typed and
resolved through the page action catalog; old free-form Heat wire actions are
not accepted by the page gate. Live nozzle, bed, and `fanM106` telemetry sits
in independent repaint boundaries, which keeps normal status refreshes to the
changed value rectangle. The page module itself remains lazy and is imported
only when the Heat page is first opened.

The filament material and action screens follow the same declarative contract
under `ff5m_ui.filament`. Their feature runtime owns navigation and printer
commands, while the material and action declarations are imported separately
only when each screen is first opened. The action status card is a repaint
boundary, so transitions between heating values cannot leave fragments of an
older label. Back from the action screen returns to material selection without
turning off the active nozzle target; finishing the workflow retains the
existing heater shutdown behavior. If the nozzle is more than 5 C above the
selected material target, the feature owns `fanM106` at 100% until the nozzle
reaches that band. It suppresses extrusion actions while cooling and restores
the fan to 0% when the band is reached or the filament workflow exits.

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

The complete repository Feather Python source set is about 364 KiB
(372,412 bytes), below the 500 KiB source budget. Splitting it adds a few module
headers but does not duplicate controller state. The file browser uses compact
slot-backed entries so a directory with many G-code files does not retain one
Python dictionary per row. It presents one flat list, scans at most two visible
subdirectory levels, and orders files by the newer of their upload/modification
time and Feather's persisted last-print time. The latter is stored in
`/opt/config/mod_data/feather_print_history.json` by default and is also updated
when a print is started outside the local screen.

List pagination is centralized in `feather_pagination.py`; file, Wi-Fi, prompt,
and calibration pages reuse its clamping and visible-row mapping. Mod parameters
paginate by pixels instead (`feather_mod_settings.category_pages`) because each
category heading is drawn inside the list and must share the page with at least
one of its rows; a category that does not fit repeats its heading, marked
`(CONT.)`, on the next page. A second heading on the same page is padded to a
whole row pitch so every row keeps one grid, and a page that had to postpone a
category spends the leftover row on a `mod.more` card naming that category, which
opens the page it starts on. The calibration menu covers every workflow
documented in `docs/CALIBRATION.md`: directly supported macros open guarded
confirmation, progress, result, and save pages, while axis and extruder rotation
calibration open measurement guides because those procedures require a printed
model and a deliberate `user.cfg` edit.

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

Primary implementation references in this repository:
[`feather_screen.py`](../../.py/klipper/plugins/feather_screen.py),
[`feather_ui.py`](../../.py/klipper/plugins/feather_ui.py), the published
[`typer`](../../.bin/exec/typer) runtime, and
[`S35tslib`](../../.root/S35tslib). Native source development is maintained
separately from the public runtime repository.
