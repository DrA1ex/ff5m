# Customizing and extending Forge-X

You can add your own macros, change almost any Forge-X or Klipper setting, show your own dialogs on the Feather screen, add your own Klipper modules, run your own programs, and start your own services. None of this requires editing Forge-X's own files, which are replaced when you update.

**Jump to:**
[Where your changes are stored](#where-your-changes-are-stored) ·
[Add or override settings and macros (`user.cfg`)](#add-or-override-settings-and-macros-usercfg) ·
[Your own dialogs on the Feather screen](#your-own-dialogs-on-the-feather-screen) ·
[Your own Klipper packages](#your-own-klipper-packages) ·
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
| Your own Klipper packages (Python modules) | `mod_data/plugins/<package>/` | After a printer reboot |
| Moonraker settings | `mod_data/user.moonraker.conf` | After a Moonraker restart |
| Startup services | `/data/.mod/.forge-x/etc/init.d/S*` (over SSH) | At boot |

The normal `RESTART`, `SAVE_CONFIG`, and `FIRMWARE_RESTART` commands work with every screen, including Stock (see [Klipper restart and saving](SCREEN.md#klipper-restart-and-saving)).

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

Feather shows standard Klipper action prompts (the same `action:prompt_*` messages that Fluidd and Mainsail use) as a modal dialog. A macro sends the messages with `RESPOND`, one message per line:

| Message | What it does |
| --- | --- |
| `action:prompt_begin <title>` | Starts a dialog with this title. |
| `action:prompt_text <text>` | Adds a line of text. Text wraps, and longer dialogs have page controls. |
| `action:prompt_button <label>\|<G-code>\|<color>` | Adds a button on its own row. |
| `action:prompt_button_group_start` / `action:prompt_button_group_end` | Puts the buttons between them on one row. |
| `action:prompt_footer_button <label>\|<G-code>\|<color>` | Adds a button to the bottom row. |
| `action:prompt_show` | Shows the dialog. |
| `action:prompt_end` | Closes the dialog. |

Buttons:

- The G-code after the first `|` runs when the button is pressed. If you leave it out, the label is used as the G-code. The G-code itself cannot contain `|`.
- The color is optional: `error`, `warning`, or `secondary` give the button a different style. Anything else, or no color, gives a normal button.
- Pressing a button does **not** close the dialog. Close it with `RESPOND TYPE=command MSG=action:prompt_end` in the button's G-code, or by calling a macro that does this.
- Button labels and rows wrap when needed. Longer dialogs have page controls; large footer groups can continue in the paged body.
- The titles `Resurrection` and `Previous timelapse` are used by Forge-X's own dialogs. Do not use them for your dialogs.

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

## Your own Klipper packages

Since Forge-X 1.4.2 you can add your own Klipper Python modules (extras), for example a driver for extra hardware or a plugin from another project, and even replace stock Klipper modules. Each package is a folder in `mod_data/plugins/` (Fluidd/Mainsail: **Configuration → mod_data**; over SSH: `/opt/config/mod_data/plugins/`). Forge-X links it into Klipper at boot, and it survives Forge-X updates.

A package can contain any of these parts:

```text
mod_data/plugins/
└── hello/                    # one folder per package
    ├── config.cfg            # Klipper configuration for the package (optional)
    ├── plugins/
    │   └── hello_world.py    # new Klipper modules, loaded like files in klippy/extras/
    └── patches/
        └── extras/fan.py     # replacements for existing Klipper modules (advanced)
```

**Example: a minimal package with a new G-code command.**

`mod_data/plugins/hello/plugins/hello_world.py`:

```python
class HelloWorld:
    def __init__(self, config):
        self.message = config.get('message', 'Hello!')
        gcode = config.get_printer().lookup_object('gcode')
        gcode.register_command('HELLO_WORLD', self.cmd_HELLO_WORLD,
                               desc="Print a greeting")

    def cmd_HELLO_WORLD(self, gcmd):
        gcmd.respond_info(self.message)

def load_config(config):
    return HelloWorld(config)
```

`mod_data/plugins/hello/config.cfg`:

```ini
[hello_world]
message: Hello from my package
```

Reboot the printer, then run `HELLO_WORLD` in the console.

How it works:

- **Apply changes with a reboot.** After adding, changing, or removing a package, reboot the printer while it is idle. A Klipper `RESTART` is not enough, because it does not relink the Python files.
- **Keep the package's configuration in its `config.cfg`**, not in `user.cfg`. Then disabling the package also removes its configuration, and Klipper does not fail on a section whose module is gone. `config.cfg` may include other files from the same folder, for example `[include settings.cfg]`.
- **Disable a package** by creating an empty file named `disabled` in its folder and rebooting. Delete the file and reboot to enable it again.
- **`patches/`** follows the paths inside `klippy/`, for example `extras/fan.py` or `toolhead.py`. The original file is kept and restored when you remove the patch. Forge-X's own patches are protected: replacing them needs the experimental `user_plugins_override_patches` option.
- **Conflicts.** If a package has invalid paths, or tries to replace a Forge-X module or a file that another package already replaces, Forge-X skips the whole package and writes an error to `mod_data/log/init.log`. Other packages still load.
- **Errors in your own code** can still stop Klipper from starting. Disable the package (or delete it) and reboot; if the printer is not reachable, use the [Dual Boot recovery menu](DUAL_BOOT.md).
- **Files copied with their original timestamps** (`cp -p`, `rsync -t`, archive extraction) can make Python keep using the old cached code if the size did not change. Run `touch` on the file or delete the `__pycache__` folder next to it, then reboot.
- `mod_data/plugins.cfg` is generated by Forge-X; do not edit it. `TAR_BACKUP` includes your packages.
- Before downgrading to a Forge-X version older than 1.4.2, disable packages that contain `patches/` and reboot once, so the original Klipper files are back in place.

The full rules (naming, ordering between packages, uninstall behavior) are in [User Klipper plugins](../openwiki/workflows/user-klipper-plugins.md).

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

A synchronous command exposes its exit code as `printer['gcode_shell_command my_script'].returncode`. It is `None` before completion or after a timeout.

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
