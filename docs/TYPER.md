# Typer Command Documentation

`typer` is a utility for drawing on the Flashforge AD5M screen, supporting text rendering, shapes, and batch processing. Below is a detailed description of its commands, parameters, and usage examples.

## Overview

The program interacts with the framebuffer device (`/dev/fb0`) to render text and graphics. It supports the bundled Roboto and JetBrains Mono fonts and provides commands for drawing text, filling rectangles, stroking rectangles, drawing lines, clearing the screen, and flushing changes in double-buffered mode.

The utility is located at `/root/printer_data/bin/typer`.   
To add it to your `PATH`, use the following command:   
```
source /opt/config/mod/.shell/common.sh
```

The deployed ARM build uses `-Os`, function/data sections, linker garbage
collection, and stripped symbols. This reduces its binary and executable-page
footprint without changing the command protocol. On the reference printer its
idle memory is approximately 1.8 MiB PSS / 1.6 MiB private memory. Raw RSS is
about 2.2 MiB because it also counts shared clean libc/libstdc++ pages; use PSS
when checking the 2 MiB Typer budget.

## Global Options

| Option | Description | Type | Default |
|--------|-------------|------|---------|
| `--debug` | Enable debug output | Flag | `false` |
| `--double-buffered`, `-db` | Enable framebuffer-backed double buffering with heap fallback | Flag | `false` |
| `--list-fonts` | List loaded fonts and exit | Flag | `false` |
| `--touch-device` | Normalized Linux input device used in pipe mode | string | `""` |
| `--event-pipe` | FIFO receiving `tap <action-id>` events | string | `""` |

## Commands

### `text` - Print text at a specified position

Renders text with customizable font, color, alignment, and scale.

| Parameter | Description | Type | Default | Required |
|-----------|-------------|------|---------|----------|
| `--pos`, `-p` | Position (x, y) | 2 integers | None | Yes |
| `--text`, `-t` | Text to display | string | None | Yes |
| `--color`, `-c` | Text color (hex RGB) | uint32_t | `0xffffffff` (white) | No |
| `--bg-color`, `-b` | Background color (hex RGB) | uint32_t | `0x00000000` (transparent) | No |
| `--font`, `-f` | Font name | string | `Roboto 12pt` | No |
| `--scale`, `-s` | Font scale | integer | `1` | No |
| `--h-align`, `-ha` | Horizontal alignment (`left`, `center`, `right`) | string | `left` | No |
| `--v-align`, `-va` | Vertical alignment (`bottom`, `baseline`, `middle`, `top`) | string | `baseline` | No |
| `--max-width` | Maximum rendered width in pixels | integer | `0` | No |
| `--max-height` | Maximum rendered height in pixels | integer | `0` | No |
| `--wrap` | Wrap text within `max-width` and `max-height` | flag | off | No |
| `--truncate` | Append an ellipsis when text exceeds its width or wrapped height | flag | off | No |

**Example:**
```bash
typer text --pos 400 240 --color ff0000 --font "Roboto Bold 16pt" --text "Hello, World!" --h-align center --v-align middle
```
Draws "Hello, World!" in red, centered at (400, 240) using RobotoBold16pt font.

Wrapping and truncation use the selected font's actual glyph metrics:

```bash
typer text --pos 400 160 --font "JetBrainsMono 12pt" --h-align center \
  --v-align middle --max-width 640 --max-height 100 --wrap --truncate \
  --text "A long status message that must stay inside the dialog"

typer text --pos 20 400 --font "JetBrainsMono 8pt" \
  --max-width 240 --truncate --text "A very long filename.gcode"
```

`--wrap` requires both positive limits. Add `--truncate` to put an ellipsis on
the last visible wrapped line when the text exceeds `max-height`. Without
`--wrap`, `--truncate` keeps the text on one line and requires a positive
`--max-width`.

In batch/pipe mode, you can continue text from the last position by omitting the `--pos` parameter in subsequent `text` commands.
```bash
typer batch \
    --batch text --pos 400 240 --text "Hello, " \
    --batch text --text "World!"
```

### `fill` - Fill a region with color

Fills a rectangular area with a specified color.

| Parameter | Description | Type | Default | Required |
|-----------|-------------|------|---------|----------|
| `--pos`, `-p` | Top-left position (x, y) | 2 integers | None | Yes |
| `--size`, `-s` | Width and height (w, h) | 2 integers | None | Yes |
| `--color`, `-c` | Fill color (hex RGB) | uint32_t | `0x000000` (black) | No |

**Example:**
```bash
typer fill --pos 100 100 --size 200 150 --color 00ff00
```
Fills a 200x150 rectangle at (100, 100) with green.

### `stroke` - Draw a rectangle outline

Draws a rectangular outline with customizable line width and stroke direction.

| Parameter | Description | Type | Default | Required |
|-----------|-------------|------|---------|----------|
| `--pos`, `-p` | Top-left position (x, y) | 2 integers | None | Yes |
| `--size`, `-s` | Width and height (w, h) | 2 integers | None | Yes |
| `--color`, `-c` | Stroke color (hex RGB) | uint32_t | `0xffffff` (white) | No |
| `--line-width`, `-lw` | Line width | uint8_t | `1` | No |
| `--stroke-direction`, `-sd` | Stroke direction (`outer`, `middle`, `inner`) | string | `middle` | No |

