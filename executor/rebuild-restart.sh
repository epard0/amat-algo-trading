#!/usr/bin/env bash
# Rebuild and recreate the executor without losing its audit trail.
#
# Positions live at the Alpaca paper broker, not in the container. The local
# journal, equity curve, plans, reports, and scheduler ledger live in the
# executor-state named volume. This script records both sides before changing
# the container and verifies that the same named volume is mounted afterwards.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

COMPOSE=(docker compose)
SERVICE="executor"
CONTAINER="amat-executor"
STATE_DESTINATION="/var/lib/amat-executor"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$SCRIPT_DIR/backups/rebuild-$STAMP"

fail() {
  printf 'rebuild-restart: %s\n' "$*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "docker is not installed or not on PATH"
[[ -f .env ]] || fail "missing $SCRIPT_DIR/.env"
"${COMPOSE[@]}" config --quiet

running_id="$("${COMPOSE[@]}" ps --quiet "$SERVICE")"
[[ -n "$running_id" ]] || fail \
  "the executor is not running; use 'docker compose up -d' for the initial deployment"

volume_name="$(docker inspect --format \
  '{{range .Mounts}}{{if eq .Destination "/var/lib/amat-executor"}}{{.Name}}{{end}}{{end}}' \
  "$CONTAINER")"
[[ -n "$volume_name" ]] || fail "cannot resolve the state volume mounted at $STATE_DESTINATION"
current_image="$(docker inspect --format '{{.Config.Image}}' "$CONTAINER")"

mkdir -p "$BACKUP_DIR"
printf '%s\n' "$volume_name" >"$BACKUP_DIR/state-volume-name.txt"

printf 'Recording paper-account positions and persistent state in %s\n' "$BACKUP_DIR"
"${COMPOSE[@]}" exec -T "$SERVICE" executor account >"$BACKUP_DIR/account-before.json" || \
  fail "could not read the paper account; the running container was left untouched"

# Freeze the writer for only the duration of the local volume snapshot. A
# helper container reads the same volume because Docker cannot exec inside a
# paused container. The EXIT trap guarantees an interrupted backup unpauses the
# trading daemon before this script returns.
paused=false
restore_running_daemon() {
  if [[ "$paused" == true ]]; then
    "${COMPOSE[@]}" unpause "$SERVICE" >/dev/null 2>&1 || true
  fi
}
trap restore_running_daemon EXIT
"${COMPOSE[@]}" pause "$SERVICE" >/dev/null
paused=true
docker run --rm --volume "$volume_name:/state:ro" --entrypoint tar "$current_image" \
  -C /state -czf - . >"$BACKUP_DIR/executor-state.tgz" || \
  fail "could not back up executor state; the running container will be unpaused"
"${COMPOSE[@]}" unpause "$SERVICE" >/dev/null
paused=false

printf 'Building the replacement image while the current executor remains running...\n'
"${COMPOSE[@]}" build "$SERVICE"

printf 'Gracefully recreating only the executor service...\n'
"${COMPOSE[@]}" up -d --no-deps --force-recreate "$SERVICE"

deadline=$((SECONDS + 300))
status=""
while ((SECONDS < deadline)); do
  status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$CONTAINER" 2>/dev/null || true)"
  case "$status" in
    healthy | running)
      break
      ;;
    unhealthy | exited | dead)
      "${COMPOSE[@]}" logs --tail=100 "$SERVICE" >&2 || true
      fail "replacement container entered state '$status'; backup is at $BACKUP_DIR"
      ;;
  esac
  sleep 5
done
[[ "$status" == "healthy" || "$status" == "running" ]] || \
  fail "replacement container did not become ready within 5 minutes"

replacement_volume="$(docker inspect --format \
  '{{range .Mounts}}{{if eq .Destination "/var/lib/amat-executor"}}{{.Name}}{{end}}{{end}}' \
  "$CONTAINER")"
[[ "$replacement_volume" == "$volume_name" ]] || fail \
  "state volume changed from '$volume_name' to '$replacement_volume'; backup is at $BACKUP_DIR"

"${COMPOSE[@]}" exec -T "$SERVICE" executor account >"$BACKUP_DIR/account-after.json" || \
  fail "container restarted but the paper account could not be re-read"

printf 'Restart complete.\n'
printf '  persistent volume: %s (unchanged)\n' "$volume_name"
printf '  state backup:      %s\n' "$BACKUP_DIR/executor-state.tgz"
printf '  positions before:  %s\n' "$BACKUP_DIR/account-before.json"
printf '  positions after:   %s\n' "$BACKUP_DIR/account-after.json"
printf 'Review account-before.json vs account-after.json if orders were filling during the restart.\n'
