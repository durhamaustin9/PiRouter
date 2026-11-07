#!/usr/bin/env bash
set -euo pipefail

config_file="${ARCHIVE_BACKUP_CONFIG:-/etc/pirouter/archive-backup.env}"
status_file="${ARCHIVE_STATUS_FILE:-/var/lib/pirouter/archive-status.json}"
runtime_dir="${ARCHIVE_RUNTIME_DIR:-/run}"
lock_file="${ARCHIVE_LOCK_FILE:-/run/lock/pirouter-archive-backup.lock}"

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
cleanup_after_upload="${REMOVE_LOCAL_AFTER_ARCHIVE:-false}"
if [[ "${1:-backup}" == "reset" ]]; then cleanup_after_upload=true; fi
samba_service="${TIME_MACHINE_SERVICE:-smbd}"
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

mkdir -p "$(dirname "$status_file")" "$(dirname "$lock_file")" "$runtime_dir" "$restic_cache"

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
  local error_detail="${7:-}"
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
    --arg errorDetail "$error_detail" \
    --arg lastCleanupAt "$(previous_value lastCleanupAt)" \
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
      errorDetail: ($errorDetail | if length > 0 then . else null end),
      lastCleanupAt: ($lastCleanupAt | if length > 0 then . else null end),
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

exec 9>"$lock_file"
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

verify_archive_data() {
  # On a trusted archive host this avoids reading the entire repository back
  # across the network. The recovery password only travels over SSH stdin.
  if [[ -n "${ARCHIVE_VERIFY_RESTIC_BIN:-}" && -n "${ARCHIVE_VERIFY_REPOSITORY_PATH:-}" ]]; then
    if [[ ! "$ARCHIVE_VERIFY_RESTIC_BIN" =~ ^/[A-Za-z0-9._/-]+$ ||
      ! "$ARCHIVE_VERIFY_REPOSITORY_PATH" =~ ^/[A-Za-z0-9._/-]+$ ||
      "$ARCHIVE_REPOSITORY" != "sftp:$ARCHIVE_SSH_TARGET:$ARCHIVE_VERIFY_REPOSITORY_PATH" ]]; then
      echo "Remote verification paths must match the SFTP backup repository" >&2
      return 1
    fi
    ssh -i "$ARCHIVE_SSH_KEY" -o BatchMode=yes -o StrictHostKeyChecking=yes \
      -o ConnectTimeout="$connect_timeout_seconds" -o ServerAliveInterval=30 \
      -o ServerAliveCountMax=3 "$ARCHIVE_SSH_TARGET" \
      "$ARCHIVE_VERIFY_RESTIC_BIN --repo '$ARCHIVE_VERIFY_REPOSITORY_PATH' --password-file /dev/stdin check --read-data" \
      < "$RESTIC_PASSWORD_FILE"
  else
    restic_command check --read-data
  fi
}

completed_source_ready_for_cleanup() {
  # Explicit reset is for an operator-authorized fresh backup test. Routine
  # cleanup must preserve interrupted Time Machine backups so they can resume.
  [[ "${1:-backup}" != "reset" ]] || return 0
  python3 - "$TIME_MACHINE_SOURCE" "${PORTABLE_EXPORT_STATUS_FILE:-}" <<'PY'
import datetime, json, os, pathlib, plistlib, sys
root = pathlib.Path(sys.argv[1])
bundles = [p for p in root.iterdir() if p.is_dir() and p.suffix in ('.sparsebundle', '.backupbundle')]
try:
    for bundle in bundles:
        with (bundle / 'com.apple.TimeMachine.Results.plist').open('rb') as handle:
            if plistlib.load(handle).get('RESULT') != 0:
                raise ValueError('The last Time Machine backup did not finish successfully')
    if sys.argv[2]:
        status = json.loads(pathlib.Path(sys.argv[2]).read_text())
        if status.get('state') != 'success' or status.get('verified') is not True:
            raise ValueError('Waiting for a verified browsable export from the Mac')
        completed = datetime.datetime.fromisoformat(status['lastSuccessAt'].replace('Z', '+00:00')).timestamp()
        for bundle in bundles:
            for directory, dirs, files in os.walk(bundle, onerror=lambda error: (_ for _ in ()).throw(error)):
                if pathlib.Path(directory).lstat().st_ctime > completed or any((pathlib.Path(directory) / name).lstat().st_ctime > completed for name in [*dirs, *files]):
                    raise ValueError('The local backup changed after the last browsable export')
except Exception as error:
    print(error, file=sys.stderr)
    sys.exit(1)
PY
}

