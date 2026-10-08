# Power Loss Recovery

Forge-X includes its own **Power Loss Recovery** system, internally called **Resurrection**, for Feather, Guppy, and Headless modes. It periodically saves enough print state to offer a recovery attempt after an unexpected power loss, reboot, Klipper crash, or MCU shutdown.

> [!IMPORTANT]
> Power Loss Recovery is a way to **save a long print that would otherwise be lost**. It does not produce a seamless print: expect a mark, seam, or weaker spot at the recovery point. For important prints, a UPS is more reliable.
>
> If the part is safety-critical, needs exact dimensions, or will carry a high load, consider reprinting it instead of using a recovered result.

The Stock screen uses the FlashForge recovery implementation. This page describes the Forge-X Resurrection implementation used by the non-Stock modes.

## Quick start

Power Loss Recovery is enabled by default for new Forge-X 1.4.2 configurations. If it was disabled, or your existing configuration preserved an older value, enable it with:

```gcode
SET_MOD PARAM=power_loss_recovery VALUE=1
```

Changing this parameter clears any old recovery checkpoint. In Feather, Guppy, or Headless mode, Klipper is restarted so the change takes effect.

To disable it:

```gcode
SET_MOD PARAM=power_loss_recovery VALUE=0
```

The default checkpoint interval is **3 seconds**. Advanced users can change it in `mod_data/user.cfg`:

```ini
[resurrection]
dump_time: 3.0
```

`dump_time` must be at least 1 second. A shorter interval writes checkpoints more often, but it does not make unexpected power loss perfectly exact because Klipper queues motion ahead of the physical printer.

When a valid recovery state is found after startup, Forge-X offers a recovery prompt where the active UI supports it. The same actions are available from the console:

```gcode
RESURRECT
RESURRECT_ABORT
```

- `RESURRECT` validates the saved state and attempts to resume the print.
- `RESURRECT_ABORT` discards the saved state and performs cleanup.

Before accepting recovery, **inspect the model, build plate, nozzle, and printer position**. If the model has moved, lifted, or detached, abort recovery.

## Best option when you know power will be removed

If you know that the printer will be switched off, use **PAUSE first**, wait until the pause and parking move have completed, and only then remove power.

Forge-X handles this case specially:

1. `PAUSE` asks Resurrection to write a checkpoint **before** `PAUSE_BASE` and before the toolhead is parked.
2. Periodic checkpoint updates are then frozen while the printer is paused.
3. The parked toolhead position therefore does not overwrite the actual print position.
4. After a normal `RESUME`, checkpoint updates are enabled again.

Once the pause and parking move have finished, all print moves before the pause have been executed, and the saved point still refers to the print position before parking. This is much more predictable than cutting power during printing.

> [!NOTE]
> This is the recommended way to handle a planned shutdown, but the result can still be affected by physical changes. Re-homing repeatability, a shifted part, thermal contraction, loss of bed adhesion, or other physical changes can still affect the resumed layer.

## Why an unexpected outage cannot be restored with perfect precision

There are two separate sources of uncertainty.

### Checkpoints are periodic

During normal printing Forge-X saves the checkpoint every `dump_time` seconds (3 seconds by default). An unexpected outage can therefore occur after the last saved checkpoint. Recovery may have to replay a short section of G-code that was processed after that checkpoint.

### Klipper queues motion ahead of the physical printer

Klipper does not execute a G-code move directly at the instant the host reads that line. The host processes G-code, builds a look-ahead motion queue, converts moves into timed step events, and sends those events to the MCU ahead of execution. Klipper's own [code overview](https://www.klipper3d.org/Code_Overview.html#code-flow-of-a-move-command) describes this host-to-MCU pipeline, and the [MCU command documentation](https://www.klipper3d.org/MCU_Commands.html#stepper-commands) describes the queued step commands.

As a result, three positions are related but are not the same clock:

- the G-code file position processed by the host;
- the motion already queued/sent to the MCU;
- the movement physically completed when power disappears.

Forge-X saves both the G-code file position and the Klipper toolhead position, but on a sudden power cut it cannot ask an unpowered MCU which individual step was the last one physically executed.

This is why a recovered print may show a small artifact around the resume area. Depending on the timing, a short section may be replayed or the printer may have to return to the saved position without reproducing every extrusion move that existed between the saved state and the physical loss of power. Possible results include a small blob, line, gap, visible seam, or weaker local bonding.

Saving checkpoints more often does not remove this: it comes from the motion queue, not from the save interval.

## What Forge-X saves

The periodic checkpoint contains the information needed to identify the print and restore its physical context:

- G-code file path;
- saved G-code file position;
- expected G-code file size;
- Klipper toolhead position;
- hotend target temperature;
- bed target temperature;
- active bed-mesh profile;
- current Z offset.

