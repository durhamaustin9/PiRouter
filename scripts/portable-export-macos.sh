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

remote_pirouter_url="$PIROUTER_URL"
local_pirouter_urls="${PIROUTER_LOCAL_URLS:-${PIROUTER_LOCAL_URL:-}}"
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
  cd / 2>/dev/null || true
  if [[ "$remote_lock_owned" == true ]]; then
    /usr/bin/ssh "${ssh_args[@]}" "$EXPORT_HOST" \
      "rmdir '$EXPORT_ROOT/.portable-export.lock'" >/dev/null 2>&1 || true
  fi
  [[ -z "$response_file" ]] || rm -f -- "$response_file"
  [[ -z "$tmutil_output_file" ]] || rm -f -- "$tmutil_output_file"
  rmdir "$lock_dir" 2>/dev/null || true
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
  local body
  if [[ -n "$snapshot_name" ]]; then
    body="$(printf '{"requestId":"%s","state":"%s","message":"%s","lastSnapshotName":"%s","bytesCopied":%s}' \
      "$request_id" "$state" "$message" "$snapshot_name" "$bytes_copied")"
  else
    body="$(printf '{"requestId":"%s","state":"%s","message":"%s"}' \
      "$request_id" "$state" "$message")"
  fi
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

report_rsync_progress() {
  local line=""
  local progress=""
  local last_progress=-1
  local last_report_epoch=0
  local now_epoch=0

  # rsync rewrites progress in place with carriage returns. Reading those
  # records directly avoids stdio buffering in an intermediate text filter.
  while IFS= read -r -d $'\r' line || [[ -n "$line" ]]; do
    if [[ "$line" =~ ([0-9]{1,3})% ]]; then
      progress="${BASH_REMATCH[1]}"
      now_epoch="$(date +%s)"
      if (( progress <= 100 )) &&
        (( progress != last_progress )) &&
        (( progress == 100 || now_epoch - last_report_epoch >= 15 )); then
        api_update "$request_id" running \
          "Copying snapshot $snapshot_name to Unraid · $progress% complete" \
          >/dev/null 2>&1 || true
        printf 'Portable export progress: %s%%\n' "$progress"
        last_progress="$progress"
        last_report_epoch="$now_epoch"
      fi
    elif [[ -n "$line" ]]; then
      printf '%s\n' "$line"
    fi
  done
}

latest_snapshot() {
  if /usr/bin/tmutil status | /usr/bin/grep -q 'Running = 1'; then
    return 75
  fi

  tmutil_output_file="$(mktemp -t pirouter-tmutil.XXXXXX)"
  exec 3< <(/usr/bin/tmutil latestbackup -m 2>"$tmutil_output_file")
  tmutil_pid=$!
  local candidate=""
  local read_result=0
  IFS= read -r -t 120 candidate <&3 || read_result=$?
  if [[ "$read_result" -ne 0 ]]; then
    kill -TERM "$tmutil_pid" 2>/dev/null || true
    exec 3<&-
    tmutil_pid=""
    if [[ "$read_result" -gt 128 ]]; then
      return 124
    fi
    return 77
  fi
  if [[ -z "$candidate" ]] || ! cd "$candidate" 2>/dev/null; then
    exec 3<&-
    tmutil_pid=""
    return 78
  fi
  snapshot_path="$candidate"
  printf '%s\n' "$snapshot_path" > "$tmutil_output_file"
  exec 3<&-
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

api_update "$request_id" running \
  "Preparing snapshot $snapshot_name and calculating transfer progress" \
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
  --partial
  --protect-args
  --exclude=/.com.apple.timemachine.checkpoint
  --exclude=/.special_paths.plist
  "--exclude=/Macintosh HD - Data/private/var/"
  "--exclude=/Macintosh HD - Data/private/etc/cups/certs/"
  "--exclude=/Macintosh HD - Data/Library/Application Support/Apple/ParentalControls/Users/"
  "--exclude=/Macintosh HD - Data/Library/Preferences/com.express.vpn/api_client/"
  "--exclude=/Macintosh HD - Data/Library/Preferences/com.express.vpn/csdk/"
  --no-inc-recursive
  --info=progress2
  --outbuf=N
  --human-readable
  --stats
  --bwlimit="$bandwidth_limit_kb"
  --rsync-path=/usr/bin/rsync
  -e "$rsync_shell"
)
if [[ -n "$previous_snapshot" && "$previous_snapshot" != "$snapshot_name" ]]; then
  rsync_args+=(--link-dest="$EXPORT_ROOT/snapshots/$previous_snapshot")
fi

/usr/bin/nice -n 10 "$rsync_bin" "${rsync_args[@]}" \
  "$snapshot_path/" "$EXPORT_HOST:$partial_path/" | report_rsync_progress
rsync_result="${PIPESTATUS[0]}"
if [[ "$rsync_result" -ne 0 ]]; then
  api_update "$request_id" error \
    "The portable copy stopped early and will resume on the next request" \
    >/dev/null 2>&1 || true
  exit 1
fi

snapshot_bytes="$(/usr/bin/ssh "${ssh_args[@]}" "$EXPORT_HOST" \
  "du -sb '$partial_path' | awk '{print \$1}'" 2>/dev/null || true)"
[[ "$snapshot_bytes" =~ ^[0-9]+$ ]] || snapshot_bytes=0

if ! /usr/bin/ssh "${ssh_args[@]}" "$EXPORT_HOST" \
  "touch '$partial_path/.pirouter-complete' && mv -nT '$partial_path' '$final_path' && test ! -e '$partial_path' && printf '%s\\n' '$snapshot_name' > '$EXPORT_ROOT/LATEST.txt'"; then
  api_update "$request_id" error \
    "The copied files are safe, but a completed Unraid folder already exists" \
    >/dev/null 2>&1 || true
  exit 1
fi

api_update "$request_id" success \
  "Browsable snapshot $snapshot_name is ready on Unraid" \
  "$snapshot_name" "$snapshot_bytes" >/dev/null
printf '%s\n' "$(date +%s)" > "$automatic_marker"
