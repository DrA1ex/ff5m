# Stock Firmware 5.0.x / 5.1.x Compatibility Analysis

Analysis of FlashForge Adventurer 5M Pro stock firmware versions **5.0.3** and
**5.1.2** against the last firmware range the mod officially documented
(**2.6.5 – 3.1.5**, using factory image **3.1.3** as the in-range baseline).

**Conclusion: Forge-X is compatible with stock firmware 5.0.x and 5.1.x.**

## Method

Factory `.tgz` images were obtained from the ghzserg/FF mirror:

- `Adventurer5MPro-3.1.3-2.2.3-20250107-Factory.tgz` (baseline, in supported range)
- `Adventurer5MPro-5.0.3-2.2.3-20260122.tgz` (target)
- `Adventurer5MPro-5.1.2-2.2.3-20260418.tgz` (newest available)

The `.tgz` images are plain (uncompressed) tar archives of xz-compressed
sub-packages — **not encrypted**. Each sub-package was extracted and compared
file-by-file with `cmp`. Binaries were inspected with `strings` and `binwalk`.

## Image structure

Each factory image contains:

| Sub-package | Contents |
|---|---|
| `kernel-2.1.6.tar.xz` | `uImage` (raw Linux-5.4.61 kernel, uncompressed), `sunxi.dtb`, `8821cu.ko`, boot logos |
| `control-2.2.3.tar.xz` | **MCU / control-board firmware** (`Mainboard-*.bin`, `Eboard-*.hex`, `mcu.img`, flashers) |
| `software-<ver>.tar.xz` | Stock control app `firmwareExe` (Qt) + web client |
| `library-<ver>.tar.xz` | `ffstartup-arm` + NetEase IM cloud SDK (`nim.tar`) + RSA helpers |
| `boot.img`/`start.img`/`end.img` | Screen splash bitmaps (not boot partitions) |
| `mencoder`, `play`, `inittab`, `shadow`, `flashforge_init.sh` | Misc + installer |

**Klipper is not present in any factory image.** `binwalk` confirms `uImage`
carries no embedded rootfs/squashfs/ext4 and the `.img` files are bitmaps.
Klipper (`/opt/klipper/klippy`) lives in the printer's persistent eMMC rootfs
and is **not shipped or modified by OTA `.tgz` updates**. This is why a stock OTA
from 3.x to 5.x cannot alter Klipper, and why the mod (which overlays its own
files onto `/opt/klipper/klippy`) is unaffected by the stock-version bump.

## What actually changed: 3.1.3 → 5.1.2

File-by-file `cmp` of every sub-package (ignoring the `md5sum.list` manifest):

| Component | Result |
|---|---|
| Kernel (`uImage`, modules, dtb) | **identical** |
| MCU / control-board firmware (all 7 files) | **identical** |
| Screen bitmaps, `mencoder`, `play`, `inittab`, `shadow` (root creds) | **identical** |
| Partition layout (`mmcblk0p7` → `/data`) | **unchanged** |
| Installer `flashforge_init.sh` | only drops copy-steps for files no longer shipped; **no signature/verification added** |
| `software` | **only `firmwareExe`** changed |
| `library` | **only `nim.tar`** changed |

The entire divergence is confined to the stock control app (`firmwareExe`) and
the cloud/telemetry layer (NetEase IM SDK).

### firmwareExe behavior change

`strings` diff shows 5.x added an **Eclipse Paho MQTT** client — the Jan-2026
"Flash Studio" connectivity model. New endpoints:

- `api.fdmcloud.flashforge.com` (MQTT broker bootstrap), `mqtt://…:8083`
- `api.voxelshare.com` (model sharing)
- `update.flashforge.com/api/updates` (stock OTA)
- `/api/v3/model/upload`, `/model/log/upload`, `/message/device/event`
- Model uploads also use **AlibabaCloud OSS** via dynamic signed URLs
  (`N12AlibabaCloud3OSS...` symbols).

All of it is telemetry / remote-model / OTA. None of it is something the mod
depends on.

## Why the mod is unaffected

Forge-X is self-contained:

- runs its own Moonraker, Klipper overlays, buildroot and runtime;
- mounts over `/data` and chroots into `/data/.mod/.forge-x`;
- blocks the stock cloud endpoints via `/etc/hosts` (`.shell/S00init`) in
  **every** screen mode — this is the protection that always applies.

### The stock UI (`firmwareExe`) is not killed by default

`firmwareExe` is the stock **touchscreen UI** (Qt app), not the whole stock
stack. Forge-X has four screen modes (`mod_params.json` /
`.shell/commands/zdisplay.sh`): `STOCK`, `FEATHER`, `GUPPY`, `HEADLESS`.

- The **default is `STOCK`** (`mod_params.json`: `"default": "STOCK"`;
  `zdisplay.sh test` also falls back to `STOCK`). In this mode the stock screen —
  and therefore the changed 5.x `firmwareExe` — **keeps running**.
