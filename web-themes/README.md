# Forge-X Feather web themes

Native **Mainsail 2.19** and **Fluidd 1.37** theme packages in the colors of
Feather's DEFAULT palette. They use each frontend's own theme folder: no
frontend source patches, custom JavaScript, API changes or firmware changes.
The frontend keeps all printer controls and safety behavior. Colors and layout
are designed for dark mode.

## On Forge-X

Normal initialization links `/opt/config/.theme` (Mainsail) and
`/opt/config/.fluidd-theme` (Fluidd) to this bundle only when those paths do
not already exist. Existing directories, files and even dangling links are
preserved. Managed links follow Forge-X updates. Uninstall removes only links
that point to this bundle. A failed theme install does not block printer
startup.

To use these themes while a personal theme is installed, back the personal
theme up and move it aside, then reboot. Select dark mode in the frontend.

## Manual install

1. From the repository root, run `python3 web-themes/build.py` to create
   `web-themes/forge-x-feather-theme.zip` (it is not committed).
2. Back up your existing `.theme` and/or `.fluidd-theme` directories.
3. Extract the ZIP on your computer.
4. In the frontend's config file browser, enable hidden files and upload
   `.theme` (Mainsail) and/or `.fluidd-theme` (Fluidd) into the Moonraker
   **config** directory.
5. Reload the browser and select dark mode. No Klipper or firmware restart is
   needed.

The ZIP contains theme files only and must not replace your printer
configuration. To revert, remove the theme directory or restore your backup.

App and printer names and plotted-series colors stay native frontend settings.
Mainsail uses its native sidebar-logo and favicon support; Fluidd's header mark
is drawn through CSS.

## Build

`build.py` needs only the Python standard library. It writes both hidden theme
folders and the ZIP, and embeds the two font files in the CSS so hosted
frontends fetch no fonts from a wrong printer-relative URL or an external font
service. After editing the CSS, fonts or logo, run the same command from the
repository root and commit the regenerated `.theme` and `.fluidd-theme`
folders with their sources.

## Provenance and licenses

Palette source: `DrA1ex/ff5m` commit
`1ceaff18778b689684bd5c6b61cb7f37cb99e958`,
`.py/klipper/plugins/ui/themes/default.json` (GPLv3). Background `#030607`,
panel `#050c0f`, cyan `#35d9e6`, violet `#b47aff`, border `#295c66`, warning
`#f2c94c`, danger `#ff4d5a`. Secondary web text is brighter than Feather's
`dim` token for readability. The host frontend supplies Roboto; JetBrains Mono
is embedded.

Fonts: JetBrains Mono Regular and Bold from the official
https://github.com/JetBrains/JetBrainsMono repository, `fonts/webfonts/`. Each
package includes the OFL license. The geometric X mark is original to this
theme, not an official Forge-X logo. The wordmark is outlined from JetBrains
Mono Bold. No raster art or generated imagery is used.

The styles are GPLv3; fonts are under the OFL. Native theme support was tested
on unmodified Mainsail and Fluidd. Browser snapshots used isolated sample state
or read-only printer data and issued no motion or heating commands.
