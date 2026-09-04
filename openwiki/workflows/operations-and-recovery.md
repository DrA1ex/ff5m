# Operations and recovery

## Supported installation envelope

The documented target is Flashforge Adventurer 5M / 5M Pro stock firmware from 2.6.5 through tested 5.1.x releases, with at least 512 MB free in `/data` and 128 MB free in `/` ([`docs/INSTALL.md`](../../docs/INSTALL.md)). Do not generalize support beyond this documented range based on the common architecture alone.

Installation uses a FAT32 USB drive and an intact release archive named for the specific model. Follow the procedural source exactly: [`docs/INSTALL.md`](../../docs/INSTALL.md). Afterward, configure slicer G-code/host access and complete calibration before printing.

## Normal boot and updates

- `.shell/S00init` is the small start-only stock-init guard. It resolves durable boot flags before optional mod code and invokes `.shell/init-main.sh` for normal runtime preparation. Reloads call `init-main.sh` directly.
- `.shell/boot/boot_mode.sh` gives mutable boot scripts one canonical view of temporary mod/stock state and one normalized stock-mode publication operation.
- `.shell/boot/boot.sh` starts `netd` directly for non-Stock modes. Feather continues offline-capable without waiting; Guppy/headless use the thin `netd-cli wait --timeout 180` and retain the Stock fallback. The wait timeout is boot policy only; `netd` itself keeps the configured network reconnecting indefinitely.
- `.shell/S99root` creates/migrates Moonraker state on first run and starts the chroot stack.
- Moonraker's update manager defines the Forge-X, Fluidd, Mainsail, and GuppyScreen updaters in [`moonraker.conf`](../../moonraker.conf). User instructions say OTA updates are initiated through **Configuration → Software Update** and are limited to the same major version ([`docs/INSTALL.md`](../../docs/INSTALL.md)).

Before an OTA or configuration migration, retain logs and backups. `init-main.sh` rotates stock logs and links mod logs into `/data/logFiles/mod`; `S99root` uses a bootstrap start/stop cycle to initialize/migrate the Moonraker database when absent.

### Early USB boot-media inspection

