# Feather UI framework dependency

FF5M vendors the framework physically at `.py/klipper/plugins/ui`. The current
framework release is `framework-v2.0.0` from the `feather-ui-designer`
repository. Printer deployment must not use a symlink, pip installation, or a
Designer checkout.

Add the dependency to a repository that does not yet contain the prefix:

```bash
git subtree add --prefix .py/klipper/plugins/ui \
  <feather-ui-designer-repository> framework-v2.0.0 --squash
```

Update it to a later compatible release:

```bash
git subtree pull --prefix .py/klipper/plugins/ui \
  <feather-ui-designer-repository> framework-v2.x.y --squash
```

Product pages and controllers belong to `.py/klipper/plugins/ff5m_ui`; they
must never be added to the framework subtree. Framework tags contain only the
contents of the canonical `framework/ui` directory.

The low-level renderer transport is framework-owned. FF5M's asynchronous Typer
integration therefore lives in `ui/renderer.py` and `ui/render_worker.py`, not
in a product page/controller. When updating the subtree, carry the render-worker
files from the matching framework release (or reconcile the local transport
patch explicitly); a plain subtree pull must not restore reactor-side FIFO or
process lifecycle code.
