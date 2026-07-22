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
