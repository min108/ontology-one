"""Stopped-instance 0.5 -> 0.6 copy. Never modify or run the old installation.

The caller owns the new installation's command gate. The old launcher command
and lifetime locks remain held until a complete private copy is promoted. The
original directory is a point-in-time fallback, not a live shared database.
"""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import tempfile

from backend import platform_io as io
from backend.version import APP_VERSION

FROM_VERSION = '0.5.0'
TO_VERSION = '0.6.0'
_EPHEMERAL = {'runtime-state.json', 'service.lock', 'command.lock', 'last-result.json',
              'migration-progress.json', 'mysql-init.sql', 'mysql-client.cnf'}


class UpgradeError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def read_json(path, private=False):
    with io.secure_open(path, 'rb', require_private=private) as stream:
        value = stream.read(1024 * 1024 + 1)
    if len(value) > 1024 * 1024:
        raise UpgradeError('UPGRADE_SOURCE_INVALID')
    return json.loads(value)


def compatible_marker(marker, settings, receipt):
    """Only an explicit completed copy permits the older initialized marker."""
    return (APP_VERSION == TO_VERSION and isinstance(receipt, dict)
        and receipt.get('schema_version') == 1 and receipt.get('state') == 'copied'
        and receipt.get('from_version') == FROM_VERSION and receipt.get('to_version') == APP_VERSION
        and receipt.get('installation_id') == marker.get('installation_id') == settings.get('installation_id')
        and marker.get('version') == FROM_VERSION
        and isinstance(receipt.get('source_manifest_sha256'), str)
        and re.fullmatch(r'[0-9a-f]{64}', receipt['source_manifest_sha256']) is not None)


def _copy_tree(source, target, *, relative=Path(), counts):
    # Enumeration supplies leaf names only. Every directory/file is reopened
    # by platform_io's handle-relative no-reparse operation before its content
    # is accessed. The pinned context also checks retagging on exit.
    with io._directory(source):
        io.assert_private(source, require_protected=False)
        names = sorted(os.listdir(source))
        for name in names:
            item = source / name
            child = relative / name
            io.reject_reparse_points(item)
            info = os.lstat(item)
            if stat.S_ISDIR(info.st_mode):
                io.ensure_private_directory(target / name)
                _copy_tree(item, target / name, relative=child, counts=counts)
            elif stat.S_ISREG(info.st_mode):
                if relative == Path('local') and name in _EPHEMERAL:
                    continue
                if info.st_nlink != 1:
                    raise UpgradeError('UPGRADE_SOURCE_INVALID')
                with io.secure_open(item, 'rb', require_private=False) as original:
                    if os.name == 'nt':
                        import msvcrt
                        io._assert_private_handle(msvcrt.get_osfhandle(original.fileno()), require_protected=False)
                    else:
                        io.assert_private(item)
                    before = os.fstat(original.fileno())
                    with io.secure_open(target / name, 'xb') as output:
                        copied = 0
                        while chunk := original.read(1024 * 1024):
                            output.write(chunk)
                            copied += len(chunk)
                        output.flush()
                        os.fsync(output.fileno())
                    after = os.fstat(original.fileno())
                    if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                            or copied != before.st_size):
                        raise UpgradeError('UPGRADE_SOURCE_CHANGED')
                counts['files'] += 1
                counts['bytes'] += copied
            else:
                raise UpgradeError('UPGRADE_SOURCE_INVALID')
        if sorted(os.listdir(source)) != names:
            raise UpgradeError('UPGRADE_SOURCE_CHANGED')


def upgrade(source_root, destination_root):
    source, destination = Path(source_root).absolute(), Path(destination_root).absolute()
    if APP_VERSION != TO_VERSION:
        raise UpgradeError('UPGRADE_UNSUPPORTED')
    if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise UpgradeError('UPGRADE_SOURCE_INVALID')
    for root in (source, destination):
        io.reject_reparse_points(root)
    data = destination / 'data'
    if os.path.lexists(data):
        raise UpgradeError('UPGRADE_TARGET_NOT_EMPTY')
    source_manifest = read_json(source / 'runtime/manifest.json')
    target_manifest = read_json(destination / 'runtime/manifest.json')
    if (source_manifest.get('application_version') != FROM_VERSION
            or source_manifest.get('platform') != 'win_amd64'
            or target_manifest.get('application_version') != APP_VERSION
            or source_manifest.get('mysql', {}).get('source_sha256') != target_manifest.get('mysql', {}).get('source_sha256')
            or not re.fullmatch(r'[0-9a-f]{64}', source_manifest.get('mysql', {}).get('source_sha256', ''))):
        raise UpgradeError('UPGRADE_UNSUPPORTED')
    local = source / 'data/local'
    with ExitStack() as held:
        try:
            for name in ('command.lock', 'service.lock'):
                stream = held.enter_context(io.secure_open(local / name, 'rb', require_private=True))
                held.enter_context(io.file_lock(stream, blocking=False))
        except (BlockingIOError, OSError):
            raise UpgradeError('UPGRADE_SOURCE_BUSY') from None
        # Both held lock streams pin this protected root and every ancestor.
        io.assert_private(source / 'data')
        settings = read_json(local / 'installation.json', True)
        marker = read_json(local / 'initialized.json', True)
        if (not isinstance(settings, dict) or not isinstance(marker, dict)
                or marker.get('version') != FROM_VERSION
                or marker.get('installation_id') != settings.get('installation_id')
                or not re.fullmatch(r'[0-9a-f]{32}', settings.get('installation_id', ''))
                or any(not re.fullmatch(r'[0-9a-f]{64}', settings.get(name, '')) for name in ('root_password', 'runtime_password'))):
            raise UpgradeError('UPGRADE_SOURCE_INVALID')
        io.assert_private(source / 'data/mysql')
        staging = Path(tempfile.mkdtemp(prefix='upgrade-copy-', dir=destination))
        io.ensure_private_directory(staging)
        try:
            counts = {'files': 0, 'bytes': 0}
            _copy_tree(source / 'data', staging, counts=counts)
            io.assert_private(source / 'data')
            receipt = {'schema_version': 1, 'state': 'copied', 'from_version': FROM_VERSION,
                'to_version': APP_VERSION, 'installation_id': settings['installation_id'],
                'source_manifest_sha256': hashlib.sha256(json.dumps(source_manifest, sort_keys=True).encode()).hexdigest(),
                **counts}
            io.atomic_write(staging / 'local/upgrade-receipt.json', json.dumps(receipt).encode())
            io.promote_private_directory(staging, data)
            return {'upgraded': True, 'from_version': FROM_VERSION, 'to_version': APP_VERSION,
                    'source_preserved': True, **counts}
        finally:
            if staging.exists():
                shutil.rmtree(staging)
