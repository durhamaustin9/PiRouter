#!/bin/bash
set -uo pipefail

config_file="${PIROUTER_PORTABLE_EXPORT_CONFIG:-${PIROUTER_SUPPORT_DIR:-${HOME}/Library/Application Support/PiRouter}/portable-export.env}"
if [[ ! -r "$config_file" ]]; then
  echo "Portable-export configuration is missing: $config_file" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$config_file"

if [[ "${EXPORT_ENABLED:-true}" == false || "${PIROUTER_NO_BACKGROUND:-0}" == 1 ]]; then
  exit 0
fi
if [[ "${EXPORT_ENABLED:-true}" != true ]]; then
  echo "EXPORT_ENABLED must be true or false." >&2
  exit 1
fi

: "${PIROUTER_URL:?PIROUTER_URL is required}"
: "${PIROUTER_TOKEN:?PIROUTER_TOKEN is required}"
: "${EXPORT_HOST:?EXPORT_HOST is required}"
: "${EXPORT_ROOT:?EXPORT_ROOT is required}"
: "${EXPORT_SSH_KEY:?EXPORT_SSH_KEY is required}"

remote_pirouter_url="$PIROUTER_URL"
local_pirouter_urls="${PIROUTER_LOCAL_URLS:-${PIROUTER_LOCAL_URL:-}}"
automatic="${AUTOMATIC_EXPORT:-true}"
automatic_hours="${AUTOMATIC_EXPORT_MIN_HOURS:-24}"
continuous_export="${CONTINUOUS_EXPORT:-false}"
bandwidth_limit_kb="${RSYNC_BANDWIDTH_LIMIT_KB:-40000}"
support_dir="$(dirname "$config_file")"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# The distributed app carries its own interpreter; source checkouts retain the
# existing system-Python fallback. Keep signed app resources read-only at runtime.
python_home="$script_dir/../Frameworks/Python.framework/Versions/Current"
if [[ -z "${PIROUTER_PYTHON:-}" && -x "$python_home/bin/python3" ]]; then
  export PIROUTER_PYTHON="$python_home/bin/python3"
  export PYTHONHOME="$python_home"
  if [[ -f "$python_home/etc/pirouter-ca.pem" ]]; then
    export SSL_CERT_FILE="$python_home/etc/pirouter-ca.pem"
  fi
fi
export PYTHONDONTWRITEBYTECODE=1
export PYTHONNOUSERSITE=1
wrapper_path="$script_dir/${BASH_SOURCE[0]##*/}"
job_file="$support_dir/portable-export-job.json"
mount_state_file="$support_dir/portable-export-mount.json"
handoff_file="$support_dir/archive-handoff.json"
folder_turn_file="$support_dir/folder-export-turn.json"
automatic_marker="$support_dir/last-automatic-check"
response_file=""
tmutil_output_file=""
tmutil_result_file=""
tmutil_pid=""
rsync_errors=""
verification_output=""
source_manifest=""
transfer_pid=""
pinned_snapshot=""
pinned_path=""
request_id=""
archive_configured=""
archive_state=""
archive_attempt=""
archive_service_active=""
archive_needs_archive=""
archive_success=""
archive_snapshot_id=""
session_mounted=false
archive_turn_observed=false
folder_turn_active=false
turn_progress_before=""
turn_progress_started=""
transfer_result=75
export_session_seconds=600

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Portable Time Machine exports must run on macOS." >&2
  exit 1
