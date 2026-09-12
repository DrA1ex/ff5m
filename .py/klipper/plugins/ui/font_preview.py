"""Project-owned browser preview policy for generated font metrics.

The runtime never imports this module. The external Designer loads it only for
the selected project, keeping browser-specific defaults out of Typer output and
out of the printer runtime path. Keys may name an exact face or a face family
without the trailing ``<size>pt`` suffix.
"""


# Typer rasterizes the point-labelled faces for the 160 DPI printer
# framebuffer. Designer uses this value only to choose the browser glyph em
# size; measured advances and line spacing still come from font_metrics.json.
FONT_PREVIEW_DPI = 160


_MONOSPACE_FALLBACK = (
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace")

FONT_PREVIEW = {
    "JetBrainsMono": {
        "family": "JetBrains Mono",
        "weight": 500,
        "fallback": _MONOSPACE_FALLBACK,
    },
    "JetBrainsMono Bold": {
        "family": "JetBrains Mono",
        "weight": 700,
        "fallback": _MONOSPACE_FALLBACK,
    },
    "Roboto": {
        "family": "Roboto",
        "weight": 500,
        "fallback": "Arial, Helvetica, sans-serif",
    },
    "Roboto Bold": {
        "family": "Roboto",
        "weight": 700,
        "fallback": "Arial, Helvetica, sans-serif",
    },
}

# TODO: Replace this temporary project-side metadata with a separately
# overridable Typer/Designer contract once that can be done without modifying
# the generated ``font_metrics.json`` file.