source_changed_recently() {
  local changes
  changes="$(find "$TIME_MACHINE_SOURCE" -type f -cmin "-${quiet_minutes}" -print -quit)" || return 0
  [[ -n "$changes" ]]
}

source_changed_since() {
  local marker="$1"
  local changes
  changes="$(find "$TIME_MACHINE_SOURCE" -cnewer "$marker" -print -quit)" || return 0
  [[ -n "$changes" ]]
}

# Fail closed: an unavailable SMB lock report must never authorize deletion.
source_has_open_files() {
  local locks
  locks="$(smbstatus --locks --json 2>/dev/null)" || return 0
  jq -e --arg prefix "${TIME_MACHINE_SOURCE%/}/" --arg source "$TIME_MACHINE_SOURCE" '
    if (.open_files | type) != "object" then true
    else [.open_files | keys[] | select(. == $source or startswith($prefix))] | length > 0 end
  ' <<<"$locks" >/dev/null 2>&1
  local result=$?
  [[ "$result" -ne 1 ]]
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

if ! find "$TIME_MACHINE_SOURCE" -mindepth 1 -maxdepth 1 -type d \
  \( -name '*.sparsebundle' -o -name '*.backupbundle' \) -print -quit | grep -q .; then
  write_status "waiting" "SSD is ready for a new Time Machine backup"
  exit 0
fi

if source_changed_recently; then
  write_status "waiting" \
    "Waiting ${quiet_minutes} minutes for Time Machine to become quiet"
  exit 0
fi
if source_has_open_files; then
  write_status "waiting" \
    "Waiting for the Mac to close the Time Machine connection"
  exit 0
fi

write_status "running" "Archiving the idle Time Machine bundle directly to Unraid"
if ! tailnet_peer_reachable; then
  write_status "error" "Unraid is unreachable; the Pi will retry automatically"
  exit 1
fi

# A terminated Restic process can leave a stale lock. The local flock above
# guarantees there is no other Pi archive process while it is cleared.
if ! unlock_output="$(restic_command unlock 2>&1)"; then
  write_status "error" "The Unraid archive could not be unlocked; retry scheduled" "" "" "" null "${unlock_output: -4000}"
  exit 1
fi

change_marker="$(mktemp "$runtime_dir/pirouter-archive-change-marker.XXXXXX")"
backup_output="$(mktemp "$runtime_dir/pirouter-archive-output.XXXXXX")"
backup_errors="$(mktemp "$runtime_dir/pirouter-archive-errors.XXXXXX")"
samba_stopped=false
cleanup() {
  if [[ -n "${restic_pid:-}" ]] && kill -0 "$restic_pid" 2>/dev/null; then
    kill -TERM "$restic_pid" 2>/dev/null || true
    wait "$restic_pid" 2>/dev/null || true
  fi
  if [[ "$samba_stopped" == true ]]; then
    systemctl start "$samba_service" || true
  fi
  rm -f -- "$change_marker" "$backup_output" "$backup_errors"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
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
    --host "$backup_host" --tag "$backup_tag" --json > "$backup_output" 2> "$backup_errors" &
restic_pid=$!
set -e

time_machine_changed=false
latest_progress_json=null
while kill -0 "$restic_pid" 2>/dev/null; do
  sleep "$copy_guard_seconds"
  if source_changed_since "$change_marker" || source_has_open_files; then
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
restic_pid=""
set -e

summary="$(jq -sc '[.[] | select(.message_type == "summary")] | last // {}' \
  "$backup_output" 2>/dev/null || echo '{}')"
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
    "The archive did not finish; the local backup was kept for an automatic retry" \
    "" "" "" "$latest_progress_json" "Restic exit code $backup_result. $(tail -c 3500 "$backup_errors")"
  if [[ "$backup_result" -eq 0 ]]; then
    backup_result=1
  fi
  exit "$backup_result"
fi

completed_at="$(date --iso-8601=seconds)"
short_snapshot="${snapshot_id:0:8}"
write_status "success" "Unraid archive snapshot ${short_snapshot} completed" \
  "$completed_at" "$snapshot_id" "$data_added"

if [[ "$cleanup_after_upload" == true ]]; then
  if ! cleanup_readiness="$(completed_source_ready_for_cleanup "${1:-backup}" 2>&1)"; then
    write_status "waiting" "Archive is safe; local backup kept until backup and export finish" \
      "$completed_at" "$snapshot_id" "$data_added" null "$cleanup_readiness"
    exit 0
  fi
  write_status "running" "Upload complete; verifying the archive before freeing SSD space" \
    "$completed_at" "$snapshot_id" "$data_added"
  if ! verify_archive_data > "$backup_errors" 2>&1 ||
    ! restic_command cat snapshot "$snapshot_id" >/dev/null 2>> "$backup_errors"; then
    write_status "error" "Archive verification failed; the local backup was kept" \
      "$completed_at" "$snapshot_id" "$data_added" null "$(tail -c 4000 "$backup_errors")"
    exit 1
  fi
  if ! mountpoint -q "$TIME_MACHINE_SOURCE" || source_has_open_files || source_changed_since "$change_marker"; then
    write_status "waiting" "Archive is safe; local cleanup deferred because the share changed or is busy" \
      "$completed_at" "$snapshot_id" "$data_added"
    exit 0
  fi
  # Hold off new SMB writers during the final check and removal. Routing and
  # other Pi services keep running. The EXIT trap restores the share on failure.
  if ! systemctl is-active --quiet "$samba_service"; then
    write_status "error" "Archive is safe; cleanup requires the SMB service to be running"
    exit 1
  fi
  samba_stopped=true
  if ! systemctl stop "$samba_service" 2> "$backup_errors"; then
    write_status "error" "Archive is safe; the SMB share could not be paused for cleanup" \
      "" "" "" null "$(tail -c 4000 "$backup_errors")"
    exit 1
  fi
  if systemctl is-active --quiet "$samba_service" || source_changed_since "$change_marker"; then
    write_status "waiting" "Archive is safe; local backup changed before cleanup and was kept"
    exit 0
  fi
  # Only backup bundles directly inside the dedicated SSD mount are removed.
  # Preserve the mount, permissions, lost+found, and share support files.
  while IFS= read -r -d '' bundle; do
    if ! rm -rf --one-file-system -- "$bundle" 2> "$backup_errors"; then
      write_status "error" "Archive is safe; removing the local backup failed" \
        "" "" "" null "Could not remove $bundle. $(tail -c 3500 "$backup_errors")"
      exit 1
    fi
  done < <(find "$TIME_MACHINE_SOURCE" -mindepth 1 -maxdepth 1 -type d \
    \( -name '*.sparsebundle' -o -name '*.backupbundle' \) -print0)
  status_temp="$(mktemp)"
  jq --arg now "$(date --iso-8601=seconds)" '.lastCleanupAt = $now' "$status_file" > "$status_temp"
  install -m 0644 "$status_temp" "$status_file"
  rm -f -- "$status_temp"
  if ! systemctl start "$samba_service" 2> "$backup_errors"; then
    write_status "error" "Archive is safe and SSD cleared, but the SMB share could not restart" \
      "" "" "" null "$(tail -c 4000 "$backup_errors")"
    exit 1
  fi
  samba_stopped=false
  write_status "success" "Archive ${short_snapshot} verified; local backup removed and SSD ready" \
    "$completed_at" "$snapshot_id" "$data_added"
fi

if ! restic_command forget \
  --host "$backup_host" \
  --tag "$backup_tag" \
  --keep-daily "${KEEP_DAILY:-14}" \
  --keep-weekly "${KEEP_WEEKLY:-8}" \
  --keep-monthly "${KEEP_MONTHLY:-12}"; then
  write_status "success" \
    "Snapshot ${short_snapshot} is safe; remote retention cleanup was deferred" \
    "$completed_at" "$snapshot_id" "$data_added"
fi
