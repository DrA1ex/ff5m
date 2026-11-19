# Feather first-run setup

First-run setup opens Welcome, Network and Color Theme in order. Both network entry points share their screen
layout, Wi-Fi scan and password entry. The wizard offers Continue Offline. The theme picker previews the palette
and saves it on Continue. An ordinary startup opens Welcome again until setup is finished or explicitly skipped.
Saved choices and completed calibrations carry over into the next session.

Printer Setup offers Time Zone, Essential Options, Additional Options and Calibration in any order. A saved mesh
is required to finish. Users can defer recommended checks after confirmation. The independent setup choice shows a
confirmation and records that automatic entry is disabled. Control > Settings > Setup Wizard opens Printer Setup directly.

## Ownership and invariants

SetupFeature owns navigation and explicit decisions. SetupProgress owns a validated, atomically replaced checkpoint
beside Mod Parameters (`/opt/config/mod_data/feather_setup.json`). Mod Parameters owns declarations, validation,
persistence and ordinary change hooks. Existing network, timezone and calibration services retain their own behavior.
The screen owns restart overlays, timers and cancellation. Setup adds no calibration algorithms or renderer path.

The startup opportunity is consumed on the first ready update. Automatic entry requires idle home, no active print,
operation or dialog. It resumes outstanding operations or opens incomplete setup when automatic entry is enabled.
Invalid checkpoints never trigger settings effects or automatic motion;
a readable completed version survives other damaged fields. Manual setup can write a fresh valid checkpoint.

The checkpoint records the latest introduction transition for diagnostics. TAR Debug includes the checkpoint
when it exists. `selections` holds the last values selected in the wizard before completion. For mutually exclusive
mesh options it includes any companion change. `draft` identifies selections still awaiting application and is
separate from the Mod Parameters file. The checkpoint also records the selected time zone, when set in the wizard.
It restricts draft and retry values to the three restart settings and calibration records to Safe Z, mesh and shaper.
Older checkpoints can contain a redundant snapshot of applied values. The reader discards that snapshot. Running and saving checkpoints name their calibration.
Saving also records a SHA-256 fingerprint of its effective configuration section; unrelated configuration values and
network credentials are never copied into progress. Failed replacement retains the previous file and memory state.

## Options and application

`feather/setup.py` declares the curated option groups and recommendations. `mod_params.json` supplies labels, types,
options, dependency visibility and optional `ui.setup_description` / `ui.setup_help`. Setup uses the common visibility
and display helpers, including inverted switches and persisted enum names. Essential Options shows the four core
settings and links to Additional Options. Open any category, then use Next to advance across both pages of
categories or Done to return from Feather. Peripherals groups camera, screen mirroring and chamber light.
Use Timelapse for camera capture, Display for brightness and ECO mode, Print Finishing for pause, parking and
motor behavior, and Feather for the theme, status bar and update checks.
The category list leads to calibration or offers Apply & Restart when changes are pending. Back returns from
the list to Printer Setup. Question buttons use shared message dialogs. Non-boolean values use the existing
parameter editors and return to the same setup group.

Ordinary selections apply through Mod Parameters immediately, including independent camera and screen streaming
hooks. Enabling KAMP turns off full-bed meshing before printing, and enabling full-bed meshing turns off KAMP;
the pair is persisted together. Purge Algorithm is visible for conventional priming. Camera device and process
status are re-observed at most every two seconds.

Tune Klipper, Tune Config and Power Loss Recovery are staged for one explicit Apply and Restart confirmation.
Setup validates and persists the batch with ordinary hooks disabled, then publishes the settings change. Tune Config
and recovery use the synchronous `setup_config` shell command (`zchanges.sh --apply-only`); its exit status is checked.
Tune Klipper requires a hard Klipper process restart; the other two require RESTART. The strongest restart wins.
Pending calibration must be saved or discarded before a settings restart. Changing Tune Config clears the setup
completion marks for mesh and Safe Z, because they need checking against the new configuration.

A checkpoint precedes effects and restart. Failed effects retain the requested values for explicit retry even if the
parameter file already matches. A replacement selection supersedes the previous retry value. On ready, committed
settings are read back before clearing the draft. Rejected restarts and restart timeouts restore the canonical
screen lifecycle. An interrupted application returns to review without automatically rerunning effects.

## Calibration

Setup reuses the Safe Z, full-bed mesh and Input Shaper workflows. Saving Safe Z in the wizard returns directly to
its calibration list and marks the step done, including when the value equals the previous setting. The wizard opens
Input Shaper with Standard vibrations selected. Pending option changes block calibration until applied.
Cancellation uses the calibration's usual cleanup and returns directly to the wizard. Leaving the wizard after
calibration motion disables the motors.

Mesh and shaper retain their normal result screens, selection controls and save paths. Setup checkpoints the target
configuration fingerprint before SAVE_CONFIG. After restart, setup opens the calibration list. It marks the step done
when the loaded section matches after normalizing whitespace. Failed readback displays a toast and leaves the step
incomplete. Interrupted motion returns to the hub without restarting hardware operations.

Mesh state and pending configuration remain entirely owned by Klipper. The normal DON'T SAVE action returns to
setup without reverting the active measured mesh, its profiles or pending sections. A later SAVE_CONFIG can save
that result alongside other pending changes; a Klipper restart discards unsaved configuration in the usual way.
Setup keeps no mesh snapshots and requires no BedMesh or PrinterConfig extensions.

## Timezone picker

The picker shows regions, optional letter ranges for large regions, cities, and fixed UTC offsets. Back follows these
levels and eventually returns to the calling page. Browsing uses `zone.tab` or directory names; only cities on the
visible page have their offsets queried through libc when the page opens, supporting slim TZif data and daylight saving. Periodic screen updates skip the location list. A selected zone
is validated and persisted by SET_TIMEZONE. The fixed-offset grid orders IANA whole-hour and fractional offsets by
their signed value, with UTC in the middle column. Signed buttons use two digits for hours and two for minutes.
Custom offsets set through SET_TIMEZONE remain visible in the current zone display.

## Verification

Product tests exercise native screen action routing, real Mod Parameters persistence, shared editors, restart
confirmation/failure/readback, Z save/cancel, native mesh save/leave behavior, malformed checkpoints and timezone navigation. Shell tests
run the repository's hook in an isolated harness. Layout inspection renders setup pages through Typer and Designer at
720, 800 and 960 pixels in Default and Industrial themes. No physical deployment is part of host verification.