fi
pirouter_urls=()
append_pirouter_url() {
  local candidate="$1"
  local existing=""
  [[ -n "$candidate" ]] || return 0
  if [[ ! "$candidate" =~ ^https?://[^[:space:]]+$ ]]; then
    echo "PiRouter URLs must use HTTP or HTTPS." >&2
    exit 1
  fi
  candidate="${candidate%/}"
  # Bash 3.2 treats an empty array expansion as unbound under `set -u`.
  for existing in "${pirouter_urls[@]-}"; do
    [[ "$existing" != "$candidate" ]] || return 0
  done
  pirouter_urls+=("$candidate")
}

if [[ -n "$local_pirouter_urls" ]]; then
  IFS=',' read -r -a configured_local_urls <<< "$local_pirouter_urls"
  for configured_url in "${configured_local_urls[@]}"; do
    append_pirouter_url "$configured_url"
  done
fi
append_pirouter_url "$remote_pirouter_url"
PIROUTER_URL="${pirouter_urls[0]}"

if [[ ! "$EXPORT_HOST" =~ ^[A-Za-z0-9._@:-]+$ ]]; then
  echo "EXPORT_HOST contains unsupported characters." >&2
  exit 1
fi
if [[ ! "$EXPORT_ROOT" =~ ^/mnt/user/[A-Za-z0-9._-]+$ ]]; then
  echo "EXPORT_ROOT must be one directory directly below /mnt/user." >&2
  exit 1
fi
if [[ ! -r "$EXPORT_SSH_KEY" ]]; then
  echo "The export SSH key is unavailable: $EXPORT_SSH_KEY" >&2
  exit 1
fi
if [[ ! "$automatic_hours" =~ ^[1-9][0-9]*$ ]]; then
  echo "AUTOMATIC_EXPORT_MIN_HOURS must be a positive integer." >&2
  exit 1
fi
if [[ ! "$bandwidth_limit_kb" =~ ^[1-9][0-9]*$ ]]; then
  echo "RSYNC_BANDWIDTH_LIMIT_KB must be a positive integer." >&2
  exit 1
fi

mkdir -p "$support_dir"
if [[ "${PIROUTER_EXPORT_LOCKED:-}" != 1 ]]; then
  exec "${PIROUTER_PYTHON:-/usr/bin/python3}" "$script_dir/portable-export-lock.py" \
    "$support_dir/export.lock" "$0" "$@"
fi

ssh_args=(
  -i "$EXPORT_SSH_KEY"
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=4
)

stop_children() {
  if [[ -n "$transfer_pid" ]]; then
    if kill -0 "$transfer_pid" 2>/dev/null; then
      kill -TERM "$transfer_pid" 2>/dev/null || true
    fi
    wait "$transfer_pid" 2>/dev/null || true
    transfer_pid=""
  fi
  if [[ -n "$tmutil_pid" ]]; then
    if kill -0 "$tmutil_pid" 2>/dev/null; then
      kill -TERM "$tmutil_pid" 2>/dev/null || true
    fi
    wait "$tmutil_pid" 2>/dev/null || true
    tmutil_pid=""
  fi
  cd / 2>/dev/null || true
}

cleanup() {
  local cleanup_result=0
  stop_children
  finish_folder_turn "$transfer_result" || true
  if ! release_for_archive; then
    cleanup_result=1
    echo "The backup image is still busy; its archive handoff will retry safely." >&2
    if [[ -n "$request_id" ]]; then
      api_update "$request_id" paused \
        "Waiting to close the idle backup image before archiving" \
        "$pinned_snapshot" >/dev/null 2>&1 || true
    fi
  fi
  [[ -z "$response_file" ]] || rm -f -- "$response_file"
  [[ -z "$tmutil_output_file" ]] || rm -f -- "$tmutil_output_file"
  [[ -z "$tmutil_result_file" ]] || rm -f -- "$tmutil_result_file"
  [[ -z "$rsync_errors" ]] || rm -f -- "$rsync_errors"
  [[ -z "$verification_output" ]] || rm -f -- "$verification_output"
  [[ -z "$source_manifest" ]] || rm -f -- "$source_manifest"
  return "$cleanup_result"
}
trap cleanup EXIT
# A signal caught while Bash is waiting for the rsync pipeline can otherwise
# replace PIPESTATUS with a successful cleanup command and publish a partial.
trap 'exit 130' INT
trap 'exit 143' TERM

response_file="$(mktemp -t pirouter-export-response.XXXXXX)"

json_value() {
  /usr/bin/plutil -extract "$2" raw -o - "$1" 2>/dev/null || true
}

api_get() {
  local candidate=""
  for candidate in "${pirouter_urls[@]}"; do
    if /usr/bin/curl --fail --silent \
      --connect-timeout 2 --max-time 20 \
      -H "Authorization: Bearer $PIROUTER_TOKEN" \
      -H "Accept: application/json" \
      "$candidate/api/portable-export" -o "$response_file"; then
      PIROUTER_URL="$candidate"
      return 0
    fi
  done
  return 1
}

api_request_automatic() {
  local candidate=""
  local ordered_urls=("$PIROUTER_URL" "${pirouter_urls[@]}")
  for candidate in "${ordered_urls[@]}"; do
    if /usr/bin/curl --fail --silent \
      --connect-timeout 2 --max-time 20 \
      -X POST \
      -H "Authorization: Bearer $PIROUTER_TOKEN" \
      -H "Accept: application/json" \
      "$candidate/api/portable-export" -o "$response_file"; then
      PIROUTER_URL="$candidate"
      return 0
    fi
  done
  return 1
}

api_update() {
  local request_id="$1"
  local state="$2"
  local message="$3"
  local snapshot_name="${4:-}"
  local bytes_copied="${5:-null}"
  local error_detail="${6:-}"
  local body
  [[ "$request_id" =~ ^[0-9a-fA-F-]{36}$ ]] || return 1
  body="$("${PIROUTER_PYTHON:-/usr/bin/python3}" - "$request_id" "$state" "$message" "$snapshot_name" "$bytes_copied" "$error_detail" <<'PY'
import json, sys
request, state, message, snapshot, size, detail = sys.argv[1:]
print(json.dumps(dict(requestId=request, state=state, message=message,
    lastSnapshotName=(snapshot or None) if state == 'success' else None,
    activeSnapshotName=(snapshot or None) if state != 'success' else None,
    bytesCopied=int(size) if size.isdigit() else None,
    errorDetail=detail[-4000:] or None, verified=state == 'success')))
PY
)" || return 1
  local candidate=""
  local ordered_urls=("$PIROUTER_URL" "${pirouter_urls[@]}")
  for candidate in "${ordered_urls[@]}"; do
    if /usr/bin/curl --fail --silent \
      --connect-timeout 2 --max-time 20 \
      -X PATCH \
      -H "Authorization: Bearer $PIROUTER_TOKEN" \
      -H "Content-Type: application/json" \
      -H "Accept: application/json" \
      --data-binary "$body" \
      "$candidate/api/portable-export" -o "$response_file"; then
      PIROUTER_URL="$candidate"
      return 0
    fi
  done
  return 1
}

api_archive_trigger() {
  local candidate=""
  for candidate in "${pirouter_urls[@]}"; do
    if /usr/bin/curl --fail --silent --connect-timeout 2 --max-time 20 \
      -X POST -H "Authorization: Bearer $PIROUTER_TOKEN" \
      -H "Accept: application/json" \
      "$candidate/api/archive-backup" -o "$response_file"; then
      return 0
    fi
  done
  return 1
}

read_archive_coordination() {
  if ! "${PIROUTER_PYTHON:-/usr/bin/python3}" - "$response_file" <<'PYCOORD'
import json, sys
try:
    value = json.load(open(sys.argv[1]))['archiveCoordination']
    if not isinstance(value, dict) or not isinstance(value.get('configured'), bool):
        raise ValueError('Unknown archive configuration')
    if value['configured']:
        if value.get('state') not in ('waiting', 'running', 'maintenance', 'success', 'error'):
            raise ValueError('Unknown archive state')
        if not isinstance(value.get('serviceActive'), bool) or not isinstance(value.get('needsArchive'), bool):
            raise ValueError('Unknown archive service or source state')
        if value.get('lastAttemptAt') is not None and not isinstance(value['lastAttemptAt'], str):
            raise ValueError('Invalid archive attempt')
except (OSError, ValueError, KeyError, TypeError):
    sys.exit(1)
PYCOORD
  then
    return 1
  fi
  archive_configured="$(json_value "$response_file" archiveCoordination.configured)"
  archive_state="$(json_value "$response_file" archiveCoordination.state)"
  archive_attempt="$(json_value "$response_file" archiveCoordination.lastAttemptAt)"
  [[ "$archive_attempt" != null ]] || archive_attempt=""
  archive_service_active="$(json_value "$response_file" archiveCoordination.serviceActive)"
  archive_needs_archive="$(json_value "$response_file" archiveCoordination.needsArchive)"
  archive_success="$(json_value "$response_file" archiveCoordination.lastSuccessAt)"
  archive_snapshot_id="$(json_value "$response_file" archiveCoordination.lastSnapshotId)"
}

try_folder_turn() {
  if [[ "$archive_service_active" != false ]]; then return 1; fi
  if "${PIROUTER_PYTHON:-/usr/bin/python3}" - "$folder_turn_file" "$archive_snapshot_id" "$archive_success" "$pinned_snapshot" "$continuous_export" "$job_file" <<'PYTURN'
import datetime, json, os, re, sys, tempfile
filename, archive, succeeded, snapshot, requested_continuous, job_filename = sys.argv[1:]
if not re.fullmatch('[a-fA-F0-9]{64}', archive): sys.exit(1)
try:
    completed = datetime.datetime.fromisoformat(succeeded.replace('Z', '+00:00'))
    if completed.tzinfo is None: sys.exit(1)
except (ValueError, TypeError): sys.exit(1)
try:
    with open(filename) as handle: previous = json.load(handle)
    if not isinstance(previous, dict): sys.exit(1)
except FileNotFoundError:
    previous = {}
except (OSError, ValueError): sys.exit(1)
continuous = False
if requested_continuous == 'true' and re.fullmatch(r'\d{4}-\d{2}-\d{2}-\d{6}', snapshot):
    try:
        with open(job_filename) as handle: job = json.load(handle)
        continuous = isinstance(job, dict) and job.get('snapshotName') == snapshot \
            and isinstance(job.get('sourcePath'), str) and os.path.isabs(job['sourcePath'])
    except (OSError, ValueError, TypeError): sys.exit(1)
if previous.get('archiveSnapshotId') == archive:
    if not continuous and previous.get('consumed') is not False: sys.exit(1)
    if snapshot and previous.get('snapshotName') and previous['snapshotName'] != snapshot: sys.exit(1)
    value = previous
else:
    try:
        prior_completed = datetime.datetime.fromisoformat(previous['archiveSuccessAt'].replace('Z', '+00:00'))
        if prior_completed >= completed: sys.exit(1)
    except (KeyError, ValueError, TypeError): pass
    value = {'archiveSnapshotId': archive, 'archiveSuccessAt': succeeded,
             'grantedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
             'snapshotName': snapshot or None, 'consumed': False, 'remainingSeconds': 3600}
try:
    granted = datetime.datetime.fromisoformat(value['grantedAt'].replace('Z', '+00:00'))
    expires = datetime.datetime.fromisoformat(value['expiresAt'].replace('Z', '+00:00')) if value.get('expiresAt') else granted + datetime.timedelta(hours=6)
    remaining = int(value.get('remainingSeconds', 3600))
    if granted.tzinfo is None or expires.tzinfo is None or not 0 <= remaining <= 3600: sys.exit(1)
    if not continuous and (remaining == 0 or datetime.datetime.now(datetime.timezone.utc) >= expires): sys.exit(1)
except (KeyError, ValueError, TypeError): sys.exit(1)
value['mode'] = 'continuous' if continuous else 'bounded'
if continuous:
    # The completed raw archive protects this pinned unfinished snapshot. Keep
    # copying it while online; live archive and Time Machine checks still run
    # at every session boundary, including after any retry or reconnect.
    value['consumed'] = False
value['expiresAt'] = expires.isoformat()
value['remainingSeconds'] = remaining
if snapshot: value['snapshotName'] = snapshot
handle, temporary = tempfile.mkstemp(prefix='.folder-turn-', dir=os.path.dirname(filename))
try:
    with os.fdopen(handle, 'w') as output:
        json.dump(value, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, filename)
    directory = os.open(os.path.dirname(filename), os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)
finally:
    if os.path.exists(temporary): os.unlink(temporary)
PYTURN
  then
    local remaining=""
    remaining="$(json_value "$folder_turn_file" remainingSeconds)"
    export_session_seconds=600
    if [[ "$(json_value "$folder_turn_file" mode)" != continuous ]]; then
      [[ "$remaining" =~ ^[1-9][0-9]*$ ]] || return 1
      if (( remaining < export_session_seconds )); then export_session_seconds="$remaining"; fi
    fi
    folder_turn_active=true
    rm -f -- "$handoff_file"
    return 0
  fi
  return 1
}

read_transfer_progress() {
  "${PIROUTER_PYTHON:-/usr/bin/python3}" - "$support_dir/jobs/${snapshot_name:-$pinned_snapshot}.sqlite3" <<'PYPROGRESS'
import pathlib, sqlite3, sys
filename = pathlib.Path(sys.argv[1])
if not filename.exists():
    print('0 0')
    sys.exit(0)
try:
    with sqlite3.connect(filename.resolve().as_uri() + '?mode=ro', uri=True) as database:
        values = dict(database.execute("SELECT key,value FROM job WHERE key IN ('bytes_copied','files_copied')"))
    size, count = int(values.get('bytes_copied', 0)), int(values.get('files_copied', 0))
    if min(size, count) < 0: raise ValueError('Invalid progress')
    print(size, count)
except (OSError, ValueError, sqlite3.Error): sys.exit(1)
PYPROGRESS
}

finish_folder_turn() {
  [[ "$folder_turn_active" == true && -n "$turn_progress_before" ]] || return 0
  local after=""
  local completed=""
  local turn_result=0
  after="$(read_transfer_progress)" || return 1
  completed="$("${PIROUTER_PYTHON:-/usr/bin/python3}" -c 'import time; print(time.time())')" || return 1
  "${PIROUTER_PYTHON:-/usr/bin/python3}" - "$folder_turn_file" "$turn_progress_before" "$after" "$1" "$turn_progress_started" "$completed" "${export_session_seconds:-600}" <<'PYFINISHTURN'
import datetime, json, math, os, sys, tempfile
filename, before, after, result, started, stopped, session_cap = sys.argv[1:]
try:
    previous_bytes, previous_files = map(int, before.split())
    current_bytes, current_files = map(int, after.split())
    advanced = current_bytes > previous_bytes or current_files > previous_files
    if result != '0' and not advanced: sys.exit(2)
    with open(filename) as source: value = json.load(source)
    cap = max(1, min(600, int(session_cap)))
    duration = float(stopped) - float(started)
    # Python 3.9 on macOS has process-local monotonic origins, so separate
    # invocations use epoch time. Clock changes must not create extra credit.
    if not math.isfinite(duration) or duration < 0:
        elapsed = cap if advanced else 0
    else:
        elapsed = min(cap, max(1, math.ceil(duration))) if advanced else 0
    remaining = max(0, int(value.get('remainingSeconds', 3600)) - elapsed)
    consumed = result == '0' or (remaining == 0 and value.get('mode') != 'continuous')
    value['consumed'] = consumed
    value['remainingSeconds'] = remaining
    value['usefulSecondsUsed'] = int(value.get('usefulSecondsUsed', 0)) + elapsed
    value['lastProgressAt'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    if consumed: value['consumedAt'] = value['lastProgressAt']
    value['bytesAdvanced'] = max(0, current_bytes - previous_bytes)
    value['filesAdvanced'] = max(0, current_files - previous_files)
    handle, temporary = tempfile.mkstemp(prefix='.folder-turn-', dir=os.path.dirname(filename))
    try:
        with os.fdopen(handle, 'w') as output:
            json.dump(value, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, filename)
        directory = os.open(os.path.dirname(filename), os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)
except (OSError, ValueError, TypeError): sys.exit(1)
sys.exit(0 if consumed else 3)
PYFINISHTURN
  turn_result=$?
  if [[ "$turn_result" -eq 0 ]]; then
    folder_turn_active=false
  elif [[ "$turn_result" -ne 2 && "$turn_result" -ne 3 ]]; then
    return 1
  fi
  turn_progress_before=""
  turn_progress_started=""
}

write_archive_handoff() {
  "${PIROUTER_PYTHON:-/usr/bin/python3}" - "$handoff_file" "${1:-false}" "${2:-}" <<'PYHANDOFF'
import datetime, json, os, sys, tempfile
filename, known, attempt = sys.argv[1:]
try:
    with open(filename) as handle:
        previous = json.load(handle)
except (OSError, ValueError):
    previous = {}
if not isinstance(previous, dict):
    previous = {}
handle, temporary = tempfile.mkstemp(prefix='.archive-handoff-', dir=os.path.dirname(filename))
try:
    with os.fdopen(handle, 'w') as output:
        json.dump({'baselineKnown': known == 'true', 'baselineAttemptAt': attempt or None,
                   'createdAt': previous.get('createdAt') or datetime.datetime.now(datetime.timezone.utc).isoformat()}, output)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, filename)
    directory = os.open(os.path.dirname(filename), os.O_RDONLY)
    try: os.fsync(directory)
    finally: os.close(directory)
finally:
    if os.path.exists(temporary): os.unlink(temporary)
PYHANDOFF
}

begin_archive_handoff() {
  # Persist before network access. An offline Pi must not cause a later launch
  # to remount the image before its pending archive has had an idle window.
  write_archive_handoff false "" || return 1
  if api_get && read_archive_coordination; then
    if [[ "$archive_configured" == false ]]; then
      rm -f -- "$handoff_file"
      return 0
    fi
    if try_folder_turn; then
      # A completed archive earns a bounded useful folder window. Failed SSH
      # handshakes retain its remaining credit instead of restarting quiet time.
      return 0
    fi
    if [[ "$archive_service_active" == false && "$archive_needs_archive" == false ]]; then
      rm -f -- "$handoff_file"
      return 0
    fi
    write_archive_handoff true "$archive_attempt" || return 1
    if [[ "$archive_service_active" == false ]]; then
      api_archive_trigger >/dev/null 2>&1 || true
    fi
  fi
}

pause_for_archive() {
  local message="$1"
  if [[ "$request_id" =~ ^[0-9a-fA-F-]{36}$ ]]; then
    api_update "$request_id" paused "$message" "$pinned_snapshot" >/dev/null 2>&1 || true
  fi
}

release_for_archive() {
  local had_receipt=false
  local adoption_path=""
  if [[ ! -r "$mount_state_file" && -r "$job_file" ]]; then
    adoption_path="$(json_value "$job_file" sourcePath)"
    if [[ -n "$adoption_path" ]]; then
      "${PIROUTER_PYTHON:-/usr/bin/python3}" "$script_dir/portable-export-mounts.py" capture \
        --statefile "$mount_state_file" --source "$adoption_path" --if-mounted || return 1
    fi
  fi
  [[ ! -r "$mount_state_file" ]] || had_receipt=true
  if [[ "$had_receipt" == true ]]; then
    "${PIROUTER_PYTHON:-/usr/bin/python3}" "$script_dir/portable-export-mounts.py" release \
      --statefile "$mount_state_file" || return 1
  fi
  if [[ "$had_receipt" == true || "$session_mounted" == true ]]; then
    session_mounted=false
    begin_archive_handoff || return 1
  fi
  return 0
}

archive_allows_export() {
  local baseline=""
  local baseline_known=""
  if ! api_get || ! read_archive_coordination; then
    echo "Waiting for reliable archive coordination before opening the backup image."
    pause_for_archive "Waiting for Pi archive status; folder progress is saved"
    return 1
  fi
  if [[ "$archive_configured" == false ]]; then
    rm -f -- "$handoff_file"
    return 0
  fi
  if [[ "$archive_service_active" == true ]]; then
    echo "The Pi archive is active; folder exports will resume after it finishes."
    pause_for_archive "Pi is archiving; folder progress is saved"
    return 1
  fi
  if try_folder_turn; then
    return 0
  fi
  if [[ -r "$handoff_file" ]]; then
    baseline_known="$(json_value "$handoff_file" baselineKnown)"
    baseline="$(json_value "$handoff_file" baselineAttemptAt)"
    [[ "$baseline" != null ]] || baseline=""
    if [[ "$baseline_known" != true ]]; then
      write_archive_handoff true "$archive_attempt" || return 1
      api_archive_trigger >/dev/null 2>&1 || true
      pause_for_archive "Waiting for the Pi archive check; folder progress is saved"
      return 1
    fi
    if [[ "$archive_needs_archive" == false ]]; then
      rm -f -- "$handoff_file"
      return 0
    fi
    if [[ -n "$archive_attempt" && "$archive_attempt" != "$baseline" && "$archive_state" == error ]]; then
      # A genuine attempted archive gets priority, but a terminal failure must
      # not permanently starve useful bounded folder-export sessions.
      rm -f -- "$handoff_file"
      archive_turn_observed=true
    else
      api_archive_trigger >/dev/null 2>&1 || true
      echo "Keeping the backup image closed while the Pi waits for its archive window."
      pause_for_archive "Waiting for the Pi archive window; folder progress is saved"
      return 1
    fi
  fi
  if [[ "$archive_needs_archive" == true && "$archive_turn_observed" != true ]]; then
    write_archive_handoff true "$archive_attempt" || return 1
    api_archive_trigger >/dev/null 2>&1 || true
    echo "The idle backup is being handed to the Pi archive before folder exports resume."
    pause_for_archive "Waiting for the Pi archive check; folder progress is saved"
    return 1
  fi
  return 0
}

latest_snapshot() {
  if /usr/bin/tmutil status | /usr/bin/grep -q 'Running = 1'; then
    return 75
  fi

  if [[ -n "${pinned_snapshot:-}" && -n "${pinned_path:-}" && -d "$pinned_path" ]]; then
    # A saved immutable snapshot can already be mounted after a reconnect.
    if [[ -n "$(/usr/bin/find "$pinned_path" -mindepth 1 -maxdepth 1 -type d -print -quit 2>/dev/null)" ]]; then
      cd "$pinned_path" || return 78
      snapshot_path="$pinned_path"
      snapshot_name="$pinned_snapshot"
      return 0
    fi
  fi

  tmutil_output_file="$(mktemp -t pirouter-tmutil.XXXXXX)"
  tmutil_result_file="$(mktemp -t pirouter-tmutil-result.XXXXXX)"
  # Bash 3.2 cannot reliably wait on a process-substitution PID. The wrapper
  # waits on its own child and records tmutil's exit status explicitly.
  exec 3< <(
    # Mount helpers must not retain the exporter lock after their parent exits.
    exec 9>&-
    if [[ -n "${pinned_snapshot:-}" ]]; then
      /usr/bin/tmutil listbackups -m 2>"$tmutil_output_file" &
    else
      /usr/bin/tmutil latestbackup -m 2>"$tmutil_output_file" &
    fi
    mount_pid=$!
    trap 'kill -TERM "$mount_pid" 2>/dev/null || true; wait "$mount_pid" 2>/dev/null || true; exit 143' TERM INT
    wait "$mount_pid"
    printf '%s\n' "$?" > "$tmutil_result_file"
  )
  tmutil_pid=$!
  local candidate=""
  local read_result=0
  local listed_candidate=""
  while true; do
    IFS= read -r -t 120 listed_candidate <&3
    read_result=$?
    if [[ "$read_result" -ne 0 ]]; then break; fi
    if [[ -z "${pinned_snapshot:-}" || "${listed_candidate##*/}" == "$pinned_snapshot.backup" ]]; then
      candidate="$listed_candidate"
      break
    fi
  done
  if [[ -z "$candidate" && "$read_result" -eq 0 ]]; then read_result=77; fi
  if [[ "$read_result" -ne 0 ]]; then
    kill -TERM "$tmutil_pid" 2>/dev/null || true
    wait "$tmutil_pid" 2>/dev/null || true
    exec 3<&-
    tmutil_pid=""
    if [[ "$read_result" -gt 128 ]]; then
      return 124
    fi
    return 77
  fi
  if [[ -z "$candidate" ]] || ! cd "$candidate" 2>/dev/null; then
    kill -TERM "$tmutil_pid" 2>/dev/null || true
    wait "$tmutil_pid" 2>/dev/null || true
    exec 3<&-
    tmutil_pid=""
    return 78
  fi
  # The path can be printed before mounting all of its APFS volumes finishes.
  # Keep the snapshot pinned with cwd, but wait for tmutil's actual exit status
  # before inspecting/copying volume directories.
  local mount_wait=0
  while [[ ! -s "$tmutil_result_file" ]] && (( mount_wait < 120 )); do
    sleep 1
    mount_wait=$((mount_wait + 1))
  done
  if [[ ! -s "$tmutil_result_file" ]]; then
    kill -TERM "$tmutil_pid" 2>/dev/null || true
    wait "$tmutil_pid" 2>/dev/null || true
    exec 3<&-
    tmutil_pid=""
    return 124
  fi
  local mount_result=""
  read -r mount_result < "$tmutil_result_file"
  if [[ "$mount_result" != 0 ]]; then
    exec 3<&-
    tmutil_pid=""
    return 78
  fi
  snapshot_path="$candidate"
  exec 3<&-
  wait "$tmutil_pid" 2>/dev/null || true
  tmutil_pid=""

  snapshot_name="$(basename "$snapshot_path")"
  snapshot_name="${snapshot_name%.backup}"
  if [[ -z "$snapshot_path" ]] ||
    [[ ! "$snapshot_name" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{6}$ ]]; then
    return 78
  fi
  return 0
}

if ! api_get; then
  echo "Unable to reach the PiRouter portable-export API." >&2
  exit 1
fi

request_id="$(json_value "$response_file" requestId)"
[[ "$request_id" =~ ^[0-9a-fA-F-]{36}$ ]] || request_id=""
if [[ -r "$job_file" ]]; then
  pinned_snapshot="$(json_value "$job_file" snapshotName)"
  pinned_path="$(json_value "$job_file" sourcePath)"
  [[ "$pinned_snapshot" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{6}$ ]] || pinned_snapshot=""
fi

# Fetching status does not mount anything. Release any previously registered
# attachment, then re-read archive coordination before deciding to reopen it.
stop_children
if ! release_for_archive; then
  echo "Waiting for the idle backup image to close safely." >&2
  pause_for_archive "Waiting to close the idle backup image before archiving"
  exit 0
fi
if ! archive_allows_export; then
  exit 0
fi

# Coordination requests share a temporary response file; restore the portable
# job response before reading its request ID below.
if ! api_get; then exit 1; fi

state="$(json_value "$response_file" state)"
request_id="$(json_value "$response_file" requestId)"
manual_request=false

if [[ "$state" == "requested" || "$state" == "running" || "$state" == "paused" || ( "$state" == "error" && -n "$request_id" ) ]]; then
  manual_request=true
elif [[ "$automatic" != true ]]; then
  exit 0
else
  now_epoch="$(date +%s)"
  last_automatic_epoch=0
  if [[ -r "$automatic_marker" ]]; then
    read -r last_automatic_epoch < "$automatic_marker" || last_automatic_epoch=0
  fi
  if [[ "$last_automatic_epoch" =~ ^[0-9]+$ ]] &&
    (( now_epoch - last_automatic_epoch < automatic_hours * 3600 )); then
    exit 0
  fi
fi

# Automatic failures also need a request so diagnostics reach the dashboard.
if [[ "$manual_request" != true ]]; then
  if ! api_request_automatic; then exit 1; fi
  request_id="$(json_value "$response_file" status.requestId)"
fi

snapshot_path=""
snapshot_name=""
if [[ -r "$job_file" ]]; then
  pinned_snapshot="$(json_value "$job_file" snapshotName)"
  pinned_path="$(json_value "$job_file" sourcePath)"
  if [[ ! "$pinned_snapshot" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{6}$ ]]; then
    echo "The saved export job is invalid; its progress has been kept." >&2
    exit 1
  fi
fi
if ! archive_allows_export; then
  exit 0
fi
latest_snapshot
snapshot_result=$?
if [[ "$snapshot_result" -ne 0 ]]; then
  if [[ -n "$request_id" ]]; then
    if [[ "$snapshot_result" -eq 75 ]]; then
      api_update "$request_id" paused \
        "Waiting for Time Machine to finish; uploaded work is retained" \
        "$pinned_snapshot" >/dev/null 2>&1 || true
      echo "Time Machine is still backing up; the request will remain queued."
      exit 0
    fi
    api_update "$request_id" paused \
      "Waiting for the saved backup snapshot; uploaded work is retained" \
      "$pinned_snapshot" null "$(cat "$tmutil_output_file" 2>/dev/null) $(/usr/bin/tmutil destinationinfo 2>&1)" \
      >/dev/null 2>&1 || true
  fi
  echo "Unable to mount the latest completed Time Machine snapshot." >&2
  exit 1
fi

if [[ ! "$request_id" =~ ^[0-9a-fA-F-]{36}$ ]]; then
  echo "The Pi returned an invalid portable-export request ID." >&2
  exit 1
fi

# Pin the immutable snapshot before starting any upload. A newer Time Machine
# backup must not redirect an unfinished export into another destination folder.
"${PIROUTER_PYTHON:-/usr/bin/python3}" - "$job_file" "$snapshot_name" "$snapshot_path" <<'PYJOB'
import json, os, sys, tempfile
filename, snapshot, source = sys.argv[1:]
handle, temporary = tempfile.mkstemp(prefix='.export-job-', dir=os.path.dirname(filename))
try:
    with os.fdopen(handle, 'w') as out:
        json.dump({'snapshotName': snapshot, 'sourcePath': source}, out)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temporary, filename)
    directory = os.open(os.path.dirname(filename), os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
finally:
    if os.path.exists(temporary): os.unlink(temporary)
PYJOB
if [[ "$?" -ne 0 ]]; then exit 1; fi

if ! "${PIROUTER_PYTHON:-/usr/bin/python3}" "$script_dir/portable-export-mounts.py" capture \
  --statefile "$mount_state_file" --source "$snapshot_path"; then
  api_update "$request_id" paused \
    "Waiting to identify the backup image safely; upload progress is retained" \
    "$snapshot_name" >/dev/null 2>&1 || true
  exit 0
fi
session_mounted=true

api_update "$request_id" running \
  "Resuming saved snapshot; verified upload progress is retained" \
  "$snapshot_name" >/dev/null 2>&1 || true

# The engine scans incrementally and checkpoints each verified block on Unraid.
# Neither a reconnect nor a large file requires another complete source scan.
joined_urls="$(IFS=,; printf '%s' "${pirouter_urls[*]}")"
if [[ "$folder_turn_active" == true ]]; then
  turn_progress_before="$(read_transfer_progress)" || turn_progress_before=""
  turn_progress_started="$("${PIROUTER_PYTHON:-/usr/bin/python3}" -c 'import time; print(time.time())')" || turn_progress_before=""
fi
PIROUTER_URLS="$joined_urls" \
PIROUTER_TOKEN="$PIROUTER_TOKEN" \
PIROUTER_REQUEST_ID="$request_id" \
  "${PIROUTER_PYTHON:-/usr/bin/python3}" "$script_dir/resumable-portable-export.py" \
  --source "$snapshot_path" \
  --snapshot "$snapshot_name" \
  --state-dir "$support_dir/jobs" \
  --host "$EXPORT_HOST" \
  --root "$EXPORT_ROOT" \
  --ssh-key "$EXPORT_SSH_KEY" \
  --proxy-command "${EXPORT_SSH_PROXY_COMMAND:-}" \
  --receiver-script "$script_dir/portable-export-receiver.php" \
  --session-seconds "$export_session_seconds" \
  --bandwidth-kb "$bandwidth_limit_kb" &
transfer_pid=$!
wait "$transfer_pid"
transfer_result=$?
transfer_pid=""
finish_folder_turn "$transfer_result" || true

if [[ "$transfer_result" -eq 0 ]]; then
  # Keep the job pinned until its successful result reached the dashboard too.
  if api_get && [[ "$(json_value "$response_file" state)" == success ]] &&
    [[ "$(json_value "$response_file" lastSnapshotName)" == "$snapshot_name" ]]; then
    rm -f -- "$job_file"
    printf '%s\n' "$(date +%s)" > "$automatic_marker"
  fi
  exit 0
fi
if [[ "$transfer_result" -eq 75 ]]; then
  echo "Export paused; verified blocks will resume on the next connection."
  exit 0
fi
if [[ "$transfer_result" -eq 76 ]]; then
  # A completed timed session has already closed its receiver and checkpointed
  # its work. Release the image before starting the same guarded flow again.
  # FD 9 survives exec, so this continuation retains the original job lock.
  if cleanup; then
    trap - EXIT
    echo "Continuing the saved export automatically after its scheduled checkpoint."
    sleep 1
    exec /bin/bash "$wrapper_path" "$@"
  fi
  # A busy image keeps its receipt and waits for the ordinary scheduled retry.
  trap - EXIT
  exit 0
fi
exit "$transfer_result"
