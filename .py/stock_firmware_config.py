#!/usr/bin/env python3
"""Synchronize the stock printer.base.cfg after Forge-X config initialization.

Uses the Qt RCC tables embedded in firmwareExe. Only printer.base.cfg is
migrated. The version stamp is deliberately updated last.
"""
import argparse
import re
import struct
from pathlib import Path

VERSION_PATH_RE = re.compile(r'^\d+\.\d+\.\d+$')
SECTION_RE = re.compile(r'^\s*\[([^\]]+)\]\s*$')
PARAM_RE = re.compile(r'^([ \t]*)([A-Za-z_][\w]*)\s*[:=]\s*(.*?)\s*$')
SAVE_RE = re.compile(r'^#\*#\s*<[^\n]*SAVE_CONFIG')
PAD_START = '# Forge-X stock config padding BEGIN'
PAD_END = '# Forge-X stock config padding END'


def firmware_version_from_path(binary_path):
    """The FlashForge installer names the software directory by its version."""
    version = Path(binary_path).parent.name
    if not VERSION_PATH_RE.fullmatch(version):
        raise ValueError('Invalid stock firmware version directory: %s' % version)
    return version


def qt_config(blob):
    """Extract /res/icon/main/printer.base.cfg from Qt4/5 RCC ELF symbols."""
    if blob[:4] != b'\x7fELF' or blob[4] != 1 or blob[5] != 1:
        raise ValueError('Expected 32-bit little-endian ELF')
    u16 = lambda o: struct.unpack_from('<H', blob, o)[0]
    u32 = lambda o: struct.unpack_from('<I', blob, o)[0]
    shoff, step, count = u32(32), u16(46), u16(48)
    sections = [struct.unpack_from('<IIIIIIIIII', blob, shoff + i * step)
                for i in range(count)]
    # A single ELF section can contain multiple RCC collections; group by
    # consecutive data/name/tree symbols, never by section index.
    symbols = []
    # Look up the few RCC names in the ELF string table first.  Do not
    # decode tens of thousands of unrelated ELF symbols in Python.
    resource_names = (
        b'_ZL16qt_resource_data', b'_ZL16qt_resource_name',
        b'_ZL18qt_resource_struct')
    for section in sections:
        if section[1] != 2 or section[9] == 0:  # SHT_SYMTAB
            continue
        strings_section = sections[section[6]]
        strings = blob[strings_section[4]:strings_section[4] + strings_section[5]]
        symbol_names = {}
        for wanted in resource_names:
            index = 0
            while True:
                index = strings.find(wanted + b'\0', index)
                if index < 0:
                    break
                if index == 0 or strings[index - 1] == 0:
                    symbol_names[index] = wanted.decode('ascii')
                index += 1
        if not symbol_names:
            continue
        for pos in range(section[4], section[4] + section[5], section[9]):
            name_offset = u32(pos)
            name = symbol_names.get(name_offset)
            if name is None:
                continue
            _, addr, size, _, _, idx = struct.unpack_from('<IIIBBH', blob, pos)
            if idx >= len(sections):
                raise ValueError('Invalid RCC symbol section')
            target_section = sections[idx]
            offset = target_section[4] + addr - target_section[3]
            if offset < 0 or offset + size > len(blob):
                raise ValueError('Invalid RCC resource symbol')
            symbols.append((addr, name, blob[offset:offset + size]))
    symbols.sort(key=lambda item: item[0])
    groups = []
    for i in range(len(symbols) - 2):
        triplet = symbols[i:i + 3]
        if [entry[1] for entry in triplet] == [
                '_ZL16qt_resource_data',
                '_ZL16qt_resource_name',
                '_ZL18qt_resource_struct']:
            groups.append({entry[1]: entry[2] for entry in triplet})
    for group in groups:
        data, names, tree = (group[n] for n in
                             ('_ZL16qt_resource_data', '_ZL16qt_resource_name',
                              '_ZL18qt_resource_struct'))
        be16 = lambda b, o: struct.unpack_from('>H', b, o)[0]
        be32 = lambda b, o: struct.unpack_from('>I', b, o)[0]
        def visit(idx, path):
            entry = idx * 14
            if entry + 14 > len(tree):
                raise ValueError('Invalid RCC tree')
            off, flags = be32(tree, entry), be16(tree, entry + 4)
            length = be16(names, off)
            name = names[off + 6:off + 6 + length * 2].decode('utf-16-be')
            full = path + '/' + name
            if flags & 2:
                count, first = be32(tree, entry + 6), be32(tree, entry + 10)
                if count > 20000:
                    raise ValueError('Invalid RCC count')
                for n in range(first, first + count):
                    found = visit(n, full)
                    if found is not None:
                        return found
                return None
            if full not in ('/res/res/icon/main/printer.base.cfg',
                            '/res/icon/main/printer.base.cfg'):
                return None
            off = be32(tree, entry + 10)
            size = be32(data, off)
            raw = data[off + 4:off + 4 + size]
            if flags & 1:
                raise ValueError('Compressed stock Qt resource is unsupported by Python 3.7 installation')
            return raw
        found = visit(0, '')
        if found is not None:
            return found
    raise ValueError('Qt resource printer.base.cfg not found')


