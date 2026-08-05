#!/bin/bash
set -uo pipefail

config_file="${PIROUTER_PORTABLE_EXPORT_CONFIG:-${HOME}/Library/Application Support/PiRouter/portable-export.env}"
if [[ ! -r "$config_file" ]]; then
  echo "Portable-export configuration is missing: $config_file" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$config_file"

: "${PIROUTER_URL:?PIROUTER_URL is required}"
: "${PIROUTER_TOKEN:?PIROUTER_TOKEN is required}"
: "${EXPORT_HOST:?EXPORT_HOST is required}"
: "${EXPORT_ROOT:?EXPORT_ROOT is required}"
: "${EXPORT_SSH_KEY:?EXPORT_SSH_KEY is required}"

automatic="${AUTOMATIC_EXPORT:-true}"
automatic_hours="${AUTOMATIC_EXPORT_MIN_HOURS:-24}"
bandwidth_limit_kb="${RSYNC_BANDWIDTH_LIMIT_KB:-40000}"
rsync_bin="${RSYNC_BIN:-/opt/homebrew/bin/rsync}"
support_dir="$(dirname "$config_file")"
lock_dir="$support_dir/run.lock"
automatic_marker="$support_dir/last-automatic-check"
response_file=""
tmutil_output_file=""
tmutil_pid=""
remote_lock_owned=false

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "Portable Time Machine exports must run on macOS." >&2
  exit 1
