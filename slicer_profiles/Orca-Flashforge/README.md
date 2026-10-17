# Forge-X Orca-Flashforge profiles

These files are Forge-X corrections to the FlashForge AD5M profiles shipped by
[`FlashForge/Orca-Flashforge`](https://github.com/FlashForge/Orca-Flashforge).
They were copied from upstream commit
`c45cc8216c184574c36873314ae7caea67c7a1ad` and retain the upstream profile
names so that they can replace the corresponding files in an existing
Orca-Flashforge profile tree.

The corrected regular AD5M 0.4 mm profile:

- delegates startup and shutdown to Forge-X `START_PRINT` / `END_PRINT`;
- emits object definitions required by KAMP adaptive meshing and purging;
- does not advertise the Pro model's auxiliary fan, filtration, or chamber
  temperature controls;
- identifies the 0.4 mm nozzle as stainless steel; and
- leaves generic PLA pressure advance under firmware ownership. The stock
  Forge-X fallback remains `pressure_advance: 0.035`; a calibrated filament
  preset may deliberately enable and override slicer pressure advance.

The included start G-code is the Forge-X **stock-screen** sequence. Feather
screen users should use only the first `START_PRINT` line, as documented in
[`docs/SLICING.md`](../../docs/SLICING.md#for-alternative-screen-feather-screen).

## Installing

These are system-profile source files, not standalone user-preset exports. The
safest workflow is to clone the matching FlashForge presets in Orca-Flashforge
and copy the changed values into the clones. Advanced users may instead close
Orca-Flashforge and replace the four matching files under its
`system/Flashforge` profile directory. Application updates can overwrite those
system files, so keep the Forge-X copies or reapply them after an update.

Do not copy this partial tree into an empty profile directory: it inherits the
remaining profiles supplied by Orca-Flashforge.
