#!/usr/bin/env python3
## Check and synchronize the Klipper replacement files against the fork.
##
## Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
##
## This file may be distributed under the terms of the GNU GPLv3 license

"""Verify or synchronize .py/klipper/patches/** with the Klipper fork.

Verification never writes files. Synchronization uses the exact fork commit as
its source of truth: it repairs locally changed/missing patches, imports new
patches changed since the manifest's commit, and removes tracked source patches
removed in the fork. Only Klipper overlay files (.py/.so) are considered.
Binaries absent from the fork are kept locally (as before).

    python3 tests/verify_klipper_fork.py
    python3 tests/verify_klipper_fork.py --fork https://github.com/DrA1ex/klipper-ad5m
    python3 tests/verify_klipper_fork.py --sync  # repair local drift at recorded commit
    python3 tests/verify_klipper_fork.py --update --commit <sha> --dry-run
    python3 tests/verify_klipper_fork.py --update --commit <sha>
    python3 tests/verify_klipper_fork.py --update --commit <sha> --yes

--update (also --sync) now downloads and synchronizes files, not just hashes.
A plan and a warning about losing local edits are shown before confirmation.
"""

import argparse
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile


ROOT = pathlib.Path(__file__).resolve().parents[1]
PATCHES = ROOT / '.py' / 'klipper' / 'patches'
MANIFEST = ROOT / 'docs' / 'klipper-ad5m-manifest.json'
FORK_URL = 'https://github.com/DrA1ex/klipper-ad5m'
BASE_COMMIT = 'e02b725602067a2cd098a62be9a4bb10fc74a9bd'
STOCK_COMMIT = 'c6e78dffa3d16a25710942a379b019debbacd5af'
SKIPPED_PARTS = {'__pycache__'}
SUFFIXES = ('.py', '.so')


def sha256(path):
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


def digest(content):
    return hashlib.sha256(content).hexdigest()


def overlay_files():
    """Return {path relative to patches/: path} for the shipped overlay."""
    files = {}
    if not PATCHES.exists():
        return files
    for path in sorted(PATCHES.rglob('*')):
        if not path.is_file() or SKIPPED_PARTS & set(path.parts):
            continue
        if path.suffix not in SUFFIXES:
            continue
        files[path.relative_to(PATCHES).as_posix()] = path
    return files


def manifest_entry(name, checksum):
    return {'ff5m': '.py/klipper/patches/' + name[len('klippy/'):],
            'sha256': checksum}


def build_manifest(commit, entries=None, previous=None):
    """Build manifest from disk, or from {klippy/path: sha256} for a plan."""
    if entries is None:
        entries = {'klippy/' + rel: sha256(path)
                   for rel, path in overlay_files().items()}
    sources, binaries = {}, {}
    for name, checksum in sorted(entries.items()):
        destination = sources if name.endswith('.py') else binaries
        destination[name] = manifest_entry(name, checksum)
    previous = previous or {}
    return {
        'fork': previous.get('fork', FORK_URL),
        'base_commit': previous.get('base_commit', BASE_COMMIT),
        'stock_commit': previous.get('stock_commit', STOCK_COMMIT),
        'commit': commit,
        'sources': sources,
        'binaries': binaries,
    }


def load_manifest():
    return json.loads(MANIFEST.read_text(encoding='utf-8'))


def validate_name(name):
    """Manifest and Git paths must never escape .py/klipper/patches/."""
    parts = pathlib.PurePosixPath(name).parts
    if (not isinstance(name, str) or not name.startswith('klippy/')
            or len(parts) < 2 or parts[0] != 'klippy'
            or '..' in parts or '.' in parts
            or '\\' in name or not name.endswith(SUFFIXES)):
        raise ValueError('Unsafe or unsupported overlay path: %r' % name)
    path = PATCHES.joinpath(*parts[1:])
    if PATCHES.resolve() not in path.resolve().parents:
        raise ValueError('Overlay path resolves outside patches/: %r' % name)
    return path


def validate_manifest(manifest):
    for key in ('sources', 'binaries'):
        if not isinstance(manifest.get(key), dict):
            raise ValueError('Manifest has no valid %s mapping' % key)
        for name, entry in manifest[key].items():
            validate_name(name)
            if ((key == 'sources') != name.endswith('.py')):
                raise ValueError('Wrong manifest category: ' + name)
            if entry.get('ff5m') != manifest_entry(name, '')['ff5m']:
                raise ValueError('Unexpected manifest path: ' + name)
            if not re.fullmatch('[0-9a-f]{64}', entry.get('sha256', '')):
                raise ValueError('Invalid manifest checksum: ' + name)
    if set(manifest['sources']) & set(manifest['binaries']):
        raise ValueError('Duplicate manifest entries')


