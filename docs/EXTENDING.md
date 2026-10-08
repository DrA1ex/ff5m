# Customizing and extending Forge-X

You can add your own macros, change almost any Forge-X or Klipper setting, show your own dialogs on the Feather screen, run your own programs, and start your own services. None of this requires editing Forge-X's own files, which are replaced when you update.

**Jump to:**
[Where your changes are stored](#where-your-changes-are-stored) ·
[Add or override settings and macros (`user.cfg`)](#add-or-override-settings-and-macros-usercfg) ·
[Your own dialogs on the Feather screen](#your-own-dialogs-on-the-feather-screen) ·
[Run your own programs from G-code](#run-your-own-programs-from-g-code) ·
[Your own startup services](#your-own-startup-services) ·
[Moonraker settings](#moonraker-settings) ·
[Other options](#other-options)

> [!WARNING]
> Custom macros and services run as `root` and can move the printer, heat it, or use up memory. Test them while the printer is idle, and keep the printer's 128 MiB of RAM in mind. If a change stops Klipper from starting, remove it from `user.cfg` or boot through the [Dual Boot recovery menu](DUAL_BOOT.md).

## Where your changes are stored

| What | Where | When it is applied |
| --- | --- | --- |
| Klipper settings and macros | `mod_data/user.cfg` (Fluidd/Mainsail: **Configuration → mod_data**) | After a Klipper restart |
| Moonraker settings | `mod_data/user.moonraker.conf` | After a Moonraker restart |
| Startup services | `/data/.mod/.forge-x/etc/init.d/S*` (over SSH) | At boot |

With the Stock screen, do not use `RESTART` or `SAVE_CONFIG`. Reboot the printer instead (see the [FAQ](FAQ.md#stock-screen-freezes-i-cant-print-anything)).

Files in `mod_data` are kept when you update Forge-X. Make a copy with `TAR_BACKUP` before uninstalling (see [Backup Management](CONFIGURATION.md#backup-management)).

## Add or override settings and macros (`user.cfg`)

`user.cfg` is loaded **last**, after the Forge-X configuration and after the optional `tune_config` settings. Klipper merges sections that appear more than once:

- an option you write in `user.cfg` replaces the earlier value of the same option;
- options you do not write keep their original values;
- a section that does not exist yet is simply added.

This works for any Klipper section, including macros that come with Forge-X. The Forge-X macros are in [`macros/base.cfg`](../macros/base.cfg) and the other files in [`macros/`](../macros/); you can also find them in Fluidd under **Configuration → mod**.

**Create your own macro:**

```ini
[gcode_macro MY_CUSTOM_MACRO]
description: My custom macro
gcode:
  M117 Hello, World!
```

**Change a setting of an existing Forge-X section** (only the listed option changes):

```ini
[gcode_macro _KAMP_Settings]
variable_purge_amount: 60

[temperature_sensor weightValue]
trigger_value: 700
```

**Replace a Forge-X macro:** write a section with the same name and your own `gcode:`. This replaces the whole macro, including its safety checks and anything other Forge-X macros expect from it. Macros whose names start with `_` are internal and may change between Forge-X versions, so check your overrides after each update.

For material presets, see [Material slots](CONFIGURATION.md#material-slots). For the list of Forge-X settings, see [Configuration](CONFIGURATION.md).

## Your own dialogs on the Feather screen

Feather shows standard Klipper action prompts (the same `action:prompt_*` messages that Fluidd and Mainsail use) as a **KLIPPER PROMPT** page. A macro sends the messages with `RESPOND`, one message per line:

| Message | What it does |
| --- | --- |
| `action:prompt_begin <title>` | Starts a dialog with this title. |
| `action:prompt_text <text>` | Adds a line of text. Keep it short, long text is cut off. |
| `action:prompt_button <label>\|<G-code>\|<color>` | Adds a button on its own row. |
| `action:prompt_button_group_start` / `action:prompt_button_group_end` | Puts the buttons between them on one row. |
| `action:prompt_footer_button <label>\|<G-code>\|<color>` | Adds a button to the bottom row. |
| `action:prompt_show` | Shows the dialog. |
| `action:prompt_end` | Closes the dialog. |

Buttons:

- The G-code after the first `|` runs when the button is pressed. If you leave it out, the label is used as the G-code. The G-code itself cannot contain `|`.
- The color is optional: `error`, `warning`, or `secondary` give the button a different style. Anything else, or no color, gives a normal button.
- Pressing a button does **not** close the dialog. Close it with `RESPOND TYPE=command MSG=action:prompt_end` in the button's G-code, or by calling a macro that does this.
- Three button rows are shown per page. If there are more, Feather adds `<` and `>` buttons.
- The titles `Resurrection` and `Cold Pull` are used by Forge-X itself. Do not use them.

**Example: a menu for choosing a lane layout** (this is the kind of dialog one user built to control a multi-material unit):

```ini
[gcode_macro LANE_LAYOUT_MENU]
description: Ask which lane layout to use
gcode:
  RESPOND TYPE=command MSG="action:prompt_begin Lane Layouts"
  RESPOND TYPE=command MSG="action:prompt_text Choose your desired lane layout for this session:"
  RESPOND TYPE=command MSG="action:prompt_button Standard Mix (PLA/PETG/ABS/TPU)|SET_LANE_LAYOUT LAYOUT=standard"
  RESPOND TYPE=command MSG="action:prompt_button 2x PETG / 2x ABS|SET_LANE_LAYOUT LAYOUT=petg_abs"
  RESPOND TYPE=command MSG="action:prompt_button 2x PLA / 2x ABS|SET_LANE_LAYOUT LAYOUT=pla_abs"
  RESPOND TYPE=command MSG="action:prompt_footer_button Cancel|RESPOND TYPE=command MSG=action:prompt_end|secondary"
  RESPOND TYPE=command MSG="action:prompt_show"

[gcode_macro SET_LANE_LAYOUT]
description: Apply the chosen lane layout
gcode:
  {% set layout = params.LAYOUT|default("standard") %}
  RESPOND TYPE=command MSG="action:prompt_end"
  RESPOND MSG="Lane layout: {layout}"
  # Put your own G-code here, for example SET_GCODE_VARIABLE or a call to your MMU macros.
```

Run `LANE_LAYOUT_MENU` from the Fluidd/Mainsail console, from a macro button, or from another macro such as your own start G-code. Forge-X uses the same mechanism for its own dialogs (filament change, USB preparation, uninstall confirmation), so [`macros/base.cfg`](../macros/base.cfg) has more examples.

A script started with `RUN_SHELL_COMMAND` can also show a dialog. Set `linewise: True` for the command (see the next section) and have the script print the lines starting with `// `, for example `// action:prompt_begin My title`.

## Run your own programs from G-code

Define a shell command in `user.cfg`, then run it from any macro or from the console:

```ini
[gcode_shell_command my_script]
command: /opt/config/mod_data/my_script.sh
timeout: 10
verbose: True
```

```gcode
RUN_SHELL_COMMAND CMD=my_script PARAMS="first second"
```

Options:

| Option | Meaning |
| --- | --- |
| `command` | Program to run. |
| `timeout` | Seconds to wait before the process is stopped (default 2). |
| `verbose` | Print the program's output in the console. |
| `mode` | `sync` (default, Klipper waits for the program), `background`, `stream`, `queue`, or `daemon`. The other modes do not block Klipper. |
| `linewise` | Send each output line as a separate message. Needed when the script prints `action:` messages. |
| `debug` | Print extra information about each run. |

Put scripts in `mod_data` so they survive updates. Make them executable with `chmod +x`.

## Your own startup services

Forge-X starts every script named `S<number><name>` from `/etc/init.d` inside the Forge-X environment (the chroot) after its own services, and stops them in reverse order. The path over SSH is `/data/.mod/.forge-x/etc/init.d/`.

```sh
#!/bin/sh
# /data/.mod/.forge-x/etc/init.d/S90my_service
case "$1" in
  start) /opt/bin/my-daemon & ;;
  stop)  killall my-daemon ;;
esac
```

Make the script executable (`chmod +x`). The scripts are not stored in `mod_data`, so uninstalling Forge-X can remove them. Keep a copy elsewhere.

## Moonraker settings

Add your own Moonraker configuration to `mod_data/user.moonraker.conf`, for example:

```cfg
[authorization]
force_logins: true

[notifier my_notifier]
url: http://example.com/notify
events: complete
body: Print finished
```

Restart Moonraker or the printer afterwards. See the [Moonraker documentation](https://moonraker.readthedocs.io/en/latest/configuration/) for all options.

## Other options

- **Linux packages:** install more software with the Entware package manager.
- **Your own screen:** switch to `HEADLESS` mode and run your own display process, or draw with [Typer](TYPER.md). See [Screen Configuration](SCREEN.md).
- **Third-party tools:** HelixScreen is available from its own project. KlipperScreen and Obico may also work. Check memory use with the `MEM` macro first, because they use a lot of RAM.
- **Changing Forge-X itself:** the source is open. See [Contributing](../CONTRIBUTING.md) and [Forge-X Klipper extensions](../openwiki/workflows/klipper-extensions.md).
