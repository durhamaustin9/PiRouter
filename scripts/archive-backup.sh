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
: "${ARCHIVE_REPOSITORY:?ARCHIVE_REPOSITORY is required}"
: "${ARCHIVE_SSH_TARGET:?ARCHIVE_SSH_TARGET is required}"
: "${ARCHIVE_SSH_KEY:?ARCHIVE_SSH_KEY is required}"
: "${RESTIC_PASSWORD_FILE:?RESTIC_PASSWORD_FILE is required}"

quiet_minutes="${QUIET_MINUTES:-20}"
copy_guard_seconds="${COPY_GUARD_SECONDS:-30}"
connect_timeout_seconds="${CONNECT_TIMEOUT_SECONDS:-15}"
tailscale_ping_attempts="${TAILSCALE_PING_ATTEMPTS:-3}"
progress_fps="${RESTIC_PROGRESS_FPS:-0.2}"
restic_cache="${RESTIC_CACHE_DIR:-/var/cache/pirouter-restic}"
backup_host="${RESTIC_BACKUP_HOST:-$(hostname --short)}"
backup_tag="${RESTIC_BACKUP_TAG:-timemachine}"
tailscale_target="${ARCHIVE_TAILSCALE_TARGET:-}"
attempt_at="$(date --iso-8601=seconds)"

for value in \
  "$quiet_minutes" \
  "$copy_guard_seconds" \
  "$connect_timeout_seconds" \
  "$tailscale_ping_attempts"; do
  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "Archive timing values must be positive integers" >&2
    exit 1
  fi
