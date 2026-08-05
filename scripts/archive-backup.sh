#!/usr/bin/env bash
set -euo pipefail

config_file="${ARCHIVE_BACKUP_CONFIG:-/etc/pirouter/archive-backup.env}"
status_file="${ARCHIVE_STATUS_FILE:-/var/lib/pirouter/archive-status.json}"

if [[ ! -r "$config_file" ]]; then
  echo "Archive backup configuration is missing: $config_file" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$config_file"

: "${TIME_MACHINE_SOURCE:?TIME_MACHINE_SOURCE is required}"
: "${ARCHIVE_SHARE:?ARCHIVE_SHARE is required}"
: "${ARCHIVE_MOUNT:?ARCHIVE_MOUNT is required}"
: "${ARCHIVE_REPOSITORY:?ARCHIVE_REPOSITORY is required}"
: "${ARCHIVE_CREDENTIALS_FILE:?ARCHIVE_CREDENTIALS_FILE is required}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required}"

quiet_minutes="${QUIET_MINUTES:-20}"
copy_guard_seconds="${COPY_GUARD_SECONDS:-30}"
restic_cache="${RESTIC_CACHE_DIR:-/var/cache/pirouter-restic}"
backup_host="${RESTIC_BACKUP_HOST:-$(hostname --short)}"
backup_tag="${RESTIC_BACKUP_TAG:-timemachine}"
attempt_at="$(date --iso-8601=seconds)"

if [[ ! "$copy_guard_seconds" =~ ^[1-9][0-9]*$ ]]; then
  echo "COPY_GUARD_SECONDS must be a positive integer" >&2
  exit 1
fi

mkdir -p "$(dirname "$status_file")" /run/lock "$restic_cache"

previous_value() {
  local key="$1"
  jq -r --arg key "$key" '.[$key] // empty' "$status_file" 2>/dev/null || true
}

write_status() {
  local state="$1"
  local message="$2"
  local last_success="${3:-$(previous_value lastSuccessAt)}"
  local snapshot_id="${4:-$(previous_value lastSnapshotId)}"
  local data_added="${5:-$(previous_value dataAddedBytes)}"
  local status_temp
  status_temp="$(mktemp)"
  jq -n \
    --arg state "$state" \
    --arg message "$message" \
    --arg lastAttemptAt "$attempt_at" \
    --arg lastSuccessAt "$last_success" \
    --arg lastSnapshotId "$snapshot_id" \
    --arg dataAddedBytes "$data_added" \
    '{
      configured: true,
      automatic: true,
      state: $state,
      message: $message,
      lastAttemptAt: ($lastAttemptAt | if length > 0 then . else null end),
      lastSuccessAt: ($lastSuccessAt | if length > 0 then . else null end),
      lastSnapshotId: ($lastSnapshotId | if length > 0 then . else null end),
      dataAddedBytes: (($dataAddedBytes | select(length > 0) | tonumber?) // null)
    }' > "$status_temp"
  install -m 0644 "$status_temp" "$status_file"
  rm "$status_temp"
}

exec 9>/run/lock/pirouter-archive-backup.lock
if ! flock -n 9; then
  exit 0
fi

mount_archive() {
  mkdir -p "$ARCHIVE_MOUNT"
  if ! mountpoint -q "$ARCHIVE_MOUNT"; then
    mount -t cifs "$ARCHIVE_SHARE" "$ARCHIVE_MOUNT" \
      -o "credentials=$ARCHIVE_CREDENTIALS_FILE,vers=3.1.1,iocharset=utf8,noserverino,uid=0,gid=0,file_mode=0600,dir_mode=0700"
  fi
}

restic_command() {
  XDG_CACHE_HOME="$restic_cache" \
    RESTIC_PASSWORD_FILE="$RESTIC_PASSWORD_FILE" \
    restic --repo "$ARCHIVE_REPOSITORY" "$@"
}

