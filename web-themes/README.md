# Forge-X Feather web themes

Native **Mainsail 2.19** and **Fluidd 1.37** theme packages styled after Forge-X
Feather's DEFAULT palette. No frontend source patches, custom JavaScript, API
changes or firmware changes. The original application remains responsible for
all printer controls and safety behavior.

## Install

1. Back up your existing `.theme` and/or `.fluidd-theme` directories.
2. Extract `forge-x-feather-theme.zip` on your computer.
3. In Mainsail's Machine/config file browser, enable hidden files and upload
   `.theme` into the Moonraker **config** directory. For Fluidd, use
   `.fluidd-theme` instead. You can install both.
4. Reload the browser and select dark mode. No Klipper or firmware restart.

Remove the installed theme directory (or restore your backup) to revert. The
ZIP contains theme files only; it must not replace your printer configuration.
App/printer names and plotted-series colors remain native application settings.
Fluidd's header mark is styled through CSS; Mainsail uses its native sidebar-logo
and favicon support. Colors and layout are intended for dark mode.

## Build and provenance

Run `python3 build.py`; Python's standard library is the only build dependency.
It packages both hidden theme folders and embeds the two font files in CSS, so
hosted frontends do not fetch fonts from an incorrect printer-relative URL or
an external font service.

Palette authority: `DrA1ex/ff5m` commit
`1ceaff18778b689684bd5c6b61cb7f37cb99e958`,
`.py/klipper/plugins/ui/themes/default.json` (GPLv3). Background `#030607`, panel
`#050c0f`, cyan `#35d9e6`, violet `#b47aff`, border `#295c66`, warning `#f2c94c`,
danger `#ff4d5a`. Secondary web text is brighter than Feather's DIM token for
readability. Roboto is supplied by the host frontend; JetBrains Mono is embedded.

Fonts: JetBrains Mono Regular/Bold, obtained from the official
https://github.com/JetBrains/JetBrainsMono repository, `fonts/webfonts/`.
The OFL license is included in each package. The geometric X mark is original to
this theme; it is not an official upstream Forge-X logo. The wordmark is outlined
from JetBrains Mono Bold. No raster art or generated imagery is used.

The styles are GPLv3, with font licensing as noted above. Native theme support
was tested on unmodified Mainsail and Fluidd. Browser snapshots use isolated
sample state or read-only printer data; no motion/heating commands are issued.

## Forge-X integration

Normal initialization links `/opt/config/.theme` and
`/opt/config/.fluidd-theme` to this bundle only when those paths do not already
exist. Existing custom directories, files and even dangling links are preserved.
Managed links follow Forge-X updates automatically. Uninstall removes only links
pointing to this bundle. Theme-install failure does not block printer startup.
To opt in when a personal theme exists, back it up and move it aside first.

Run `python3 web-themes/build.py` after editing the CSS, fonts or logo sources.
Commit the generated native theme folders alongside their sources.
