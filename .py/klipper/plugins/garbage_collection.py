# Garbage collection optimizations
#
# Copyright (C) 2025  Branden Cash <ammmze@gmail.com>
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# Backport of upstream Klipper commit d57fe4395e77b0b6f5eabccf890498049fabbcfe:
# https://github.com/Klipper3d/klipper/commit/d57fe4395e77b0b6f5eabccf890498049fabbcfe
import gc
import logging

class GarbageCollection:
    def __init__(self, config):
        self.printer = config.get_printer()
        # freeze/unfreeze is available in Python 3.7+
        can_freeze = hasattr(gc, 'freeze') and hasattr(gc, 'unfreeze')
        if can_freeze:
            self.printer.register_event_handler("klippy:ready",
                                                self._handle_ready)
            self.printer.register_event_handler("klippy:disconnect",
                                                self._handle_disconnect)

    def _handle_ready(self):
        logging.debug("Running full garbage collection and freezing")
        for n in range(3):
            gc.collect(n)
        gc.freeze()

    def _handle_disconnect(self):
        logging.debug("Unfreezing garbage collection")
        gc.unfreeze()

def load_config(config):
    return GarbageCollection(config)