def parse_config(text):
    """Parse Klipper sections, params, and multiline values independent of order.

    Record source line ranges so we can edit values in place and preserve
    comments, includes, and unrelated Forge-X options.
    """
    sections = {}
    section = None
    last = None
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if SAVE_RE.match(stripped):
            break
        if not stripped or stripped.startswith('#'):
            continue
        match = SECTION_RE.fullmatch(stripped)
        if match:
            name = match.group(1)
            if name in sections:
                raise ValueError('Duplicate Klipper section: ' + name)
            sections[name] = {'params': {}, 'end': len(lines)}
            if section is not None:
                sections[section]['end'] = index
            section, last = name, None
            continue
        if section is None:
            raise ValueError('Parameter before first section: ' + stripped)
        # A gcode body may contain text resembling a parameter assignment.
        # A deeper indentation is always a continuation of the last parameter.
        leading = len(line) - len(line.lstrip(' \t'))
        if last is not None and leading > last['indent']:
            last['end'] = index + 1
            last['value_lines'].append(stripped)
            continue
        match = PARAM_RE.match(line)
        if match:
            indent, name, value = match.groups()
            if name in sections[section]['params']:
                raise ValueError('Duplicate Klipper parameter: [%s].%s' % (section, name))
            last = {'start': index, 'end': index + 1, 'indent': len(indent),
                    'value_lines': [value], 'raw': line}
            sections[section]['params'][name] = last
        elif last is not None and leading:
            last['end'] = index + 1
            last['value_lines'].append(stripped)
        else:
            raise ValueError('Unsupported Klipper syntax: ' + stripped)
    return sections, lines


def _normalized(entry):
    return '\n'.join(line.strip() for line in entry['value_lines']).rstrip()


def sync_config(current, stock):
    """Overwrite vendor parameters in existing sections only, logging every edit."""
    sections, lines = parse_config(current)
    stock_sections, stock_lines = parse_config(stock)
    edits = []
    changes = []
    for name, section in sections.items():
        vendor = stock_sections.get(name)
        if vendor is None:
            continue  # Preserve Forge-X-only sections.
        new_keys = []
        for key, entry in vendor['params'].items():
            old = section['params'].get(key)
            if old is None:
                new_keys.append((key, entry))
                changes.append(('Added', name, key, None, _normalized(entry)))
            elif _normalized(old) != _normalized(entry):
                # Copy the exact original vendor block (including multiline gcode).
                edits.append((old['start'], old['end'], stock_lines[entry['start']:entry['end']]))
                changes.append(('Updated', name, key, _normalized(old), _normalized(entry)))
        if new_keys:
            insertion = []
            for _, entry in new_keys:
                insertion.extend(stock_lines[entry['start']:entry['end']])
            edits.append((section['end'], section['end'], insertion))
    if not edits:
        return current, changes
    # Reverse source offsets so earlier replacements do not affect later ones.
    for start, end, replacement in sorted(edits, key=lambda x: (x[0], x[1]), reverse=True):
        lines[start:end] = replacement
    return ''.join(lines), changes