- `killall firmwareExe` runs only inside `apply_display_off()`, which is invoked
  for `FEATHER` / `GUPPY` / `HEADLESS` (and via `apply` when the mode is not
  `STOCK`). Only then is the changed binary stopped.

So 5.x is safe not because the binary is killed — on the default STOCK screen it
runs — but because its new MQTT bootstrap, VoxelShare and OTA hosts are pinned to
`127.0.0.1` in `/etc/hosts` regardless of screen mode.

Everything it depends on — partition layout, USB `.tgz` installer, kernel
modules, MCU firmware, `/opt/klipper` — is byte-identical between 3.1.3 and
5.1.2. The installer still accepts the mod's `.tgz` (same format, no signature
check). `zversion.sh` only checks the mod's own core-vs-OTA version, never the
stock firmware version, so there is no hard gate to bypass.

## MCU Klipper version (11 vs 13)

The AD5M/Pro MCU ships **Klipper 11** firmware (the `control-2.2.3` blob). Z-Mod
can reflash the MCU to Klipper 13 and back via `UPDATE_MCU`. That MCU blob is
**byte-identical 3.1.3 → 5.1.2**, so stock 5.x still carries MCU-Klipper-11.
Forge-X targets v11 and keeps it — it does **not** switch to v13; instead it
patches the known v11 bugs in place:

| Stock v11 bug | Forge-X fix |
|---|---|
| E0011 communication timeout | `ztune_klipper.sh`: `TRSYNC_TIMEOUT 0.025 → 0.05` (`mcu.py`) |
| E0017 move-queue overflow | `ztune_klipper.sh`: `LOOKAHEAD_FLUSH_TIME 0.5 → 0.150` (`toolhead.py`) |
| Broken square-corner velocity | `tuning.cfg`: `square_corner_velocity: 9` |
| Faulty resume / power loss | `resurrection.py` plugin |
| Unicode object-name bugs | `gcode.py` patch |
| Nozzle collision | `load_cell_tare.py` + bed-collision protection |

## Downgrade / version availability

When (and only when) a downgrade is actually needed:

- **5.0.x → no downgrade.** It is in the verified range; install the mod directly.
- **5.1.x → optional downgrade** if you want to stay in the verified range.
  Untested, but the installer accepts it (no signature check), so it is your call.
- A downgrade is also needed when coming from an unsupported state or recovering a
  broken install — see `UNINSTALL.md`.

**Available `-Factory` images** (ghzserg/FF mirror, Pro, at time of writing):
`3.1.3`, `3.2.3`, `3.2.4`, `3.2.5`, `3.2.7`. The 5.0.3 / 5.1.2 images are **not**
`-Factory` builds. `UNINSTALL.md` historically lists only `2.7.8` and `3.1.3`;
the newer `3.2.x -Factory` images are equally valid downgrade targets.

### The 3.1.5 "flash 3.1.3 first" rule — verified

`UNINSTALL.md` says the official 3.1.5 image lacks printer config files, so you
must flash a `-Factory` image (3.1.3) first, then 3.1.5. This is **correct** and
the mechanism is verifiable:

- `flashforge_init.sh` copies `printer.cfg`, `printer.base.cfg` and `tmc.py` to
  the system **only if those files exist in the flashed image**.
- `-Factory` images (e.g. 3.1.3) **contain** all three. The thin images
  (3.1.5 official, and the 5.0.3 / 5.1.2 images) **contain none of them** —
  confirmed by extracting each image.

So flashing a thin image onto a printer that has no config (e.g. after a wipe)
leaves it without `printer.cfg`; seeding it from a `-Factory` image first fixes
that. The same property is why 5.x images are pure app/cloud updates — they ship
no config, kernel or MCU changes.

## Changes applied to the mod

1. **`docs/INSTALL.md`** — raised the documented stock-firmware ceiling and
   explained the 3.2.x–5.1.x change surface.
2. **`.shell/S00init`** — extended the existing `/etc/hosts` cloud block to the
   full telemetry/cloud host set confirmed in stock 5.1.x `firmwareExe`. Kills
   the MQTT bootstrap, VoxelShare, stock OTA and the sz3dp/polar3d/qvs hosts.

### Known limitation

Model uploads use AlibabaCloud OSS via **dynamic signed URLs** whose hostnames
are generated at runtime; these cannot be neutralized through `/etc/hosts`.
A router/firewall block is required for a full air-gap.

### Caveat

`/opt/klipper` is not in any factory image, so a unit **factory-flashed** on 5.x
*could* carry a newer base Klipper than the 3.x baseline the overlays were
written against. An OTA update from 3.x to 5.x does not touch it. This was not
verifiable without a device rootfs dump or physical printer.
