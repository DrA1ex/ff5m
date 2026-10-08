# Feather screen guide

Feather is the default local screen of Forge-X. This page describes what it can do. For the choice between Feather, Stock, Guppy Screen, and Headless mode, and for switching between them, see [Screens](SCREEN.md).

## Home screen and navigation

The home screen shows the current job, nozzle and bed state, material, toolhead, network, and the previous print. The main menu provides direct access to printing, printer controls, filament handling, calibration, networking, and settings.

The first touch after the panel dims only wakes the display; it does not activate the control under the finger.

## Local files and print control

Feather can browse G-code stored on the printer or on a connected USB drive. It supports folders, multi-page file lists, refresh, file information, print confirmation, and a recent-print list. Files can be shown as a list or as tiles with the slicer's preview image, and Feather remembers your choice. The preview is also shown on the print screen. For a sharp preview, set the thumbnail size in the slicer as described in [Slicing](SLICING.md#print-preview-thumbnails).

Before starting a file, you can ask Feather to measure a fresh full-bed mesh for that print. If KAMP is enabled, the screen explains that the full mesh will run instead. You can also choose to save the new mesh for future prints. Feather waits until the print succeeds and then asks for confirmation. Confirming saves the mesh permanently and restarts Klipper; postponing leaves it available only for the current session.

During a print, Feather shows progress, elapsed and remaining time, layer and height information. It provides pause, resume, filament change, live Z adjustment, and cancellation with confirmation. While the printer prepares a print, Feather shows the current step, for example `PRINT PREP -> MESH VALIDATION -> HEATING NOZZLE`.

You can also cancel while the printer is preparing a print. Heating waits stop immediately. Homing, probing, and moves are never stopped halfway: the cancel takes effect after the current step, and the cleanup for that step runs. While a cancel is pending, Feather offers **Continue Operation** or an emergency stop (`M112`). Steps that cannot be cancelled safely offer only Continue or `M112`. Details for developers: [Configuration and printing workflows](../openwiki/workflows/configuration-and-printing.md).

When the previous timelapse is still being saved, Feather holds the next print and offers **WAIT**, **CANCEL PRINT**, and **CANCEL TIMELAPSE**. See [Timelapse](CAMERA.md#timelapse).

## Movement, heating, and lighting

Feather provides both step movement and joystick movement. The printer must be in an appropriate idle state, and movement is kept within the configured limits. Separate controls are available for homing, nozzle and bed heating, material preheat, cooldown, the part-cooling fan, and chamber-light brightness. Hardware-active pages provide emergency-stop access.

## Filament and calibration

Material presets are shared with the Forge-X filament macros. Feather remembers the selected material and provides guided loading, unloading, purging, Cold Pull, and filament-change actions, including during a paused print.

The Calibration page includes guided workflows for bed screws, bed mesh, Safe Z, Z offset, extruder feed, PID, and Input Shaper. Follow the instructions on the screen and review the result before saving it. Feather also shows calibration and recovery workflows started from Fluidd, Mainsail, or the console.

## Update notifications

When Moonraker reports that a newer Forge-X version is available, Feather can show the new version and a short, scrollable list of changes while the printer is idle. Select **UPDATE** to start the normal Forge-X OTA update, or **LATER** to hide that version until the printer or Klipper restarts. If a print starts, the notification closes immediately and may return after the printer is idle again.

## Network, settings, and themes

Feather can scan for 2.4 and 5 GHz Wi-Fi networks, enter normal WPA/WPA2-PSK credentials with the on-screen keyboard, configure DHCP Ethernet, and show the live connection state, signal, and IP address. Its Wi-Fi list presents the band, SSID, and signal in separate columns. A saved network is marked in the scan list and reconnects immediately when selected; use **RESET PASSWORD** to replace its credential.

Feather starts without waiting for the saved network. While that startup connection is active, the dashboard shows **CONNECTING** and the Network page can either keep waiting or cancel it before choosing another network. The screen keeps the connection state current while the printer reconnects. Any network change may briefly take the printer offline. If a Wi-Fi change fails, Feather returns to the previous saved Wi-Fi network when possible.

Static addressing and enterprise Wi-Fi still require advanced configuration outside Feather.

Feather Settings provides display brightness, chamber-light level, sound feedback, Forge-X parameters, and theme selection. With the camera enabled, mod settings also include timelapse intervals, parking for photos, and the final photo. Settings that require a Klipper or printer restart are identified before they are applied.

## Limits

Advanced operations such as unrestricted G-code, file deletion, static or enterprise Wi-Fi, and detailed diagnostics remain in Fluidd or Mainsail.

Feather uses the `auto` bed-mesh profile. Recreate or rename the persistent mesh after switching from Stock mode.
