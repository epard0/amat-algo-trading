#!/bin/bash
# Read-only source volumes; bounded low-priority host-disk staging, never RAM tmpfs.
set -euo pipefail
umask 077
backup_root="$HOME/.local/share/amat/backups"
mkdir -p "$backup_root"
exec 9>"$HOME/.local/share/amat/backup.lock"
flock -n 9 || exit 0
staging_outer=''
staging_image=''
current_partial=''
backup_helper=''
cleanup() {
  if [[ -n "$backup_helper" ]]; then docker rm -f "$backup_helper" >/dev/null 2>&1 || true; fi
  if [[ -n "$current_partial" ]]; then rm -f -- "$current_partial"; fi
  if [[ -n "$staging_outer" ]]; then
    # The image's uid 10001 owns online SQLite staging; remove it as that user.
    timeout 30 docker run --rm --name "${backup_helper}-cleanup" --network none --read-only --cap-drop ALL --memory 48m \
      --pids-limit 16 --mount "type=bind,source=$staging_outer/data,target=/backup" \
      --entrypoint python "$staging_image" -c \
      'import pathlib,shutil;[(shutil.rmtree(p) if p.is_dir() else p.unlink()) for p in pathlib.Path("/backup").iterdir()]' >/dev/null 2>&1 || true
    docker rm -f "${backup_helper}-cleanup" >/dev/null 2>&1 || true
    rm -rf -- "$staging_outer"
  fi
}
trap cleanup EXIT
for kind in state perps; do
  if [[ "$kind" == state ]]; then
    container=amat-executor
    state_root=/var/lib/amat-executor
  else
    container=kalshi-perps
    state_root=/var/lib/kalshi-perps
  fi
  # Reserve source256MiB+archive272MiB+stdout272MiB+128MiB headroom.
  python3 - "$backup_root" <<'CHECK'
import pathlib,shutil,sys
import os
memory={x.split(':',1)[0]:int(x.split()[1])*1024 for x in pathlib.Path('/proc/meminfo').read_text().splitlines() if x.split(':',1)[0] in ('MemAvailable','MemTotal','SwapTotal','SwapFree')}
swap=memory.get('SwapTotal',0)
if memory['MemAvailable'] < max(128*1024*1024,memory['MemTotal']*.15) or (swap and (swap-memory.get('SwapFree',0))/swap>=.75) or os.getloadavg()[0]>1.5*(os.cpu_count() or 1) or shutil.disk_usage(sys.argv[1]).free < 944*1024*1024:
    raise SystemExit('Backup deferred: insufficient host memory/disk headroom')
CHECK
  staging_image=$(docker inspect --format '{{.Image}}' "$container")
  staging_outer=$(mktemp -d "$backup_root/.staging-XXXXXXXX")
  mkdir "$staging_outer/data"
  # Outer is private (0700); only its inner directory is writable to image uid.
  chmod 0777 "$staging_outer/data"
  archive="backup-$kind-$(date -u +%Y%m%dT%H%M%SZ).tar.gz"
  target="$backup_root/$archive"
  backup_helper="amat-backup-${kind}-$$"
  current_partial="$target.partial"
  timeout 120 docker run --rm -i --name "$backup_helper" --network none --read-only --cap-drop ALL \
    --memory 192m --memory-swap 256m --cpus 0.25 --pids-limit 32 \
    --volumes-from "$container:ro" \
    --mount "type=bind,source=$staging_outer/data,target=/backup" \
    --tmpfs /tmp:size=8m,mode=1777 --entrypoint python "$staging_image" -c \
    'import os,sys,shutil; os.nice(10); scope={"__name__":"snapshot_helper"}; exec(compile(sys.stdin.read(),"<snapshot>","exec"),scope); scope["snapshot"](sys.argv[1],"/backup/state.tar.gz"); shutil.copyfileobj(open("/backup/state.tar.gz","rb"),sys.stdout.buffer)' \
    "$state_root" < "$HOME/.local/bin/amat-backup-state.py" > "$target.partial"
  chmod 600 "$target.partial"
  mv "$target.partial" "$target"
  current_partial=''
  cleanup
  staging_outer=''
  python3 - "$backup_root" "$kind" <<'CLEAN'
from pathlib import Path
import sys
for path in sorted(Path(sys.argv[1]).glob('backup-'+sys.argv[2]+'-*.tar.gz'), reverse=True)[28:]:
    path.unlink()
CLEAN
  echo "Saved $archive"
done
