## Package-safe access to the shared Feather lazy-export helper.

import importlib


_root_package = (__package__ or "").rpartition(".")[0]
_module_name = "%s.ui.lazy" % _root_package if _root_package else "ui.lazy"
resolve_lazy_export = importlib.import_module(
    _module_name).resolve_lazy_export

__all__ = ("resolve_lazy_export",)