source_has_open_files() {
  smbstatus --locks --json 2>/dev/null |
    jq -e --arg source "$TIME_MACHINE_SOURCE" \
      '.open_files | keys | any(. == $source or startswith($source + "/"))' \
      >/dev/null
}

source_changed_recently() {
  find "$TIME_MACHINE_SOURCE" -type f -mmin "-${quiet_minutes}" -print -quit |
    grep -q .
}

if [[ "${1:-backup}" == "maintenance" ]]; then
  if [[ -z "$(previous_value lastSuccessAt)" ]]; then
    exit 0
  fi
  write_status "maintenance" "Checking and compacting archive history"
  mount_archive
  restic_command check
  restic_command prune
  write_status "success" "Archive history verified" \
    "$(previous_value lastSuccessAt)" "$(previous_value lastSnapshotId)"
  exit 0
fi

if [[ ! -d "$TIME_MACHINE_SOURCE" ]]; then
  write_status "error" "Time Machine source is unavailable"
  exit 1
fi

if source_has_open_files; then
  write_status "waiting" "Waiting for the current Time Machine backup to finish"
  exit 0
fi

if source_changed_recently; then
  write_status "waiting" "Waiting ${quiet_minutes} minutes for Time Machine to become quiet"
  exit 0
fi

write_status "running" "Copying a read-only snapshot to the secondary archive"
mount_archive

change_marker="$(mktemp /run/pirouter-archive-change-marker.XXXXXX)"
backup_output="$(mktemp /run/pirouter-archive-output.XXXXXX)"
trap 'rm -f -- "$change_marker" "$backup_output"' EXIT
touch "$change_marker"

set +e
ionice -c 3 nice -n 15 \
  env \
    XDG_CACHE_HOME="$restic_cache" \
    RESTIC_PASSWORD_FILE="$RESTIC_PASSWORD_FILE" \
    restic --repo "$ARCHIVE_REPOSITORY" backup "$TIME_MACHINE_SOURCE" \
    --host "$backup_host" --tag "$backup_tag" --json > "$backup_output" &
restic_pid=$!
set -e

time_machine_interrupted=false
while jobs -pr | grep -qx "$restic_pid"; do
  sleep "$copy_guard_seconds"
  if source_has_open_files; then
    time_machine_interrupted=true
    kill -TERM "$restic_pid" 2>/dev/null || true
    break
  fi
done

set +e
wait "$restic_pid"
backup_result=$?
set -e

summary="$(jq -sc '[.[] | select(.message_type == "summary")] | last // {}' "$backup_output")"
snapshot_id="$(jq -r '.snapshot_id // empty' <<<"$summary")"
data_added="$(jq -r '.data_added // empty' <<<"$summary")"

changed_during_copy="$time_machine_interrupted"
if find "$TIME_MACHINE_SOURCE" -type f -newer "$change_marker" -print -quit | grep -q .; then
  changed_during_copy=true
fi
if source_has_open_files; then
  changed_during_copy=true
fi

if [[ "$backup_result" -ne 0 || "$changed_during_copy" == true ]]; then
  if [[ -n "$snapshot_id" ]]; then
    restic_command forget "$snapshot_id"
  fi
  if [[ "$changed_during_copy" == true ]]; then
    write_status "waiting" "Time Machine became active; the archive will retry later"
    exit 0
  fi
  write_status "error" "Archive copy was incomplete and will be retried"
  exit "$backup_result"
fi

completed_at="$(date --iso-8601=seconds)"
short_snapshot="${snapshot_id:0:8}"
write_status "success" "Archive snapshot ${short_snapshot} completed" \
  "$completed_at" "$snapshot_id" "$data_added"

restic_command forget \
  --host "$backup_host" \
  --tag "$backup_tag" \
  --keep-daily "${KEEP_DAILY:-14}" \
  --keep-weekly "${KEEP_WEEKLY:-8}" \
  --keep-monthly "${KEEP_MONTHLY:-12}"