Special boot flags and a standalone `flashforge_init.sh` are searched by [`.shell/boot/init_boot_flag.sh`](../../.shell/boot/init_boot_flag.sh). Firmware-image detection is delegated to [`.shell/boot/install-image.sh`](../../.shell/boot/install-image.sh) through its `test <directory>` operation, which alone owns the supported `Adventurer5M*.tgz` / `Adventurer5M*.tar.xz` naming and selection policy. The boot-flag script shares [the USB discovery and mount path](configuration-and-printing.md#usb-swap-and-drive-preparation) used by swap initialization, waits for late kernel device-node creation, scans every supported partition (or a whole-disk filesystem), and reads media using `ro` access. Every callback receives the inspected source directory. A temporary inspection mount is released immediately when no flag is found and before every non-image action. For an accepted image, `install-image.sh` selects the file and releases the temporary source mount immediately after staging. A mount that existed before inspection is retained.

The `S00init` guard captures the original `firmwareExe` parent PID once for the
whole early boot attempt, then invokes the recovery and special-boot handlers
with that exported value. The image callback launches `install-image.sh`;
image paths and mount ownership do not leak into `S00init`.
[`stock_identity.sh`](../../.shell/boot/stock_identity.sh) reads the stock
`MACHINE` / `PID` pair from the active `auto_run.sh` for both recovery and the
installer. The installer accepts only an image named for that model. Once an
image is accepted, it stops
`ffstartup-arm` and terminates the original `firmwareExe` parent before mounting
`/data`, so neither a launcher restart nor a mount failure can resume the stock
boot. A terminal failure does not return while a live stock parent cannot be
stopped. The installer does not start networking. It validates tar member
names, checks free space against the uncompressed tar size plus a 16 MiB
reserve, replaces `/data/.firmware` and `/data/.firmware-runner`, and streams
extraction through one screen row updated by percentage. A `.tgz` is
deliberately treated as an uncompressed tar; a `.tar.xz` is streamed through
`xz -dc` and then tar. The next ordinary boot removes both staging directories
through the same script's `cleanup` operation after `/data` is mounted. The
package directory is always removed and recreated before extraction, so files
from an earlier package cannot enter the selected installer.

The staged entrypoint policy is fixed and fail closed:

1. If `forge-x-init` exists, it must be a non-symlink executable ELF file and is selected.
2. Otherwise, if `forge-x-init.sh` exists, it must be a non-symlink regular file accepted by `bash -n` and is selected.
3. Only when neither Forge-X entrypoint exists may a non-symlink `flashforge_init.sh` accepted by `bash -n` be selected.

The selected entrypoint runs from `/data/.firmware` with the same stock machine
and product arguments (`Adventurer5M 0023` or `Adventurer5MPro 0024`).
Immediately before hand-off, the splash stops and Typer reports that the
installer is being prepared; it does not claim that the image is already
running. The immutable package tree remains untouched. A small runner, Typer,
Typer's required `libstdc++` runtime, and the detached log live in the sibling
`/data/.firmware-runner` directory. The hand-off starts that runner with
detached streams and returns so the `S00init` and `logged` processes can finish.
The runner reports its wait while old mod mounts or open files remain. It
records those references and shows a terminal blocked screen if they remain
for 30 seconds, rather than waiting invisibly forever. Once released, it clears
the persisted boot-screen queue and starts the selected entrypoint from the
package directory. A binary installer that remains alive for ten seconds owns
the framebuffer and all subsequent recovery through its own watchdog; the
runner stops observing it. A binary that returns `0`, `100`, `101`, or `102`
during that startup window has also completed through its own contract and
does not trigger another screen. Only an early loader, crash, or
unexpected-status failure receives the runner's emergency screen. Legacy shell
entrypoints retain their synchronous delayed fallback.

This path intentionally depends on kernel block metadata rather than partition-table parsing. Changes must retain coverage for multiple and logical partitions, digit-suffixed block names, whole-disk filesystems, existing read-only mounts, FAT/ext filesystems, firmware images, and normal boot flags. Unsupported filesystems are skipped so root/eMMC flag checks and the remaining recovery path still run.

### Operational caveat: release channels

`moonraker.conf` currently configures Forge-X as a Git updater on `channel: dev`, while `version.txt` states 1.4.1. The inspected repository does not establish whether every released device should consume that channel. Do not “fix” this discrepancy without an explicit release-policy decision.

## Diagnostic order

1. **Preserve evidence:** do not delete install, boot, uninstall, or recovery logs. The root README calls this out because logs can make recovery possible.
2. **Check the current mode:** determine whether a `SKIP_MOD` condition, failed mod boot, network setup in alternate mode, or a service failure is responsible.
3. **Use the lowest-risk documented escape hatch:** the project offers dual boot/failsafe before destructive recovery.
4. **Use the documented recovery ladder:** debug image, dry-run image, full recovery, uninstall image, factory image; only then consider UART/U-Boot/FEL work.

## Dual boot, uninstall, and recovery

[`docs/DUAL_BOOT.md`](../../docs/DUAL_BOOT.md) documents temporary mod bypass
mechanisms. A reboot during the three-second early skip window enters the
bounded recovery lifecycle before normal mod initialization. The small
`S00init` guard creates `BOOT_FLAG_RECOVERY_FAILURE` before invoking
[`.shell/boot/recovery.sh`](../../.shell/boot/recovery.sh). That marker remains
armed for the complete UI, SSH, download, verification, and firmware hand-off
lifecycle. `S00init` disarms it only after the recovery process returns. A power
loss, restart, crash, or hang before that return therefore makes the next boot
consume the marker and perform one hard stock bypass. A completed recovery does
not add another stock boot after the selected stock hand-off. Thus recovery
remains optional and stock boot does not depend on Python, Typer, networking,
GitHub, image metadata, or the large normal initializer being healthy.

The stock tmpfs lifecycle clears `/tmp` on reboot, so ready and stock-mode
flags apply only to the current boot. Persistent recovery markers are consumed
before the guard publishes the corresponding temporary stock mode. Early boot
therefore does not depend on an optional kernel boot-ID interface.

`BOOT_FLAG_FAILURE` remains armed only while normal initialization has not
returned a confirmed result. A crash or reboot during that interval therefore
selects one hard stock boot. If the initializer returns an error or returns
without readiness, the guard first publishes hard stock for the current boot
and then consumes the marker; the next reboot retries normal initialization
instead of repeating an already completed fallback.

The recovery wrapper owns its action file and temporary early lifecycle:
mounting the already-installed Buildroot chroot, binding `/data`, `/opt/config`,
`/run`, `/tmp`, `/dev`, `/sys`, and `/proc`, starting `S35tslib`, optionally
starting `netd`, and reversing only resources it started. It consumes every UI
action itself, including the `Stock + SSH` and `Stock Only` markers. `S00init`
sees only whether the wrapper returned; on failure it applies `SKIP_MOD_HARD`,
while `Stock + SSH` remains an explicit user choice. The wrapper discovers
exactly one stock `/opt/Python-*/bin/python3.*` runtime and starts the UI outside the chroot;
the chroot remains limited to its installed tslib service. The UI owns one Typer
FIFO/touch session. It can select `Stock + SSH` or `Stock Only`, use
Ethernet or an already-saved Wi-Fi profile, start the stock-side Dropbear
service and remain before normal printer services, run the canonical checksum
command, or request a firmware hand-off. The wrapper mounts `/dev/pts` before
Dropbear can be started, so an accepted SSH connection can allocate a terminal
and launch its shell. It releases a mount that recovery created unless recovery
SSH is intentionally carried into `Stock + SSH` mode.
New Wi-Fi credentials are
intentionally outside this early UI so passwords never need to cross its action
file or process arguments.

Configuration reset follows the same semantic hand-off: the UI offers backup
before confirmation, and the wrapper invokes the model-aware transactional
reset command. The default-file provenance, exact reset scope, rollback
contract, and remaining hardware checks are recorded in
[`recovery-integration-notes.md`](recovery-integration-notes.md).

After `/data` is mounted, each entry into recovery rotates
`/data/logFiles/recovery.log` through five older attempts and captures the
complete shell/Python lifecycle in the new file. Reopening the UI within that
same recovery session does not rotate again, and normal Forge-X or stock boots
do not touch this log. If the log cannot be prepared, recovery and its fail-open
stock fallback continue without it.

The recovery UI keeps the model and network summary in the right side of its
header. Connected network details use two vertically centered lines. Connection
screens render the current netd stage, selected network, and retry number until
the requested interface owns a usable address. A short visual
flash acknowledges every accepted button tap before its action starts.

Every stock mode reaches one `S55boot` hand-off before Klipper starts. It
allows 15 seconds to restore the stock display configuration. On timeout it
terminates the complete restoration process group, removes its temporary config
file, cleans up its chroot mounts, and continues vendor boot. `skip.log` is
written directly and includes the early guard's `Stock Only` fallback reason. A failure
in optional screen reporting therefore cannot block or hide the fallback. Hard
mode remains restrictive, leaving vendor init to own network startup and
keeping Forge-X networking, Dropbear, and other mod services disabled.

Downloads use the vendor HTTPS-enabled `/opt/cloud/curl-*-https/bin/curl` with
the CA bundle belonging to the selected stock Python. Recovery loads the saved
fake clock and owns a temporary chroot NTP service while its networking is
available, because certificate validation occurs before the normal Forge-X
runtime would otherwise synchronize time. A failed request reports curl's
actionable error line rather than the final generic certificate-help line.
The UI acknowledges an image selection immediately and reports the initial
server-connection stage even before curl has created its partial file. Downloads
are written as
`.part` files under `/data/forge-x-recovery`, checked against the GitHub asset
size, validated as safe firmware archives, and atomically renamed. Because the
stock Python lacks `_lzma`, `.tar.xz` validation streams through `/usr/bin/xz`.
The firmware menu can clear every regular file directly inside the dedicated
download directory after confirmation; it never follows symlinks or removes
subdirectories. Individual files remain operator-managed through Fluidd or
Mainsail.
The fixed factory catalog additionally checks the published MD5 values. Forge-X
release discovery uses the GitHub releases API rather than `releases/latest`,
so stable and prerelease assets remain selectable. Factory and file-recovery
entries remain a small explicit catalog matching the documented images.

The UI never executes a firmware entrypoint. After two explicit confirmations
it writes a validated
`/data/forge-x-recovery/Adventurer5M*.tgz|tar.xz` action; the wrapper tears down
recovery-owned services and passes that exact file to the existing
[`install-image.sh`](../../.shell/boot/install-image.sh). Archive validation,
model enforcement, stock-parent termination, staging, entrypoint selection,
detached hand-off, and terminal installer failure behavior therefore retain one
canonical owner. The installer and its detached runner are headless: they emit
status and failure details only through their output streams and never own the
framebuffer or splash lifecycle. Recovery renders its own hand-off page, then
connects the installer's output to the standard `logged --send-to-screen` path
while retaining `recovery.log`, so its `//%` archive-extraction progress remains
visible. Normal USB-image boot routes the same output through the existing init
logger and splash. The installer status remains authoritative if either screen
logger returns a different status. The detached runner immediately reconnects
its standard streams to `/dev/console`, and the selected installer inherits
them unchanged. Immediately before starting the installer, Recovery publishes
a blank full-screen frame so its final hand-off page cannot remain behind the
new bottom-aligned log rows. A separate rotated
`/data/logFiles/firmware-installer-launch.log` retains only hand-off, startup,
and early-exit lifecycle events and is included in Forge-X debug archives;
normal installer output remains owned by the installer's application log.

The checksum command now returns non-zero when a listed file is missing or
changed and prints live checked/failure counts plus a final result.
`verify-plain` exposes that same behavior without starting a competing screen
renderer. The recovery UI records every output line in
`/data/logFiles/verification.log`, while it redraws changed counters and up to
seven recent status lines no more than once per second and always publishes the
final state. Failures are rendered in red, and the UI offers the documented
full recovery image after a failed check. Skipping the mod is still not
equivalent to reverting configuration—keep that distinction clear in
support/runbook work.

When the checksum list names a stock Klipper module currently replaced by the
managed Forge-X patch overlay, verification checks the adjacent regular
`<module>.bak` stock copy. This exception applies only when the active path is a
symlink into the managed `klipper/patches/` tree. A missing or changed backup is
still reported as corruption; plugin links and unrelated symlinks are never
masked by a neighboring backup.

The recovery `REBOOT` action clears the active recovery guard and every
temporary stock-mode selection before rebooting. It therefore requests the
normal Forge-X boot path; only the explicit `Stock + SSH` and `Stock Only`
actions select a stock boot.

[`docs/UNINSTALL.md`](../../docs/UNINSTALL.md) is the source for `REMOVE_MOD` / soft removal and USB/image paths. Use it rather than constructing an uninstall from repository scripts.

[`docs/RECOVERY.md`](../../docs/RECOVERY.md) establishes escalation:

1. Flash debug image and retain diagnostics.
2. Run dry recovery to identify corruption without restoring it.
3. Use full recovery if appropriate.
4. Try uninstall/factory firmware images if recovery does not restore operation.
5. Use UART/U-Boot only for systems that cannot use USB recovery; the guide requires **3.3 V** UART and warns that 5 V can damage the board. FEL is last-resort territory.

These are hardware operations. This wiki records the routing, but the user-facing guide has the necessary physical instructions and must be followed verbatim.

## Operational integrations

| Integration | Operational source | Notes |
|---|---|---|
| Moonraker / Fluidd / Mainsail | `moonraker.conf`, `.root/S65moonraker`, `.root/S70httpd`, `docs/INSTALL.md` | API at port 7125; static UIs are HTTP paths. API key auth is disabled in default config. |
| Stock / Feather / Guppy / headless screens | `config/*.cfg`, `.shell/boot/boot.sh`, `docs/SCREEN.md` | Screen selection changes network and calibration workflow. |
| Camera | `.shell/S98camera`, `.shell/commands/zchanges.sh`, `docs/CAMERA.md` | Ensure stock camera stream is disabled before enabling mod camera. |
| Remote SSH / Telegram timelapse | `.shell/S98zssh`, `telegram/`, `docs/TELEGRAM.md` | User-side SSH material is mutable/private and intentionally not inspected here. |
| Cloud blocking | `.shell/init-main.sh`, `mod_params.json`, `docs/CONFIGURATION.md` | `block_cloud` is opt-in; it changes `/etc/hosts` entries, not routing/firewall. |

## Runbook for changes to boot or operations

- Review the stock-mode fallback and verify that a failure cannot strand the device before recovery access.
- Exercise the default Feather mode, explicit Stock mode, and each touched alternative-display path.
- If service order, update behavior, mounts, or persistent locations change, review `S00init`, `init-main.sh`, `S55boot`, `S99root`, `.root/start.sh`, and the relevant docs together.
- Add/adjust recovery instructions whenever an operational change affects rollback, diagnostics, or required recalibration.
- Apply the validation guidance in [Testing and change guide](../testing-and-change-guide.md).