fi
if [[ ! "$PIROUTER_URL" =~ ^https?://[^[:space:]]+$ ]]; then
  echo "PIROUTER_URL must be an HTTP or HTTPS URL." >&2
  exit 1
fi
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
rsync_version="$("$rsync_bin" --version 2>/dev/null || true)"
if [[ ! -x "$rsync_bin" ]] || [[ "$rsync_version" != *"version 3."* ]]; then
  echo "rsync 3 is required; install it with: brew install rsync" >&2
  exit 1
fi
unset rsync_version
if [[ ! "$automatic_hours" =~ ^[1-9][0-9]*$ ]]; then
  echo "AUTOMATIC_EXPORT_MIN_HOURS must be a positive integer." >&2
  exit 1
fi
if [[ ! "$bandwidth_limit_kb" =~ ^[1-9][0-9]*$ ]]; then
  echo "RSYNC_BANDWIDTH_LIMIT_KB must be a positive integer." >&2
  exit 1
fi

mkdir -p "$support_dir"
if ! mkdir "$lock_dir" 2>/dev/null; then
  exit 0
fi

ssh_args=(
  -i "$EXPORT_SSH_KEY"
  -o BatchMode=yes
  -o ConnectTimeout=10
  -o ServerAliveInterval=30
  -o ServerAliveCountMax=4
)

cleanup() {
  if [[ -n "$tmutil_pid" ]] && kill -0 "$tmutil_pid" 2>/dev/null; then
    kill -TERM "$tmutil_pid" 2>/dev/null || true
  fi
  if [[ "$remote_lock_owned" == true ]]; then
    /usr/bin/ssh "${ssh_args[@]}" "$EXPORT_HOST" \
      "rmdir '$EXPORT_ROOT/.portable-export.lock'" >/dev/null 2>&1 || true
  fi
  [[ -z "$response_file" ]] || rm -f -- "$response_file"
  [[ -z "$tmutil_output_file" ]] || rm -f -- "$tmutil_output_file"
  rmdir "$lock_dir" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

response_file="$(mktemp -t pirouter-export-response.XXXXXX)"

json_value() {
  /usr/bin/plutil -extract "$2" raw -o - "$1" 2>/dev/null || true
}

api_get() {
  /usr/bin/curl --fail --silent --show-error \
    --connect-timeout 5 --max-time 20 \
    -H "Authorization: Bearer $PIROUTER_TOKEN" \
    -H "Accept: application/json" \
    "$PIROUTER_URL/api/portable-export" -o "$response_file"
}

api_request_automatic() {
  /usr/bin/curl --fail --silent --show-error \
    --connect-timeout 5 --max-time 20 \
    -X POST \
    -H "Authorization: Bearer $PIROUTER_TOKEN" \
    -H "Accept: application/json" \
    "$PIROUTER_URL/api/portable-export" -o "$response_file"
}

api_update() {
  local request_id="$1"
  local state="$2"
  local message="$3"
  local snapshot_name="${4:-}"
  local bytes_copied="${5:-null}"
  local body
  if [[ -n "$snapshot_name" ]]; then
    body="$(printf '{"requestId":"%s","state":"%s","message":"%s","lastSnapshotName":"%s","bytesCopied":%s}' \
      "$request_id" "$state" "$message" "$snapshot_name" "$bytes_copied")"
  else
    body="$(printf '{"requestId":"%s","state":"%s","message":"%s"}' \
      "$request_id" "$state" "$message")"
  fi
  /usr/bin/curl --fail --silent --show-error \
    --connect-timeout 5 --max-time 20 \
    -X PATCH \
    -H "Authorization: Bearer $PIROUTER_TOKEN" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    --data-binary "$body" \
    "$PIROUTER_URL/api/portable-export" -o "$response_file"
}

latest_snapshot() {
  if /usr/bin/tmutil status | /usr/bin/grep -q 'Running = 1'; then
    return 75
  fi

  tmutil_output_file="$(mktemp -t pirouter-tmutil.XXXXXX)"
  /usr/bin/tmutil latestbackup -m >"$tmutil_output_file" 2>&1 &
  tmutil_pid=$!
  local elapsed=0
  while kill -0 "$tmutil_pid" 2>/dev/null; do
    if (( elapsed >= 120 )); then
      kill -TERM "$tmutil_pid" 2>/dev/null || true
      wait "$tmutil_pid" 2>/dev/null || true
      tmutil_pid=""
      return 124
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  if ! wait "$tmutil_pid"; then
    tmutil_pid=""
    return 77
  fi
  tmutil_pid=""

  snapshot_path="$(tail -n 1 "$tmutil_output_file")"
  snapshot_name="$(basename "$snapshot_path")"
  snapshot_name="${snapshot_name%.backup}"
  if [[ ! -d "$snapshot_path" ]] ||
    [[ ! "$snapshot_name" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{6}$ ]]; then
    return 78
  fi
  return 0
}

if ! api_get; then
  echo "Unable to reach the PiRouter portable-export API." >&2
  exit 1
fi

state="$(json_value "$response_file" state)"
request_id="$(json_value "$response_file" requestId)"
last_snapshot="$(json_value "$response_file" lastSnapshotName)"
manual_request=false

if [[ "$state" == "requested" || "$state" == "running" ]]; then
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

snapshot_path=""
snapshot_name=""
latest_snapshot
snapshot_result=$?
if [[ "$snapshot_result" -ne 0 ]]; then
  if [[ "$manual_request" == true && -n "$request_id" ]]; then
    if [[ "$snapshot_result" -eq 75 ]]; then
      echo "Time Machine is still backing up; the request will remain queued."
      exit 0
    fi
    api_update "$request_id" error \
      "The Mac could not mount the completed snapshot; check Full Disk Access" \
      >/dev/null 2>&1 || true
  fi
  echo "Unable to mount the latest completed Time Machine snapshot." >&2
  exit 1
fi

if [[ "$manual_request" != true ]]; then
  if [[ "$snapshot_name" == "$last_snapshot" ]]; then
    printf '%s\n' "$(date +%s)" > "$automatic_marker"
    exit 0
  fi
  if ! api_request_automatic; then
    echo "Unable to request the automatic export." >&2
    exit 1
  fi
  request_id="$(json_value "$response_file" status.requestId)"
fi

if [[ ! "$request_id" =~ ^[0-9a-fA-F-]{36}$ ]]; then
  echo "The Pi returned an invalid portable-export request ID." >&2
  exit 1
fi

if [[ "$snapshot_name" == "$last_snapshot" ]]; then
  api_update "$request_id" success \
    "Latest completed snapshot is already available on Unraid" \
    "$snapshot_name" 0 >/dev/null
  exit 0
fi

api_update "$request_id" running \
  "Copying snapshot $snapshot_name to a browsable Unraid folder" \
  >/dev/null

if ! /usr/bin/ssh "${ssh_args[@]}" "$EXPORT_HOST" \
  "mkdir -p '$EXPORT_ROOT/snapshots' && mkdir '$EXPORT_ROOT/.portable-export.lock'"; then
  api_update "$request_id" error \
    "Another export is using the Unraid destination" >/dev/null 2>&1 || true
  exit 1
fi
remote_lock_owned=true

final_path="$EXPORT_ROOT/snapshots/$snapshot_name"
partial_path="$EXPORT_ROOT/snapshots/.$snapshot_name.partial"
if /usr/bin/ssh "${ssh_args[@]}" "$EXPORT_HOST" \
  "test -f '$final_path/.pirouter-complete'"; then
  snapshot_bytes="$(/usr/bin/ssh "${ssh_args[@]}" "$EXPORT_HOST" \
    "du -sb '$final_path' | awk '{print \$1}'" 2>/dev/null || true)"
  [[ "$snapshot_bytes" =~ ^[0-9]+$ ]] || snapshot_bytes=0
  api_update "$request_id" success \
    "Browsable snapshot $snapshot_name is ready on Unraid" \
    "$snapshot_name" "$snapshot_bytes" >/dev/null
  exit 0
fi

previous_snapshot="$(/usr/bin/ssh "${ssh_args[@]}" "$EXPORT_HOST" \
  "find '$EXPORT_ROOT/snapshots' -mindepth 1 -maxdepth 1 -type d -name '20??-??-??-??????' -printf '%f\\n' | sort | tail -n 1" \
  2>/dev/null || true)"
if [[ -n "$previous_snapshot" ]] &&
  [[ ! "$previous_snapshot" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}-[0-9]{6}$ ]]; then
  previous_snapshot=""
fi

/usr/bin/ssh "${ssh_args[@]}" "$EXPORT_HOST" "mkdir -p '$partial_path'"
rsync_shell="/usr/bin/ssh -i $EXPORT_SSH_KEY -o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=4"
rsync_args=(
  -a
  --acls
  --xattrs
  --delete-delay
  --partial
  --protect-args
  --human-readable
  --stats
  --bwlimit="$bandwidth_limit_kb"
  --rsync-path=/usr/bin/rsync
  -e "$rsync_shell"
)
if [[ -n "$previous_snapshot" && "$previous_snapshot" != "$snapshot_name" ]]; then
  rsync_args+=(--link-dest="$EXPORT_ROOT/snapshots/$previous_snapshot")
fi

if ! /usr/bin/nice -n 10 "$rsync_bin" "${rsync_args[@]}" \
  "$snapshot_path/" "$EXPORT_HOST:$partial_path/"; then
  api_update "$request_id" error \
    "The portable copy stopped early and will resume on the next request" \
    >/dev/null 2>&1 || true
  exit 1
fi

snapshot_bytes="$(/usr/bin/ssh "${ssh_args[@]}" "$EXPORT_HOST" \
  "du -sb '$partial_path' | awk '{print \$1}'" 2>/dev/null || true)"
[[ "$snapshot_bytes" =~ ^[0-9]+$ ]] || snapshot_bytes=0

if ! /usr/bin/ssh "${ssh_args[@]}" "$EXPORT_HOST" \
  "touch '$partial_path/.pirouter-complete' && mv '$partial_path' '$final_path' && printf '%s\\n' '$snapshot_name' > '$EXPORT_ROOT/LATEST.txt'"; then
  api_update "$request_id" error \
    "The copied files are safe, but the final Unraid folder could not be published" \
    >/dev/null 2>&1 || true
  exit 1
fi

api_update "$request_id" success \
  "Browsable snapshot $snapshot_name is ready on Unraid" \
  "$snapshot_name" "$snapshot_bytes" >/dev/null
printf '%s\n' "$(date +%s)" > "$automatic_marker"