**Example:**
```bash
typer stroke --pos 50 50 --size 300 200 --color 0000ff --line-width 3 --stroke-direction outer
```
Draws a blue 3-pixel-wide outline around a 300x200 rectangle at (50, 50).

### `line` - Draw a line

Draws a line between two points.

| Parameter | Description | Type | Default | Required |
|-----------|-------------|------|---------|----------|
| `--start`, `-s` | Start position (x, y) | 2 integers | None | Yes |
| `--end`, `-e` | End position (x, y) | 2 integers | None | Yes |
| `--color`, `-c` | Line color (hex RGB) | uint32_t | `0xffffff` (white) | No |
| `--line-width`, `-lw` | Line width | uint8_t | `1` | No |

**Example:**
```bash
typer line --start 100 100 --end 300 300 --color ffff00 --line-width 2
```
Draws a yellow 2-pixel-wide line from (100, 100) to (300, 300).

### `clear` - Clear the screen

Clears the entire screen with a specified color.

| Parameter | Description | Type | Default | Required |
|-----------|-------------|------|---------|----------|
| `--color`, `-c` | Clear color (hex RGB) | uint32_t | `0x000000` (black) | No |

**Example:**
```bash
typer clear --color 333333
```
Clears the screen with a dark gray color.

### `flush` - Flush pending changes

Flushes changes to the screen in double-buffered mode.

| Parameter | Description | Type | Default | Required |
|-----------|-------------|------|---------|----------|
| None | - | - | - | - |

**Example:**
```bash
typer --double-buffered flush
```
Flushes pending changes to the screen.

### `hitbox` and `clear-hitboxes` - Interactive display list

`hitbox` associates a rectangular screen region with a short ASCII action ID.
`clear-hitboxes` removes the regions from the previous page. Hitboxes do not
execute commands; a matching touch writes `tap <action-id>` to `--event-pipe`.
Add `--continuous` for controls that need pointer coordinates while held. These
regions write `touch <action-id> begin|move|end <x> <y>`. A stationary press
emits a `move` heartbeat every 100 ms so the consumer can detect a lost input
stream and stop safely.

```bash
typer --double-buffered \
  --touch-device /dev/input/guppy \
  --event-pipe /tmp/feather-events \
  batch --pipe /tmp/typer

# Sent as part of a batch frame:
--batch clear-hitboxes
--batch hitbox --id print.pause --pos 100 300 --size 250 100
--batch hitbox --id move.joy.xy --pos 25 75 --size 430 285 --continuous
```

Action IDs are limited to 1–64 characters from `A-Z`, `a-z`, `0-9`, `_`, `.`,
`:`, and `-`. Later hitboxes take precedence when regions overlap.
Continuous delivery is a transport feature only: `typer` never interprets the
action or controls printer motion itself.

### `batch` - Process multiple commands

Executes multiple commands in a batch, either via command-line arguments or a named pipe.

| Parameter | Description | Type | Default | Required |
|-----------|-------------|------|---------|----------|
| `--pipe` | Named pipe to read batches | string | `""` | No |
| `--batch` | Batch command arguments | remaining | None | No |

**Example (Command-line Batch):**
```bash
typer batch \
    --batch clear --color ff0000 \
    --batch text --pos 400 240 --text "Batch Test" --font "JetBrainsMono 20pt" --h-align center \
    --batch flush
```
Clears the screen red, draws "Batch Test" centered, and flushes changes.

**Example (Pipe Mode):**
```bash
typer batch --pipe /tmp/typer_pipe
```
In another terminal:
```bash
echo -e "--batch clear -c ff0000" > /tmp/typer_pipe
echo -e "--batch text -p 400 200 -t \"Hello, World.\"" > /tmp/typer_pipe
echo -e "--batch flush" > /tmp/typer_pipe
echo -e "--end" > /tmp/typer_pipe
```
Processes commands via the named pipe `/tmp/typer_pipe`.

## Fonts

Supported fonts include variants of Roboto and JetBrains Mono at different sizes and weights. Use `--list-fonts` to view all available fonts.

**Example:**
```bash
typer --list-fonts
```
Lists all loaded fonts, e.g., `Roboto 12pt` and `JetBrainsMono Bold 16pt`.

## Notes

- Colors are specified in hexadecimal RGB format (e.g., `ff0000` for red).
- The screen resolution is fixed at 800x480 pixels.
- In pipe mode, use `--end` to terminate the pipe session.
- Double buffering (`--double-buffered`) requires explicit `flush` commands to update the screen.
- Double-buffered mode first uses the non-visible second page of `/dev/fb0` as
  its backbuffer for both standalone and pipe-mode runs. It does not page-flip;
  `flush` copies the dirty rectangle to the visible page. Unsupported geometry
  or mapping failures are reported on stderr and use a heap backbuffer instead.
  A non-blocking process lock prevents concurrent `typer -db` instances from
  sharing the scratch page; later instances also fall back to heap with a log.
  On the Forge-X framebuffer this avoids a persistent 1,536,000-byte anonymous
  allocation without changing the visible-page or dirty-flush behavior.
- The program requires access to `/dev/fb0` and sufficient permissions.
- Feather obtains calibrated 800×480 touch coordinates from `ts_uinput` through
  `/dev/input/guppy`; `typer` does not duplicate tslib calibration.

## Copyright

Copyright (C) 2025, Alexander K <https://github.com/drA1ex>. Distributed under the GNU GPLv3 license.