def check_manifest(manifest):
    """Return differences between the local overlay and the manifest."""
    validate_manifest(manifest)
    problems = []
    listed = dict(manifest['sources'])
    listed.update(manifest['binaries'])
    shipped = {'klippy/' + rel: path for rel, path in overlay_files().items()}
    for name in sorted(set(shipped) - set(listed)):
        problems.append('not in the manifest: ' + name)
    for name in sorted(set(listed) - set(shipped)):
        problems.append('in the manifest but not shipped: ' + name)
    for name in sorted(set(shipped) & set(listed)):
        if sha256(shipped[name]) != listed[name]['sha256']:
            problems.append('SHA-256 differs from the manifest: ' + name)
    return problems


def git(repo, *arguments):
    result = subprocess.run(['git', '-C', str(repo)] + list(arguments),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError('git %s failed: %s' % (
            arguments[0], result.stderr.decode('utf-8', 'replace').strip()))
    return result.stdout


def fetch_revision(repo, fork, revision):
    if not re.fullmatch('[0-9a-fA-F]{40}', revision or ''):
        raise ValueError('Expected a full 40-character Git commit SHA, got: %r' % revision)
    git(repo, 'fetch', '-q', '--no-tags', '--depth', '1', fork, revision)
    resolved = git(repo, 'rev-parse', 'FETCH_HEAD^{commit}').decode().strip()
    if not resolved.startswith(revision.lower()):
        raise RuntimeError('Fetched commit does not match requested SHA: ' + revision)
    return resolved


def git_paths(repo, revision):
    """Paths supported by the overlay, directly from the requested Git tree."""
    paths = git(repo, 'ls-tree', '-r', '-z', '--name-only', revision, '--', 'klippy')
    return {os.fsdecode(name) for name in paths.split(b'\0') if name
            and os.fsdecode(name).endswith(SUFFIXES)}


def changed_paths(repo, old, new):
    names = git(repo, 'diff', '--name-only', '-z', '--no-renames',
                old, new, '--', 'klippy/')
    return {os.fsdecode(name) for name in names.split(b'\0') if name
            and os.fsdecode(name).endswith(SUFFIXES)}


def fork_file(repo, revision, name):
    validate_name(name)
    return git(repo, 'show', revision + ':' + name)


def check_fork(manifest, fork):
    """Compare tracked sources with their exact bytes in the fork commit."""
    validate_manifest(manifest)
    problems = []
    with tempfile.TemporaryDirectory(prefix='klipper-fork-check-') as tmp:
        repo = pathlib.Path(tmp) / 'fork'
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        revision = fetch_revision(repo, fork, manifest['commit'])
        remote_names = git_paths(repo, revision)
        for name, entry in sorted(manifest['sources'].items()):
            if name not in remote_names:
                problems.append('missing in the fork: ' + name)
            elif digest(fork_file(repo, revision, name)) != entry['sha256']:
                problems.append('differs from the fork: ' + name)
        # A .so that is not in the fork is the printer's locally shipped binary.
        for name, entry in sorted(manifest['binaries'].items()):
            if (name in remote_names
                    and digest(fork_file(repo, revision, name)) != entry['sha256']):
                problems.append('binary differs from the fork: ' + name)
    return problems


def create_plan(repo, previous, old_revision, revision):
    """Download and validate ALL required blobs before touching the overlay."""
    remote_names = git_paths(repo, revision)
    old_names = git_paths(repo, old_revision)
    changed = changed_paths(repo, old_revision, revision)
    listed = dict(previous['sources'])
    listed.update(previous['binaries'])
    shipped = {'klippy/' + rel: path for rel, path in overlay_files().items()}
    candidates = set(listed) | set(shipped) | changed
    actions = []
    desired = {name: sha256(path) for name, path in shipped.items()}

    for name in sorted(candidates):
        path = validate_name(name)
        is_binary = name.endswith('.so')
        exists = name in shipped
        if path.is_symlink():
            raise RuntimeError('Refusing to overwrite symbolic link: ' + str(path))
        if name in remote_names:
            content = fork_file(repo, revision, name)
            checksum = digest(content)
            desired[name] = checksum
            if not exists or sha256(path) != checksum:
                locally_changed = (exists and (name not in listed or
                    sha256(path) != listed[name]['sha256']))
                actions.append((name, 'REPLACE' if exists else 'ADD',
                                content, locally_changed))
        elif is_binary:
            # FlashForge's precompiled binaries aren't necessarily in Git.
            if exists:
                desired[name] = sha256(path)
            else:
                desired.pop(name, None)
                raise RuntimeError('Locally shipped binary is missing: ' + name)
        elif name in listed:
            # Never silently treat an absent fork file as a deletion if the
            # previous recorded fork revision didn't contain it either.
            if name not in old_names:
                raise RuntimeError('Tracked source was never in the old fork commit: '
                                   + name)
            desired.pop(name, None)
            if exists:
                actions.append((name, 'DELETE', None,
                                sha256(path) != listed[name]['sha256']))
        elif exists:
            # Do not delete a local-only, untracked source automatically.
            raise RuntimeError('Local-only source not present in the fork: ' + name)
    result = build_manifest(revision, desired, previous)
    return actions, result


def format_plan(actions, previous, result):
    print('Klipper fork synchronization')
    print('  From: %s' % previous['commit'])
    print('  To:   %s' % result['commit'])
    if actions:
        for name, op, _content, locally_changed in actions:
            annotation = ' [LOCAL EDITS / NOT IN MANIFEST]' if locally_changed else ''
            print('  %-7s %s%s' % (op, name, annotation))
    else:
        print('  All overlay files already match the target fork commit.')
    if previous != result:
        print('  UPDATE  docs/klipper-ad5m-manifest.json')
    print('  %d file action(s)' % len(actions))


def confirm_changes():
    print('\nWARNING: REPLACE and DELETE discard local file contents.')
    print('If your local changes have not been saved/committed, they may be lost.')
    print('No Git working-tree cleanliness check is performed.')
    if not sys.stdin.isatty():
        print('Not an interactive terminal. No files changed; use --yes to confirm.',
              file=sys.stderr)
        return False
    try:
        answer = input("Type 'yes' to apply synchronization: ")
    except (KeyboardInterrupt, EOFError):
        print()
        return False
    return answer.strip() == 'yes'


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix='.klipper-sync-',
                                    suffix='.tmp', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            os.chmod(temp_path, path.stat().st_mode & 0o777)
        os.replace(temp_path, str(path))
    finally:
        if os.path.exists(temp_path):
            os.unlink(temp_path)


def apply_plan(actions, result):
    """Write manifest last; restore overwritten/deleted files on I/O failure."""
    with tempfile.TemporaryDirectory(prefix='klipper-sync-rollback-') as tmp:
        backups = []
        paths = [validate_name(name) for name, _op, _content, _dirty in actions]
        paths.append(MANIFEST)
        for index, path in enumerate(paths):
            if path.is_symlink():
                raise RuntimeError('Refusing to modify symbolic link: ' + str(path))
            backup = pathlib.Path(tmp) / str(index)
            if path.exists():
                shutil.copy2(str(path), str(backup))
                backups.append((path, backup))
        existing = {path for path, _backup in backups}
        applied = []
        try:
            for (name, op, content, _dirty), path in zip(actions, paths):
                if op == 'DELETE':
                    path.unlink()
                else:
                    atomic_write(path, content)
                applied.append(path)
            # Any interrupted update is detectable via the *old* manifest.
            manifest_bytes = (json.dumps(result, indent=2, sort_keys=True)
                              + '\n').encode('utf-8')
            atomic_write(MANIFEST, manifest_bytes)
            applied.append(MANIFEST)
            problems = check_manifest(result)
            if problems:
                raise RuntimeError('Post-sync check failed: ' + '; '.join(problems))
        except Exception:
            for path in reversed(applied):
                if path not in existing and path.exists():
                    path.unlink()
            for path, backup in reversed(backups):
                # Restore without calling the normal write helper: the error
                # may have originated in that helper (or its destination).
                shutil.copy2(str(backup), str(path))
            raise


def synchronize(previous, fork, commit, dry_run=False, yes=False):
    validate_manifest(previous)
    with tempfile.TemporaryDirectory(prefix='klipper-fork-sync-') as tmp:
        repo = pathlib.Path(tmp) / 'fork'
        subprocess.run(['git', 'init', '-q', str(repo)], check=True)
        old_revision = fetch_revision(repo, fork, previous['commit'])
        revision = (old_revision if old_revision.startswith(commit.lower())
                    else fetch_revision(repo, fork, commit))
        actions, result = create_plan(repo, previous, old_revision, revision)
        format_plan(actions, previous, result)
        if dry_run:
            print('Dry run: no files changed.')
            return 0
        if not actions and previous == result:
            print('Already synchronized.')
            return 0
        if not yes and not confirm_changes():
            print('Synchronization cancelled; no files changed.')
            return 1
        apply_plan(actions, result)
        print('Updated %d file(s) and manifest; verified SHA-256 checksums.'
              % len(actions))
        return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--fork', help='fork URL/path (also enables fork verification)')
    parser.add_argument('--update', '--sync', action='store_true',
                        help='synchronize local overlay and manifest from fork')
    parser.add_argument('--commit', help='target full fork commit SHA; defaults to manifest commit')
    parser.add_argument('--dry-run', action='store_true',
                        help='show planned sync without writing files')
    parser.add_argument('--yes', action='store_true',
                        help='explicitly allow overwrites/deletions without prompt')
    args = parser.parse_args(argv)
    if not args.update and (args.commit or args.dry_run or args.yes):
        parser.error('--commit/--dry-run/--yes require --update')
    try:
        manifest = load_manifest()
        if args.update:
            return synchronize(manifest, args.fork or manifest.get('fork', FORK_URL),
                               args.commit or manifest['commit'],
                               dry_run=args.dry_run, yes=args.yes)
        problems = check_manifest(manifest)
        if args.fork:
            problems += check_fork(manifest, args.fork)
        for problem in problems:
            print(problem, file=sys.stderr)
        if not problems:
            print('OK: %d Python files and %d binaries match the manifest' % (
                len(manifest['sources']), len(manifest['binaries'])))
        return 1 if problems else 0
    except (OSError, RuntimeError, ValueError, KeyError, json.JSONDecodeError) as e:
        print('ERROR: %s' % e, file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
