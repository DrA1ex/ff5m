## MD5 checking support for gcode
##
## Copyright (C) 2025-2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

import hashlib
import os

READ_BLOCK_SIZE = 64 * 1024


class MD5Checker:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.gcode = self.printer.lookup_object('gcode')

        self.delete_invalid_files = config.getboolean("delete_invalid", True)

        self.gcode.register_command("CHECK_MD5", self.cmd_CHECK_MD5)

        self.printer.register_event_handler("klippy:ready", self._init)

    def _init(self):
        self.vcard = self.printer.lookup_object("virtual_sdcard")
        self.params = self.printer.lookup_object("mod_params")

    def calculate_md5(self, file_path):
        hash_md5 = hashlib.md5()
        reactor = self.printer.get_reactor()
        next_yield = reactor.monotonic() + 0.02

        with open(file_path, "rb") as f:
            f.readline()  # Skip the first line containing the checksum.
            for chunk in iter(lambda: f.read(READ_BLOCK_SIZE), b""):
                hash_md5.update(chunk)

                if reactor.monotonic() >= next_yield:
                    reactor.pause(reactor.NOW)
                    next_yield = reactor.monotonic() + 0.02

        return hash_md5.hexdigest()

    def check_md5(self, file_path, delete):
        self.gcode.respond_raw("INFO: Begin MD5 check...")

        if os.path.isdir(file_path) or not os.path.exists(file_path):
            self.gcode.respond_raw(f"!! ERROR: File doesn't exists: {file_path}")
            return False

        try:
            # Inspect only the header; G-code body bytes need no text decoding.
            with open(file_path, 'rb') as f:
                first_line = f.readline().strip()
            if not first_line.startswith(b"; MD5:"):
                self.gcode.respond_raw("WARNING: No MD5 checksum found in G-code.")
                return True

            expected_md5 = first_line.split(b':', 1)[1].decode("ascii", errors="replace")
            calculated_md5 = self.calculate_md5(file_path)
        except OSError as exc:
            self.gcode.respond_raw(f"!! ERROR: Unable to read G-code for MD5 check: {exc}")
            return False

        if expected_md5 != calculated_md5:
            self.gcode.respond_raw(f"!! ERROR: MD5 checksum mismatch: {expected_md5} != {calculated_md5}")
            if delete and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    self.gcode.respond_raw(f"INFO: File {file_path} deleted!")

                    bmp_file_path = file_path.rsplit('.', maxsplit=1)[0] + ".bmp"
                    if os.path.exists(bmp_file_path):
                        os.remove(bmp_file_path)
                except OSError as exc:
                    self.gcode.respond_raw(f"!! ERROR: Unable to delete invalid G-code files: {exc}")

            return False

        self.gcode.respond_raw("INFO: MD5 checksum correct!")
        return True

    def cmd_CHECK_MD5(self, gcmd):
        filename = gcmd.get("FILENAME", None)
        delete = gcmd.get("DELETE", str(self.delete_invalid_files)) == "True"

        if not filename:
            filename = self.vcard.file_path() or ""

        if not self.check_md5(filename, delete):
            if self.params.variables['display'] != 0:
                self.gcode.run_script_from_command('CANCEL_PRINT REASON="MD5 Mismatch"')
            else:
                self.gcode.run_script_from_command('CANCEL_PRINT')

            raise gcmd.error("MD5 check failed. Print cancelled!")


def load_config(config):
    return MD5Checker(config)
