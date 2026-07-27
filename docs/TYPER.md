# Typer Command Documentation

`typer` is an advanced utility for drawing on the Flashforge AD5M screen. It
can render text and simple shapes, process batches of commands, and register
touch regions for a custom screen. Feather starts and manages Typer itself, so
normal printing and Feather use do not require this page.

> [!WARNING]
> Do not start another Typer process or write to Feather's display pipe while
> Feather is enabled. Concurrent drawing can corrupt the screen. For a custom
> display, switch to Headless mode and test it before starting a print.

## Overview

Typer draws to the printer screen at a fixed resolution of **800×480**. It is
installed at `/root/printer_data/bin/typer`. To add it to `PATH`, run:

```sh
source /opt/config/mod/.shell/common.sh
```

All coordinates and sizes are pixels. Colours are hexadecimal values; for
example, `ff0000` is red and `00ff00` is green. Use `typer --help` on the
printer for the exact command-line help installed with that version.

## Global Options

| Option | Description | Default |
| --- | --- | --- |
| `--debug` | Print diagnostic output. | Off |
| `--double-buffered`, `-db` | Draw in a back buffer; use `flush` to show the result. | Off |
| `--list-fonts` | List available fonts and exit. | Off |
| `--font-manifest` | Print the JSON font-metrics manifest and exit. | Off |
| `--touch-device <path>` | Read normalized touch input from a Linux input device. | Disabled |
| `--event-pipe <path>` | Write touch events to a named pipe. Requires `--touch-device`. | Disabled |

## Commands

### `text` - Print text at a specified position

Draws text with a chosen font, colour, alignment, scale, and optional wrapping.

| Parameter | Description | Default |
| --- | --- | --- |
| `--pos`, `-p <x> <y>` | Position. Required for a standalone command; a later text command in the same batch may reuse the previous position. | — |
| `--text`, `-t <text>` | Text to draw. | Empty |
| `--color`, `-c <hex>` | Text colour. | White |
| `--bg-color`, `-b <hex>` | Text background colour. | Transparent |
| `--font`, `-f <name>` | Font name. | `Roboto 12pt` |
| `--scale`, `-s <n>` | Integer font scale. | `1` |
| `--h-align`, `-ha <left\|center\|right>` | Horizontal alignment. | `left` |
| `--v-align`, `-va <bottom\|baseline\|middle\|top>` | Vertical alignment. | `baseline` |
| `--max-width <px>` | Maximum rendered width. | Unlimited |
| `--max-height <px>` | Maximum rendered height. | Unlimited |
| `--wrap` | Wrap text within the maximum width and height. | Off |
| `--truncate` | Add an ellipsis when text exceeds the allowed width or height. | Off |

```sh
typer text --pos 400 240 --color ff0000 \
  --font "Roboto Bold 16pt" --text "Hello, World!" \
  --h-align center --v-align middle
```

This draws red text centred at `(400, 240)`.

To constrain a long label:

```sh
typer text --pos 20 20 --text "A long status message" \
  --max-width 300 --max-height 48 --wrap --truncate
```

### `fill` - Fill a region with color

| Parameter | Description | Default |
| --- | --- | --- |
| `--pos`, `-p <x> <y>` | Top-left position. | Required |
| `--size`, `-s <width> <height>` | Rectangle size. | Required |
| `--color`, `-c <hex>` | Fill colour. | Black |

```sh
typer fill --pos 100 100 --size 200 150 --color 00ff00
```

### `stroke` - Draw a rectangle outline

| Parameter | Description | Default |
| --- | --- | --- |
| `--pos`, `-p <x> <y>` | Top-left position. | Required |
| `--size`, `-s <width> <height>` | Rectangle size. | Required |
| `--color`, `-c <hex>` | Outline colour. | White |
| `--line-width`, `-lw <px>` | Outline width. | `1` |
| `--stroke-direction`, `-sd <outer\|middle\|inner>` | Where the outline is drawn relative to the rectangle edge. | `middle` |

```sh
typer stroke --pos 50 50 --size 300 200 --color 0000ff \
  --line-width 3 --stroke-direction outer
```

### `line` - Draw a line

