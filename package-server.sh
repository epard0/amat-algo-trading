#!/usr/bin/env bash
# Usage: ./package-server.sh [user@server] [remote-directory]
# Requires Bash, Python 3, GNU tar, and xz locally; Docker Compose on the server.
# Creates the archive locally and PRINTS the deployment command; never connects.
set -Eeuo pipefail
umask 077
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SERVER="${1:-user@server}"
REMOTE_DIR="${2:-amat-workspace}"
ARCHIVE="$ROOT/amat-server-$(date -u +%Y%m%dT%H%M%SZ)-$$.tar.xz"
for tool in python3 tar xz; do
  command -v "$tool" >/dev/null || { printf 'Missing required tool: %s\n' "$tool" >&2; exit 1; }
done
MANIFEST="$(mktemp)"
trap 'rm -f -- "$MANIFEST"' EXIT
# Use an allowlist so newly generated research files cannot bloat the archive.
# Read .env as data, never as shell commands. Include the selected snapshot only.
python3 - "$ROOT" >"$MANIFEST" <<'PY'
import os
import pathlib
import re
import sys

root = pathlib.Path(sys.argv[1])
env = root / 'executor/.env'
if not env.is_file():
    sys.exit('Missing executor/.env; configure it before packaging.')
settings = {}
for line in env.read_text().splitlines():
    match = re.match(r'^\s*(?:export\s+)?([A-Za-z_][A-Za-z_0-9]*)\s*=\s*(.*?)\s*$', line)
    if match:
        value = match[2]
        if value[:1] in ('"', "'"):
            value = value[1:value.find(value[0], 1)]
        else:
            value = re.split(r'\s+#', value, maxsplit=1)[0]
        settings[match[1]] = value
snapshot = settings.get('SNAPSHOT', '')
if snapshot.startswith('/opt/amat/'):
    snapshot = 'amat/' + snapshot[len('/opt/amat/'):]
elif snapshot.startswith('data/'):
    snapshot = 'amat/' + snapshot
snapshot_path = (root / snapshot).resolve()
snapshot_root = (root / 'amat/data/snapshots').resolve()
if not snapshot or not snapshot_path.is_relative_to(snapshot_root) or not snapshot_path.is_dir():
    sys.exit('SNAPSHOT in executor/.env must select an existing amat/data/snapshots directory.')
paths = [root / 'package-server.sh', snapshot_path]
for package in ('amat', 'executor'):
    base = root / package
    paths.extend(base / name for name in ('src', 'configs', 'pyproject.toml', 'uv.lock',
                 'README.md', '.python-version', 'Dockerfile', '.dockerignore',
                 'docker-compose.yml', 'rebuild-restart.sh', 'VPS_RUNBOOK.md')
                 if (base / name).exists())
    paths.extend(base.glob('.env*'))
paths.extend(root.glob('.env*'))
skip = {'__pycache__', '.venv', 'venv', 'node_modules', '.git', '.pytest_cache',
        '.mypy_cache', '.ruff_cache', '.cache'}
files = set()
for path in paths:
    if path.is_dir():
        for directory, dirs, names in os.walk(path):
            dirs[:] = sorted(d for d in dirs if d not in skip)
            for name in names:
                if not name.endswith(('.pyc', '.pyo')):
                    files.add(pathlib.Path(directory) / name)
    else:
        files.add(path)
for path in sorted(files):
    if path.is_symlink():
        sys.exit(f'Refusing runtime symlink; package its target explicitly: {path.relative_to(root)}')
    sys.stdout.buffer.write(os.fsencode(str(path.relative_to(root))) + b'\0')
print(f'Including selected snapshot: {snapshot_path.relative_to(root)}', file=sys.stderr)
PY
printf 'Creating %s\n' "$ARCHIVE"
# Low compression level and parallel xz keep packaging quick.
if ! XZ_OPT='-T0 -1' tar -C "$ROOT" --null --verbatim-files-from -T "$MANIFEST" -cJf "$ARCHIVE"; then
  rm -f -- "$ARCHIVE"
  exit 1
fi
chmod 600 "$ARCHIVE"
ls -lh -- "$ARCHIVE"
# Double quote layers: the local shell passes one argument to SSH, and the
# remote shell passes the generated command intact to Bash.
python3 - "$ARCHIVE" "$SERVER" "$REMOTE_DIR" <<'PY'
import shlex
import sys
archive, server, destination = sys.argv[1:]
q = shlex.quote
remote_archive = '/tmp/' + archive.rsplit('/', 1)[-1]
command = (f'set -e; umask 077; mkdir -p -- {q(destination)}; '
           f'tar -xJf {q(remote_archive)} -C {q(destination)}; '
           f'find {q(destination + "/amat")} -type d -exec chmod 755 {{}} +; '
           f'find {q(destination + "/amat")} -type f ! -name ".env*" -exec chmod a+r {{}} +; '
           f'cd -- {q(destination + "/executor")}; '
           'docker compose config --quiet; docker compose up -d --build executor; '
           'docker compose ps')
print('\nTransfer, extract, and start (run this locally):')
print(f'scp -- {q(archive)} {q(server + ":" + remote_archive)} && '
      f'ssh -- {q(server)} {q("bash -c " + q(command))}')
print('\nArchive includes private .env files; keep it private. Existing server Docker volumes are retained.')
PY