The checkpoint itself is written through a temporary file, flushed with `fsync()`, and atomically replaced. This avoids treating a partially written JSON file as a valid checkpoint after a power interruption.

Forge-X also re-parses the G-code from the beginning up to the saved file position before recovery. This reconstructs important modal state that is not safe to guess from the checkpoint alone, including:

- absolute/relative motion mode (`G90` / `G91`);
- absolute/relative extrusion mode (`M82` / `M83`);
- logical extruder position;
- feed rate;
- speed and extrusion factors (`M220` / `M221`);
- velocity/acceleration limits;
- Pressure Advance;
- skew correction;
- firmware retraction settings and retracted state;
- fan states;
- print progress and layer metadata.

## Validation before Forge-X moves the printer

Before the printer moves, Forge-X checks the saved state and the G-code file it refers to.

Among other checks, recovery is rejected when:

- the checkpoint cannot be parsed;
- a required checkpoint field is missing;
- the saved G-code file no longer exists;
- the saved file size or file position is invalid;
- the G-code file size has changed since the checkpoint;
- the file is outside the configured virtual-SD directory;
- the saved file position is not on a G-code line boundary;
- saved coordinates, temperatures, or Z offset are invalid or non-finite;
- the saved mesh is unavailable and no valid `auto` fallback exists;
- the G-code state up to the checkpoint cannot be parsed safely;
- required firmware-retraction state cannot be restored;
- the toolhead does not reach the recovered machine position.

The G-code parser also validates the state values it reconstructs instead of executing arbitrary values from a damaged file. If recovery fails after loading the print, Forge-X cancels the virtual-SD job, resets the recovery operation, turns the heaters off, and leaves the saved checkpoint available for inspection/retry where possible.

## Recovery sequence

When `RESURRECT` is accepted, Forge-X performs the recovery in a controlled order:

1. Load and validate the saved checkpoint.
2. Re-parse the G-code up to the saved file position to rebuild the modal print state.
3. Load the original G-code file and restore its virtual-SD position.
4. Load the saved bed mesh.
5. **Heat the bed to its saved target and wait for it first.**
6. Only after the bed is ready, **heat the nozzle to its saved target and wait for it.**
7. Home the printer if required.
8. Tare the AD5M load cell.
9. Restore the saved Z offset.
10. Restore skew and move the toolhead back to the saved machine position.
11. Restore extrusion mode/position, feed rate, speed factors, motion limits, Pressure Advance, firmware retraction, fans, and print metadata.
12. Resume virtual-SD printing from the saved file position.

Heating the **bed before the nozzle** is intentional. After an outage, the bed has had time to cool and the model may have lost some adhesion. Restoring the bed first gives the part the best chance to stabilize before the nozzle becomes hot and the printer returns to the model. It also reduces the time a fully heated nozzle spends near a cold or poorly adhered part.

This cannot re-attach a model that has already shifted or detached. Always inspect the part before resuming.

## Adhesion matters after a power loss

A print can be logically recoverable while being physically impossible to save.

When the bed cools:

- the build plate and model contract;
- a PEI sheet may release the part as it approaches room temperature;
- tall models can move even if they still appear to be touching the plate;
- reheating can change the model's position or first-layer stress.

For a long or important print:

- start with a clean build plate;
- use the normal bed temperature appropriate for the material;
- use a suitable adhesion aid if your material/build surface normally benefits from one;
- avoid touching the print or flexing/removing the build plate after an outage;
- inspect the entire base of the model before pressing **Restore**.

If the part moved, recovery should be aborted.

## UPS recommendation

For prints where failure is expensive, a UPS is more reliable than trying to reconstruct a print after power has already disappeared.

FlashForge lists the Adventurer 5M power supply as **350 W** on the [official AD5M specification page](https://www.flashforge.com/products/adventurer-5m-3d-printer). The Adventurer 5M Pro is also specified at 350 W.

For UPS sizing:

- check the UPS **watt** rating, not only its VA number;
- its continuous output should comfortably cover the printer's 350 W rated supply, with reasonable margin;
- if the goal is only to bridge short outages, you do not need a very large battery capacity;
- actual runtime depends heavily on whether the bed and hotend are heating, so do not estimate runtime from the 350 W rating alone.

A UPS with modest battery capacity but sufficient output power can be enough for brief interruptions. For longer outages, either size the battery for the required runtime or use the controlled **PAUSE → wait for parking → power off** workflow described above.

## Related documentation

- [Printing](PRINTING.md) — normal print, pause/resume, safety, and calibration behavior.
- [Configuration](CONFIGURATION.md) — Forge-X settings and `SET_MOD`.
- [Firmware Recovery Guide](RECOVERY.md) — recovering the printer/firmware itself; this is separate from recovering an interrupted print.
- [Forge-X Klipper extensions](../openwiki/workflows/klipper-extensions.md#resurrection-optional-power-loss-recovery) — engineering-level implementation notes.