| Parameter | Description | Default |
| --- | --- | --- |
| `--start`, `-s <x> <y>` | Start point. | Required |
| `--end`, `-e <x> <y>` | End point. | Required |
| `--color`, `-c <hex>` | Line colour. | White |
| `--line-width`, `-lw <px>` | Line width. | `1` |

```sh
typer line --start 100 100 --end 300 300 --color ffff00 --line-width 2
```

### `clear` - Clear the screen

```sh
typer clear --color 333333
```

The optional `--color`, `-c <hex>` argument defaults to black.

### `flush` - Flush pending changes

When using `--double-buffered`, call `flush` after drawing to show the pending
changes:

```sh
typer --double-buffered flush
```

## Batches and named pipes

### `batch` - Process multiple commands

`batch` processes multiple commands together. This is useful when a complete
screen should appear at once.

```sh
typer --double-buffered batch \
  --batch clear --color 202020 \
  --batch text --pos 400 240 --text "Ready" \
          --font "JetBrainsMono 20pt" --h-align center \
  --batch flush
```

For a custom display process, Typer can read batches from a named pipe:

```sh
typer --double-buffered batch --pipe /tmp/typer_pipe
```

Then write complete batches to that pipe. Quote text containing spaces and end
the stream with `--end`:

```sh
printf '%s\n' '--batch clear -c 202020' > /tmp/typer_pipe
printf '%s\n' '--batch text -p 400 200 -t "Hello, World!" -ha center' > /tmp/typer_pipe
printf '%s\n' '--batch flush' > /tmp/typer_pipe
printf '%s\n' '--end' > /tmp/typer_pipe
```

## Touch controls

Touch input is optional. Start pipe mode with both a normalized input device
and an event pipe:

```sh
typer --double-buffered \
  --touch-device /dev/input/guppy \
  --event-pipe /tmp/typer-events \
  batch --pipe /tmp/typer_pipe
```

Register a rectangular touch region with `hitbox`:

```sh
typer hitbox --pos 40 380 --size 220 60 --id start_print
```

For a normal tap, the event pipe receives:

```text
tap start_print
```

Use `--continuous` for a held control such as a joystick. It emits begin,
move, periodic stationary heartbeats, and end events with coordinates:

```sh
typer hitbox --pos 300 300 --size 160 160 --id move_xy --continuous
```

```text
touch move_xy begin 380 380
touch move_xy move 410 380
touch move_xy end 410 380
```

Clear all touch regions before drawing a new page:

```sh
typer clear-hitboxes
```

### `button` — draw a button and optionally make it touchable

`button` combines a filled rectangle, border, centred text, and an optional
touch region.

| Parameter | Description | Default |
| --- | --- | --- |
| `--pos`, `-p <x> <y>` | Top-left position. | Required |
| `--size`, `-s <width> <height>` | Button size. | Required |
| `--background <hex>` | Button background. | Black |
| `--border <hex>` | Border colour. | White |
| `--text-color <hex>` | Label colour. | White |
| `--line-width`, `-lw <px>` | Border width. | `2` |
| `--font`, `-f <name>` | Label font. | `Roboto 12pt` |
| `--text`, `-t <text>` | Label text. | Empty |
| `--max-width <px>` | Maximum label width. | Unlimited |
| `--truncate` | Truncate an oversized label. | Off |
| `--id <name>` | Optional tap action ID. | None |
| `--continuous` | Emit held-touch events instead of a tap. Requires `--id`. | Off |

```sh
typer button --pos 40 380 --size 220 60 \
  --background 1769aa --border ffffff --text "Start" --id start_print
```

## Fonts

Run the following command to list the fonts included with the installed Typer:

```sh
typer --list-fonts
```

Fonts include Roboto and JetBrains Mono variants at several sizes. Use the
exact name returned by `--list-fonts` with `--font`.

`--font-manifest` is intended for tools that need font measurements in JSON;
ordinary custom screens normally only need `--list-fonts`.

## Notes

- Use double buffering and a final `flush` for a complete custom page.
- Clear old hitboxes before changing pages so old actions cannot remain active.
- A hitbox ID is a short ASCII action name; Typer reports it but never executes
  printer commands itself.
- Typer needs access to `/dev/fb0`. Test a custom screen while the printer is
  idle and before relying on it for a print.

## Copyright

Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>. Distributed
under the GNU GPLv3 license.
