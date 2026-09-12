# Shared screen themes

These runtime theme files are shared by `splash` and `logged`. Both programs
use the same `--theme <name>` and repeatable `--themes-path <directory>` lookup
semantics. Search paths are layered in argument order; the last directory
containing `<name>.json` wins. Relative paths are resolved against the process
working directory.

The build does not copy this directory next to either binary. Packaging or the
runtime environment decides where theme files are installed. Both programs
default to searching `./themes`.

If `--theme` is omitted, or the selected theme is absent from every configured
directory, each program uses its built-in fallback. A file that is found but
invalid is an error and does not fall through to a lower-priority directory.

Every shipped theme uses `schema_version: 1`. The loader rejects missing or unsupported schema versions instead of guessing compatibility.

Every shipped theme contains roles for both renderers. The bundled splash/logged
role assignments are adapted from the corresponding Forge-X UI reference palette;
except for the intentionally specialized CLASSIC and AUBERGINE treatments described
below, the adaptation reuses colors from that reference palette rather than inventing
additional effect colors. Runtime override themes are not subject to this design rule.

Splash roles:

- `background` — uniform screen background, painted once at splash startup;
- `text` — main logo face;
- `text_highlight` — face highlight;
- `accent` — authored accent cells;
- `wire` — wire/edge layer;
- `shadow` — depth/shadow layer;
- `beam` — diagonal light sweep;
- `glitch_primary`, `glitch_secondary` — chromatic glitch channels;
- `glitch_cut` — cut/erase color used by glitch;
- `subtitle` — subtitle text.

Logged roles:

- `background` — the same background as splash;
- `debug` — DEBUG messages;
- `info` — INFO messages;
- `warn` — WARN messages;
- `error` — ERROR messages;
- `uptime` — boot uptime counter.

Colors accept `RRGGBB` or `#RRGGBB`. Colors are not brightness-restricted.

`aubergine.json` is derived from the project Ubuntu-style AUBERGINE palette: warm gray `DEDBD8`, Ubuntu orange `E95420`, aubergine `5E2750`/`772953`/`2C001E`, and dark gray `2C2C2C`. Splash and logged roles are mapped only from that source palette.

`classic.json` intentionally follows a Windows 95 visual vocabulary: system
gray background, navy and teal accents, bright beveled edges, dark body text,
and restrained red/ochre status colors.
