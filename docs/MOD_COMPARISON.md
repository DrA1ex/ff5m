# Forge-X vs Z-Mod vs xblax Klipper Mod

How the three AD5M (Pro) mods handle Klipper and the known stock-firmware bugs.
All three trace back to the original [xblax
`flashforge_ad5m_klipper_mod`](https://github.com/xblax/flashforge_ad5m_klipper_mod);
Forge-X and Z-Mod ([ghzserg/zmod](https://github.com/ghzserg/zmod)) take the
"keep native Klipper" approach, xblax replaces it.

## TL;DR

- **Forge-X / Z-Mod**: run the *stock native* Klipper and patch the known bugs
  in place. Maximum stability, trivial uninstall, MCU firmware untouched.
- **xblax**: compile *mainline upstream* Klipper (v0.13) and rebuild the MCU
  firmware to match. Fixes the bugs at the root, but is beta and reflashes the
  MCU.

## "Klipper 13" in Z-Mod is the MCU firmware, not host klippy

Z-Mod's `UPDATE_MCU` switches the **MCU / control-board firmware** between the
native Klipper 11 build (12 for AD5X) and a Klipper 13 build. The host-side
`/opt/klipper/klippy` stays **stock native Klipper** regardless — Z-Mod FAQ:
*"Z-Mod uses the standard Klipper from the native firmware, as well as Klipper
13."*

Klipper-13-MCU is **off by default** (`klipper13 = 1` opt-in in
`mod_data/variables.cfg`), precisely because it requires the MCU and host
Klipper versions to match, otherwise it throws version-mismatch errors. It is an
optional MCU upgrade — **it does not replace the host-side workarounds**.

The `control-2.2.3` MCU blob is byte-identical across stock firmware 3.1.3 →
5.1.2 (see `FIRMWARE_5x_COMPAT.md`), so stock 5.x still ships MCU-Klipper-11.

## Workarounds: Forge-X vs Z-Mod

Both patch the same stock host Klipper files. Forge-X applies them as part of the
mod; Z-Mod exposes them as runtime flags (`SAVE_ZMOD_DATA`).

| Stock bug | Forge-X | Z-Mod |
|---|---|---|
| E0011 (comm timeout / TRSYNC) | `ztune_klipper.sh`: `mcu.py` `TRSYNC_TIMEOUT 0.025 → 0.05` | `FIX_E0011`: `mcu.py` `TRSYNC_TIMEOUT → 0.1` |
| E0017 (move-queue overflow / LOOKAHEAD) | `ztune_klipper.sh`: `toolhead.py` `LOOKAHEAD_FLUSH_TIME 0.5 → 0.150` | `FIX_E0017`: `toolhead.py` `LOOKAHEAD_FLUSH_TIME → 0.150` |
| Broken SCV (shaper/accel graphs) | `tuning.cfg`: `square_corner_velocity: 9` | `FIX_SCV` (`SCV=9`) |
| Power-loss / resume | `resurrection.py` plugin | `ZRESTORE` |
| Unicode object-name bugs | `gcode.py` patch | native patch |

Conclusion: switching Z-Mod to "Klipper 13" (MCU) does **not** let it skip these
host-side fixes. Both mods carry the same class of in-place patches over the
stock native Klipper.

## xblax: different architecture

xblax replaces Klipper entirely instead of patching the stock one.

| | Forge-X / Z-Mod | xblax |
|---|---|---|
| Host Klipper | stock native (v11-era) + in-place `.py` patches | mainline upstream **v0.13** compiled from source |
| MCU firmware | stock Klipper-11 (Z-Mod: opt-in 13) | rebuilt to match upstream; auto-flashed on install, auto-downgraded on uninstall / dual boot |
| E0011 / E0017 | host-patch stock klippy (`TRSYNC` / `LOOKAHEAD`) | upstream Klipper + one clean patch `0003-Relax-MCU-TRSYNC_TIMEOUT-for-ffad5m` |
| SCV | force `square_corner_velocity: 9` (works around stock graph bug) | normal `square_corner_velocity: 25` (no stock bug — real Klipper) |
| Build | ship patched Python files over stock | full **Buildroot** image; Klipper as a Buildroot package |
| Status | stable / production | self-labeled **beta**, reverse-engineered MCU support |

Because xblax runs real modern Klipper with a matching MCU, most of the
Forge-X / Z-Mod "workarounds" are simply upstream defaults there and are not
needed as hacks. The trade-offs: it is beta, it reflashes the MCU (larger blast
radius, though it auto-reverts on dual boot), and it has its own RAM-driven
known issues on the 128MB T113 (e.g. MCU timeouts during `SHAPER_CALIBRATE` and
after long idle).

## Design philosophy

Forge-X (shared with Z-Mod): **do not touch native Klipper or MCU firmware.**
This maximizes stability and makes uninstall trivial (just remove the overlay
files), at the cost of carrying the targeted patches above indefinitely. xblax
chooses to fix the root cause by running upstream Klipper, accepting the
complexity of rebuilding and reflashing the MCU.
