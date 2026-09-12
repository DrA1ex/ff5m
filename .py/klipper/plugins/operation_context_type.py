## Configurable operation context type registration.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license


def load_config_prefix(config):
    printer = config.get_printer()
    manager = printer.lookup_object("operation_context", None)
    if manager is None:
        manager = printer.load_object(config, "operation_context")
    return manager.register_context_type(config)