done

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
  local progress_json="${6:-null}"
  local status_temp
  if ! jq -e 'type == "object"' <<<"$progress_json" >/dev/null 2>&1; then
    progress_json=null
  fi
  status_temp="$(mktemp)"
  jq -n \
    --arg state "$state" \
    --arg message "$message" \
    --arg lastAttemptAt "$attempt_at" \
    --arg lastSuccessAt "$last_success" \
    --arg lastSnapshotId "$snapshot_id" \
    --arg dataAddedBytes "$data_added" \
    --argjson progress "$progress_json" \
    '{
      configured: true,
      automatic: true,
      state: $state,
      message: $message,
      lastAttemptAt: ($lastAttemptAt | if length > 0 then . else null end),
      lastSuccessAt: ($lastSuccessAt | if length > 0 then . else null end),
      lastSnapshotId: ($lastSnapshotId | if length > 0 then . else null end),
      dataAddedBytes: (($dataAddedBytes | select(length > 0) | tonumber?) // null),
      progressPercent: ($progress.progressPercent // null),
      bytesProcessed: ($progress.bytesProcessed // null),
      totalBytes: ($progress.totalBytes // null),
      filesProcessed: ($progress.filesProcessed // null),
      totalFiles: ($progress.totalFiles // null),
      currentFile: ($progress.currentFile // null),
      elapsedSeconds: ($progress.elapsedSeconds // null),
      estimatedSecondsRemaining: ($progress.estimatedSecondsRemaining // null),
      progressUpdatedAt: ($progress.progressUpdatedAt // null)
    }' > "$status_temp"
  install -m 0644 "$status_temp" "$status_file"
  rm "$status_temp"
}

exec 9>/run/lock/pirouter-archive-backup.lock
if ! flock -n 9; then
  exit 0
fi

tailnet_peer_reachable() {
  if [[ -z "$tailscale_target" ]] ||
    ! command -v tailscale >/dev/null 2>&1; then
    return 0
  fi

  local attempt
  for ((attempt = 1; attempt <= tailscale_ping_attempts; attempt++)); do
    if tailscale ping --timeout="${connect_timeout_seconds}s" -c 1 \
      "$tailscale_target" >/dev/null 2>&1; then
      return 0
    fi
    if (( attempt < tailscale_ping_attempts )); then
      sleep 2
    fi
  done
  return 1
}

restic_command() {
  local sftp_command
  sftp_command="/usr/bin/ssh -i $ARCHIVE_SSH_KEY -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=$connect_timeout_seconds -o ServerAliveInterval=30 -o ServerAliveCountMax=3 $ARCHIVE_SSH_TARGET -s sftp"
  XDG_CACHE_HOME="$restic_cache" \
    RESTIC_PASSWORD_FILE="$RESTIC_PASSWORD_FILE" \
    restic -o "sftp.command=$sftp_command" --repo "$ARCHIVE_REPOSITORY" "$@"
}

source_changed_recently() {
  find "$TIME_MACHINE_SOURCE" -type f -mmin "-${quiet_minutes}" \
    -print -quit | grep -q .
}

source_changed_since() {
  local marker="$1"
  find "$TIME_MACHINE_SOURCE" -type f -newer "$marker" \
    -print -quit | grep -q .
}

if [[ ! -d "$TIME_MACHINE_SOURCE" ]]; then
  write_status "error" "Time Machine source is unavailable"
  exit 1
fi

if [[ "${1:-backup}" == "maintenance" ]]; then
  if [[ -z "$(previous_value lastSuccessAt)" ]]; then
    exit 0
  fi
  write_status "maintenance" "Verifying and compacting Unraid archive history"
  if ! tailnet_peer_reachable; then
    write_status "error" "Unraid is unreachable; maintenance will retry next week"
    exit 1
  fi
  restic_command unlock
  restic_command check
  restic_command prune
  write_status "success" "Unraid archive history verified" \
    "$(previous_value lastSuccessAt)" "$(previous_value lastSnapshotId)"
  exit 0
fi

if source_changed_recently; then
  write_status "waiting" \
    "Waiting ${quiet_minutes} minutes for Time Machine to become quiet"
  exit 0
fi

write_status "running" "Archiving the idle Time Machine bundle directly to Unraid"
if ! tailnet_peer_reachable; then
  write_status "error" "Unraid is unreachable; the Pi will retry automatically"
  exit 1
fi

# A terminated Restic process can leave a stale lock. The local flock above
# guarantees there is no other Pi archive process while it is cleared.
if ! restic_command unlock; then
  write_status "error" "The Unraid archive could not be unlocked; retry scheduled"
  exit 1
fi

change_marker="$(mktemp /run/pirouter-archive-change-marker.XXXXXX)"
backup_output="$(mktemp /run/pirouter-archive-output.XXXXXX)"
trap 'rm -f -- "$change_marker" "$backup_output"' EXIT
touch "$change_marker"

set +e
ionice -c 3 nice -n 15 \
  env \
    XDG_CACHE_HOME="$restic_cache" \
    RESTIC_PASSWORD_FILE="$RESTIC_PASSWORD_FILE" \
    RESTIC_PROGRESS_FPS="$progress_fps" \
    restic \
    -o "sftp.command=/usr/bin/ssh -i $ARCHIVE_SSH_KEY -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=$connect_timeout_seconds -o ServerAliveInterval=30 -o ServerAliveCountMax=3 $ARCHIVE_SSH_TARGET -s sftp" \
    --repo "$ARCHIVE_REPOSITORY" backup "$TIME_MACHINE_SOURCE" \
    --host "$backup_host" --tag "$backup_tag" --json > "$backup_output" &
restic_pid=$!
set -e

time_machine_changed=false
latest_progress_json=null
while kill -0 "$restic_pid" 2>/dev/null; do
  sleep "$copy_guard_seconds"
  if source_changed_since "$change_marker"; then
    time_machine_changed=true
    kill -TERM "$restic_pid" 2>/dev/null || true
    break
  fi
  raw_progress="$(tail -n 50 "$backup_output" | jq -sc '
    [.[] | select(.message_type == "status")] | last // empty
  ' 2>/dev/null || true)"
  if [[ -n "$raw_progress" ]]; then
    progress_updated_at="$(date --iso-8601=seconds)"
    latest_progress_json="$(jq -c \
      --arg sourcePrefix "${TIME_MACHINE_SOURCE%/}/" \
      --arg progressUpdatedAt "$progress_updated_at" \
      '{
        progressPercent: (((.percent_done // 0) * 100) | if . > 100 then 100 else . end),
        bytesProcessed: (.bytes_done // null),
        totalBytes: (.total_bytes // null),
        filesProcessed: (.files_done // null),
        totalFiles: (.total_files // null),
        currentFile: ((.current_files // [] | last // null) |
          if . == null then null
          elif startswith($sourcePrefix) then ltrimstr($sourcePrefix)
          else . end),
        elapsedSeconds: (.seconds_elapsed // null),
        estimatedSecondsRemaining: (.seconds_remaining // null),
        progressUpdatedAt: $progressUpdatedAt
      }' <<<"$raw_progress")"
    progress_display="$(jq -r '.progressPercent * 100 | round / 100' \
      <<<"$latest_progress_json")"
    write_status "running" \
      "Active Restic transfer to Unraid · ${progress_display}% processed" \
      "" "" "" "$latest_progress_json"
  fi
done

set +e
wait "$restic_pid"
backup_result=$?
set -e

summary="$(jq -sc '[.[] | select(.message_type == "summary")] | last // {}' \
  "$backup_output")"
snapshot_id="$(jq -r '.snapshot_id // empty' <<<"$summary")"
data_added="$(jq -r '.data_added // empty' <<<"$summary")"

if source_changed_since "$change_marker"; then
  time_machine_changed=true
fi

if [[ "$time_machine_changed" == true ]]; then
  if [[ -n "$snapshot_id" ]]; then
    restic_command forget "$snapshot_id" >/dev/null 2>&1 || true
  fi
  write_status "waiting" \
    "Time Machine became active; the Pi stopped safely and will retry"
  exit 0
fi

if [[ "$backup_result" -ne 0 || -z "$snapshot_id" ]]; then
  write_status "error" \
    "The Unraid connection stopped early; uploaded chunks are safe and retryable" \
    "" "" "" "$latest_progress_json"
  if [[ "$backup_result" -eq 0 ]]; then
    backup_result=1
  fi
  exit "$backup_result"
fi

completed_at="$(date --iso-8601=seconds)"
short_snapshot="${snapshot_id:0:8}"
write_status "success" "Unraid archive snapshot ${short_snapshot} completed" \
  "$completed_at" "$snapshot_id" "$data_added"

if ! restic_command forget \
  --host "$backup_host" \
  --tag "$backup_tag" \
  --keep-daily "${KEEP_DAILY:-14}" \
  --keep-weekly "${KEEP_WEEKLY:-8}" \
  --keep-monthly "${KEEP_MONTHLY:-12}"; then
  write_status "success" \
    "Snapshot ${short_snapshot} is safe; retention cleanup was deferred" \
    "$completed_at" "$snapshot_id" "$data_added"
fi
