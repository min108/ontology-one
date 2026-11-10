"""Trusted container entrypoint. Never mounted from an uploaded skill package."""
import json
import os
from pathlib import Path, PurePosixPath
import resource
import signal
import subprocess
import sys


def main():
    # These kernel limits remain effective even when a rootless Docker daemon
    # cannot apply cgroups. The outer container limits add aggregate controls on
    # normal deployments; they are never our only protection against fork/OOM.
    for limit, value in ((resource.RLIMIT_AS, 48 * 1024 * 1024),
                         (resource.RLIMIT_NPROC, 4), (resource.RLIMIT_CPU, 5),
                         (resource.RLIMIT_NOFILE, 64), (resource.RLIMIT_CORE, 0),
                         (resource.RLIMIT_FSIZE, 16 * 1024 * 1024)):
        resource.setrlimit(limit, (value, value))
    raw = sys.stdin.buffer.read(3 * 1024 * 1024 + 1)
    if len(raw) > 3 * 1024 * 1024:
        return 65
    body = json.loads(raw)
    if set(body) != {'files', 'entrypoint', 'input'}:
        return 65
    root = Path('/tmp/skill')
    root.mkdir(mode=0o700)
    for name, content in body['files'].items():
        parts = PurePosixPath(name).parts
        if not parts or name.startswith('/') or '\\' in name or ':' in name or '\x00' in name or any(part in {'.', '..'} for part in name.split('/')):
            return 65
        path = root.joinpath(*parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    entry = body['entrypoint']
    if entry not in body['files'] or not entry.startswith('scripts/') or not entry.endswith('.py'):
        return 65
    # No inherited proxy, database password, provider key or caller token reaches
    # the untrusted script, including when the service itself has such variables.
    environment = {'PATH': '/usr/local/bin:/usr/bin:/bin', 'HOME': '/tmp', 'LANG': 'C.UTF-8',
                   'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONUNBUFFERED': '1'}
    os.environ.clear()
    os.environ.update(environment)
    try:
        process = subprocess.Popen([sys.executable, '-I', '-B', '-u', str(root / entry)],
            cwd=root, env=environment, stdin=subprocess.PIPE, stdout=sys.stdout, stderr=sys.stderr, start_new_session=True)
    except (OSError, MemoryError):
        return 75
    try:
        process.communicate(json.dumps(body['input'], ensure_ascii=False, allow_nan=False).encode(), timeout=4.5)
        return process.returncode if process.returncode >= 0 else 128 - process.returncode
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()
        return 124
    finally:
        # Exiting PID 1 also kills descendants that created another process group.
        if process.poll() is None:
            process.kill()


if __name__ == '__main__':
    try:
        code = main()
    except BaseException:
        code = 65
    os._exit(code)
