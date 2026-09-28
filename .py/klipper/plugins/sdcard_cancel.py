# Cancel a held virtual SD print without resuming it.
#
# Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
#
# This file may be distributed under the terms of the GNU GPLv3 license.


class SDCardCancel:
    cmd_SDCARD_CANCEL_FILE_help = "Cancel a loaded SD file, including a paused file"

    def __init__(self, config):
        self.printer = config.get_printer()
        gcode = self.printer.lookup_object("gcode")
        gcode.register_command(
            "SDCARD_CANCEL_FILE", self.cmd_SDCARD_CANCEL_FILE,
            desc=self.cmd_SDCARD_CANCEL_FILE_help)

    def cmd_SDCARD_CANCEL_FILE(self, gcmd):
        sdcard = self.printer.lookup_object("virtual_sdcard")
        if sdcard.is_cmd_from_sd():
            raise gcmd.error("SDCARD_CANCEL_FILE cannot be run from the sdcard")
        sdcard.do_cancel()


def load_config(config):
    return SDCardCancel(config)
