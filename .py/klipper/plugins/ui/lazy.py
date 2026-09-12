## Shared lazy-import primitives for Feather UI modules.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import importlib


def _qualify_module(package, suffix):
    """Return a package-qualified module name without importing it."""
    package = str(package or "")
    suffix = str(suffix)
    return "%s.%s" % (package, suffix) if package else suffix


class LazyModule:
    """Proxy a module and import it on the first attribute access."""

    __slots__ = ("_module_name", "_module")

    def __init__(self, module_name, package=None):
        self._module_name = _qualify_module(package, module_name)
        self._module = None

    def _load(self):
        module = self._module
        if module is None:
            module = importlib.import_module(self._module_name)
            self._module = module
        return module

    def __getattr__(self, name):
        return getattr(self._load(), name)


def resolve_lazy_export(namespace, name, exports, package=None):
    """Load, cache, and return one module-level lazy export.

    ``exports`` maps a public name either to a module suffix (the exported
    attribute keeps the same name) or to ``(module suffix, attribute name)``.
    ``package`` prefixes module suffixes when supplied.
    """
    if name in namespace:
        return namespace[name]
    target = exports.get(name)
    if target is None:
        raise AttributeError(
            "module %r has no attribute %r" %
            (namespace.get("__name__", "<module>"), name))
    if isinstance(target, str):
        module_suffix, attribute = target, name
    else:
        module_suffix, attribute = target
    module = importlib.import_module(_qualify_module(package, module_suffix))
    value = getattr(module, attribute)
    namespace[name] = value
    return value
