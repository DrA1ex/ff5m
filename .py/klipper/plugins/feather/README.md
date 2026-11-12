# Feather plugin layout

The top-level `feather_screen.py` file is the Klipper entry point. Internal
Feather implementation lives in this package so the plugin root stays focused
on loadable Klipper extras.

- `calibration/`: reusable Z-offset, extruder, and input-shaper calibration implementation.
- `control/`: joystick planning and low-latency motion helpers.
- `features/`: lazily loaded product features and the feature host.
- `network/`: netd protocol, transport, client, and network pages.
- `screen/`: screen controls, keyboard/pagination helpers, and page groups.
- `screen/pages/`: page behavior grouped by user-facing responsibility.
- `settings/`: mod-settings metadata and presentation helpers.
- `testing/`: compatibility/test support used by the on-printer UI suite.

Shared domain helpers that do not belong to one of those subpackages remain at
this level (`cpu_usage.py`, `files.py`, `gcode_info.py`, `materials.py`,
`model_preview.py`, `previews.py`, `safety.py`, and `update_notification.py`).
