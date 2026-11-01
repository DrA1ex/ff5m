#!/usr/bin/env python3
"""Move the probe offset out of the stock include so SAVE_CONFIG can own it."""

import argparse
import os
from pathlib import Path
import re
import tempfile

HEADER = re.compile(r'^\s*\[([^]]+)\]\s*(?:[#;].*)?$')
OFFSET = re.compile(r'^\s*z_offset\s*[:=]', re.I)


def offset_lines(text):
    section = None
    for index, line in enumerate(text.splitlines(keepends=True)):
        match = HEADER.match(line.rstrip('\r\n'))
        if match:
            section = match.group(1).strip()
        elif section == 'probe' and OFFSET.match(line):
            yield index, line


def has_saved_offset(text):
    # SAVE_CONFIG's block is commented on disk, but supplies active settings.
    saved = []
    in_saved = False
    for line in text.splitlines(keepends=True):
        if line.startswith('#*# <') and 'SAVE_CONFIG' in line:
            in_saved = True
        elif in_saved and line.startswith('#*#'):
            saved.append(line[3:].lstrip(' '))
    return bool(list(offset_lines(''.join(saved))))


def atomic_write(path, text):
    fd, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        os.fchmod(fd, path.stat().st_mode & 0o777)
        with os.fdopen(fd, 'w') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def migrate(config_dir):
    root = Path(config_dir)
    main_path, base_path = root / 'printer.cfg', root / 'printer.base.cfg'
    main, base = main_path.read_text(), base_path.read_text()
    offsets = list(offset_lines(base))
    if not offsets:
        return False
    if len(offsets) != 1:
        raise ValueError('Expected one [probe] z_offset in printer.base.cfg')
    index, value = offsets[0]
    # Existing main-file and autosaved calibrations take precedence over stock.
    if not list(offset_lines(main)) and not has_saved_offset(main):
        lines = main.splitlines(keepends=True)
        insert = next((i + 1 for i, line in enumerate(lines)
                       if (HEADER.match(line.rstrip('\r\n')) and
                           HEADER.match(line.rstrip('\r\n')).group(1).strip() == 'probe')), None)
        if insert is not None:
            lines.insert(insert, value.rstrip('\r\n') + '\n')
            updated = ''.join(lines)
        else:
            marker = next((i for i, line in enumerate(lines)
                           if line.startswith('#*# <') and 'SAVE_CONFIG' in line), len(lines))
            before = ''.join(lines[:marker]).rstrip('\r\n')
            updated = before + '\n\n[probe]\n' + value.rstrip('\r\n') + '\n\n' + ''.join(lines[marker:])
        # Write the destination first. An interrupted migration can be rerun
        # without losing the only copy of a user's calibration.
        atomic_write(main_path, updated)
    lines = base.splitlines(keepends=True)
    del lines[index]
    atomic_write(base_path, ''.join(lines))
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-dir', default='/opt/config')
    if migrate(parser.parse_args().config_dir):
        print('// Moved probe Z-offset ownership to printer.cfg for SAVE_CONFIG')