def pad_config(text, resource_size):
    """Keep printer.base.cfg bigger than the resource-based recovery threshold."""
    lines = text.splitlines(keepends=True)
    starts = [i for i, line in enumerate(lines) if line.strip() == PAD_START]
    ends = [i for i, line in enumerate(lines) if line.strip() == PAD_END]
    if starts or ends:
        if len(starts) != 1 or len(ends) != 1 or starts[0] >= ends[0]:
            raise ValueError('Invalid Forge-X padding block')
        del lines[starts[0]:ends[0] + 1]
    text = ''.join(lines)
    if text and not text.endswith('\n'):
        text += '\n'
    size_goal = resource_size + 256
    match = re.search(r'(?m)^#\*#\s*<[^\n]*SAVE_CONFIG[^\n]*$', text)
    location = match.start() if match else len(text)
    prefix, suffix = text[:location], text[location:]
    if prefix and not prefix.endswith('\n'):
        prefix += '\n'
    head, tail = PAD_START + '\n', PAD_END + '\n'
    overhead = len((prefix + head + '# \n' + tail + suffix).encode('utf-8'))
    padding_len = max(0, size_goal - overhead)
    return prefix + head + '# ' + 'x' * padding_len + '\n' + tail + suffix


def atomic_write(path, data):
    import os
    import tempfile
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        os.fchmod(fd, path.stat().st_mode & 0o777 if path.exists() else 0o644)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)



def find_firmware_binary(software_dir='/opt/PROGRAM/software'):
    """Resolve the one installed stock firmwareExe, regardless of version folder.

    The vendor installer deletes old version folders after successful md5
    verification. Ignore its staging directory and fail on unexpected
    multiple versions rather than silently choosing an obsolete binary.
    """
    root = Path(software_dir)
    binaries = sorted(path for path in root.glob('*/firmwareExe')
                      if path.is_file() and path.parent.name != 'temp')
    if len(binaries) != 1:
        raise RuntimeError('Expected exactly one installed firmwareExe in %s; found: %s'
                           % (root, ', '.join(map(str, binaries)) or 'none'))
    return binaries[0]


def run(binary_path, config_path, version_path):
    version = firmware_version_from_path(binary_path)
    firmware = Path(binary_path).read_bytes()
    stock = qt_config(firmware).decode('utf-8')
    parse_config(stock)
    version_path = Path(version_path)
    previous_version = version_path.read_text().strip() if version_path.exists() else ''
    config_path = Path(config_path)
    current = config_path.read_text()
    parse_config(current)
    changed = previous_version != version
    if changed:
        print('Stock firmware version: %s -> %s' % (previous_version or '<missing>', version))
        migrated, changes = sync_config(current, stock)
        for action, section, parameter, old, new in changes:
            if action == 'Updated':
                print('Updated [%s].%s: %r -> %r' % (section, parameter, old, new))
            else:
                print('Added [%s].%s: %r' % (section, parameter, new))
        print('Stock migration: %s modified parameters' % len(changes))
    else:
        migrated = current
    padded = pad_config(migrated, len(stock.encode('utf-8')))
    parse_config(padded)
    if padded != current:
        atomic_write(config_path, padded.encode('utf-8'))
        print('Stock config padding: %s -> %s bytes' %
              (len(migrated.encode('utf-8')), len(padded.encode('utf-8'))))
    if changed:
        atomic_write(version_path, (version + '\n').encode('utf-8'))
        print('Updated /root/version: %s -> %s' % (previous_version or '<missing>', version))
    print('Stock config is compatible with firmware %s' % version)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='Migrate and pad stock printer.base.cfg')
    parser.add_argument('--binary', default=None,
                        help='Explicit firmwareExe path (default: auto-detect installed version)')
    parser.add_argument('--config', default='/opt/config/printer.base.cfg')
    parser.add_argument('--version', default='/root/version')
    args = parser.parse_args()
    binary_path = Path(args.binary) if args.binary else find_firmware_binary()
    print('Stock firmware binary: %s' % binary_path)
    run(binary_path, args.config, args.version)


if __name__ == '__main__':
    main()
