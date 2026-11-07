#!/bin/bash
set -euo pipefail

if [[ "$(uname -s)" != Darwin ]]; then
  echo "This installer must run on macOS." >&2
  exit 1
fi

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
support_dir="${PIROUTER_SUPPORT_DIR:-$HOME/Library/Application Support/PiRouter}"
app_dir="${PIROUTER_APP_DIR:-/Applications/PiRouter Exporter.app}"
executable="$app_dir/Contents/MacOS/PiRouterExporter"
config_file="$support_dir/portable-export.env"
agent_dir="${PIROUTER_LAUNCH_AGENT_DIR:-$HOME/Library/LaunchAgents}"
launch_agent="$agent_dir/com.pirouter.portable-export.plist"
skip_service="${PIROUTER_SKIP_SERVICE:-false}"
no_open="${PIROUTER_NO_OPEN:-false}"
[[ "$skip_service" != 1 ]] || skip_service=true
[[ "$no_open" != 1 ]] || no_open=true
uid_value="$(id -u)"
label="gui/$uid_value/com.pirouter.portable-export"
prebuilt_app="${PIROUTER_PREBUILT_APP:-}"

for target in "$support_dir" "$app_dir" "$agent_dir"; do
  [[ "$target" == /* ]] || { echo "Installation paths must be absolute." >&2; exit 1; }
done
mkdir -p "$support_dir" "$(dirname "$app_dir")" "$agent_dir"
stage_root="$(mktemp -d "$(dirname "$app_dir")/.pirouter-install.XXXXXX")"
build_work="$(mktemp -d "${TMPDIR:-/tmp}/pirouter-app-build.XXXXXX")"
app_stage="$stage_root/PiRouter Exporter.app"
config_temp="$stage_root/config-new"
agent_temp="$stage_root/agent-new.plist"
replaced=false
committed=false
had_config=false
had_agent=false
had_app=false
service_was_loaded=false
service_stopped=false
[[ ! -f "$config_file" ]] || { had_config=true; cp -p "$config_file" "$stage_root/config-before"; }
[[ ! -f "$launch_agent" ]] || { had_agent=true; cp -p "$launch_agent" "$stage_root/agent-before.plist"; }
if [[ "$skip_service" != true ]] && launchctl print "$label" >/dev/null 2>&1; then service_was_loaded=true; fi

cleanup() {
  result=$?
  trap - EXIT
  if [[ "$result" -ne 0 && "$replaced" == true && "$committed" != true ]]; then
    [[ "$skip_service" == true ]] || launchctl bootout "gui/$uid_value" "$launch_agent" >/dev/null 2>&1 || true
    [[ ! -e "$app_dir" ]] || mv "$app_dir" "$stage_root/failed.app"
    [[ "$had_app" != true ]] || mv "$stage_root/previous.app" "$app_dir"
    if [[ "$had_config" == true ]]; then cp -p "$stage_root/config-before" "$config_file"; else rm -f "$config_file"; fi
    if [[ "$had_agent" == true ]]; then cp -p "$stage_root/agent-before.plist" "$launch_agent"; else rm -f "$launch_agent"; fi
    echo "Installation did not finish; the previous app and settings were restored." >&2
  fi
  if [[ "$result" -ne 0 && "$committed" != true && "$service_stopped" == true && "$service_was_loaded" == true ]]; then
    launchctl enable "$label" >/dev/null 2>&1 || true
    launchctl bootstrap "gui/$uid_value" "$launch_agent" >/dev/null 2>&1 || true
  fi
  rm -rf -- "$stage_root" "$build_work"
  exit "$result"
}
trap cleanup EXIT
chmod 0700 "$stage_root" "$build_work"

requested_pirouter_url="${PIROUTER_URL:-}"
requested_local_url="${PIROUTER_LOCAL_URL:-}"
requested_local_urls="${PIROUTER_LOCAL_URLS:-}"
requested_pirouter_token="${PIROUTER_TOKEN:-}"
requested_export_host="${EXPORT_HOST:-}"
requested_export_root="${EXPORT_ROOT:-}"
requested_export_ssh_key="${EXPORT_SSH_KEY:-}"
requested_export_ssh_proxy_command="${EXPORT_SSH_PROXY_COMMAND:-}"
requested_export_ssh_proxy_is_set="${EXPORT_SSH_PROXY_COMMAND+x}"
requested_continuous_export="${CONTINUOUS_EXPORT:-}"
requested_export_enabled="${EXPORT_ENABLED:-}"
requested_automatic_export="${AUTOMATIC_EXPORT:-}"
requested_automatic_hours="${AUTOMATIC_EXPORT_MIN_HOURS:-}"
requested_bandwidth="${RSYNC_BANDWIDTH_LIMIT_KB:-}"

if [[ -r "$config_file" ]]; then
  # Preserve the existing pairing and destination on upgrades.
  # shellcheck disable=SC1090
  source "$config_file"
fi

pirouter_url="${requested_pirouter_url:-${PIROUTER_URL:-}}"
pirouter_local_url="${requested_local_url:-${PIROUTER_LOCAL_URL:-}}"
pirouter_local_urls="${requested_local_urls:-${PIROUTER_LOCAL_URLS:-}}"
pirouter_token="${requested_pirouter_token:-${PIROUTER_TOKEN:-}}"
export_host="${requested_export_host:-${EXPORT_HOST:-}}"
export_root="${requested_export_root:-${EXPORT_ROOT:-/mnt/user/time-machine-austin}}"
export_ssh_key="${requested_export_ssh_key:-${EXPORT_SSH_KEY:-$HOME/.ssh/id_ed25519}}"
continuous_export="${requested_continuous_export:-${CONTINUOUS_EXPORT:-true}}"
automatic_export="${requested_automatic_export:-${AUTOMATIC_EXPORT:-true}}"
automatic_hours="${requested_automatic_hours:-${AUTOMATIC_EXPORT_MIN_HOURS:-24}}"
bandwidth_limit="${requested_bandwidth:-${RSYNC_BANDWIDTH_LIMIT_KB:-40000}}"
export_enabled="${requested_export_enabled:-${EXPORT_ENABLED:-}}"
if [[ -z "$export_enabled" ]]; then
  if [[ -n "$pirouter_token" && -n "$export_host" && -r "$export_ssh_key" ]]; then export_enabled=true; else export_enabled=false; fi
fi
if [[ "$continuous_export" != true && "$continuous_export" != false ]]; then
  echo "CONTINUOUS_EXPORT must be true or false." >&2
  exit 1
fi
if [[ "$requested_export_ssh_proxy_is_set" == x ]]; then
  export_ssh_proxy_command="$requested_export_ssh_proxy_command"
else
  export_ssh_proxy_command="${EXPORT_SSH_PROXY_COMMAND:-}"
fi

if [[ -z "$pirouter_url" ]]; then
  echo "The app's first-run setup will ask for the PiRouter address."
elif [[ ! "$pirouter_url" =~ ^https?://[^[:space:]]+$ ]]; then
  echo "The PiRouter address must be an HTTP or HTTPS URL." >&2
  exit 1
fi
if [[ ! "$export_root" =~ ^/mnt/user/[A-Za-z0-9._-]+$ ]]; then echo "The export destination must be one share under /mnt/user." >&2; exit 1; fi
for value in "$export_enabled" "$automatic_export"; do
  [[ "$value" == true || "$value" == false ]] || { echo "Export options must be true or false." >&2; exit 1; }
done
for value in "$automatic_hours" "$bandwidth_limit"; do
  [[ "$value" =~ ^[1-9][0-9]*$ ]] || { echo "The export interval and bandwidth limit must be positive integers." >&2; exit 1; }
done
if [[ "$export_enabled" == true && ( -z "$pirouter_url" || -z "$pirouter_token" || -z "$export_host" || ! -r "$export_ssh_key" ) ]]; then
  echo "Enabled exports require a PiRouter address, pairing token, SSH destination, and readable SSH key." >&2
  exit 1
fi

if [[ -n "$prebuilt_app" ]]; then
  [[ -d "$prebuilt_app" ]] || { echo "The prebuilt app is missing." >&2; exit 1; }
  ditto "$prebuilt_app" "$app_stage"
else
  PIROUTER_BUILD_DIR="$build_work" \
  PIROUTER_APP_OUTPUT="$build_work/PiRouter Exporter.app" \
  PIROUTER_BUILD_DMG=false \
    bash "$project_dir/scripts/build-macos-app.sh"
  ditto "$build_work/PiRouter Exporter.app" "$app_stage"
fi
codesign --verify --deep --strict "$app_stage"
[[ "$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "$app_stage/Contents/Info.plist")" == local.pirouter.portable-export ]] || {
  echo "The staged app has an unexpected identity." >&2; exit 1;
}
python_home="$app_stage/Contents/Frameworks/Python.framework/Versions/Current"
python_command="$python_home/bin/python3"
[[ -x "$python_command" ]] || { echo "The staged app is missing its private Python runtime." >&2; exit 1; }
export PYTHONHOME="$python_home"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1

if [[ -r "$config_file" ]]; then
  awk '
    /^# BEGIN PIROUTER INSTALLER SETTINGS$/ { skip=1; next }
    /^# END PIROUTER INSTALLER SETTINGS$/ { skip=0; next }
    !skip { print }
  ' "$config_file" > "$config_temp"
else
  : > "$config_temp"
fi
# This file is sourced by Bash. Quote values as shell literals so a proxy
# command retains spaces and quotes without running while loading the config.
{
  printf '\n# BEGIN PIROUTER INSTALLER SETTINGS\n'
  printf 'PIROUTER_URL=%q\n' "$pirouter_url"
  printf 'PIROUTER_LOCAL_URL=%q\n' "$pirouter_local_url"
  printf 'PIROUTER_LOCAL_URLS=%q\n' "$pirouter_local_urls"
  printf 'PIROUTER_TOKEN=%q\n' "$pirouter_token"
  printf 'EXPORT_ENABLED=%s\n' "$export_enabled"
  printf 'EXPORT_HOST=%q\n' "$export_host"
  printf 'EXPORT_ROOT=%q\n' "$export_root"
  printf 'EXPORT_SSH_KEY=%q\n' "$export_ssh_key"
  printf 'EXPORT_SSH_PROXY_COMMAND=%q\n' "$export_ssh_proxy_command"
  printf 'CONTINUOUS_EXPORT=%s\n' "$continuous_export"
  printf 'AUTOMATIC_EXPORT=%s\n' "$automatic_export"
  printf 'AUTOMATIC_EXPORT_MIN_HOURS=%s\n' "$automatic_hours"
  printf 'RSYNC_BANDWIDTH_LIMIT_KB=%s\n' "$bandwidth_limit"
  printf '# END PIROUTER INSTALLER SETTINGS\n'
} >> "$config_temp"
chmod 0600 "$config_temp"

"$python_command" - "$agent_temp" "$executable" "$support_dir" <<'PY'
from pathlib import Path
import plistlib, sys
metadata = {
 'Label':'com.pirouter.portable-export', 'ProgramArguments':[sys.argv[2],'--background'],
 'RunAtLoad':True, 'StartInterval':300, 'ProcessType':'Background', 'LowPriorityIO':True,
 'Umask':0o077,
 'EnvironmentVariables':{'PIROUTER_SUPPORT_DIR':sys.argv[3], 'PIROUTER_PORTABLE_EXPORT_CONFIG':str(Path(sys.argv[3])/'portable-export.env')},
 'StandardOutPath':str(Path(sys.argv[3])/'export.log'),
 'StandardErrorPath':str(Path(sys.argv[3])/'export-error.log'),
}
Path(sys.argv[1]).write_bytes(plistlib.dumps(metadata))
PY
plutil -lint "$agent_temp" >/dev/null
check_settings_unchanged() {
  if { [[ "$had_config" == true ]] && ! cmp -s "$stage_root/config-before" "$config_file"; } ||
     { [[ "$had_config" != true ]] && [[ -e "$config_file" ]]; }; then
    echo "Settings changed while the app was being built. Re-run installation to preserve those changes." >&2
    exit 1
  fi
}
check_settings_unchanged

if [[ "$skip_service" != true ]]; then
  if launchctl print "$label" >/dev/null 2>&1; then service_was_loaded=true; else service_was_loaded=false; fi
  launchctl bootout "gui/$uid_value" "$launch_agent" >/dev/null 2>&1 || true
  service_stopped=true
  if launchctl print "$label" >/dev/null 2>&1; then
    echo "The background exporter could not be stopped. The existing app was not replaced." >&2
    exit 1
  fi
  "$python_command" - "$executable" <<'PY'
import os, signal, subprocess, sys, time
executable=sys.argv[1]
processes=[]
for line in subprocess.check_output(['/bin/ps','-axo','pid=,command='],text=True).splitlines():
 fields=line.strip().split(None,1)
 if len(fields)!=2: continue
 pid,command=int(fields[0]),fields[1]
 if command==executable or command.startswith(executable+' '):
  processes.append(pid)
  try: os.kill(pid,signal.SIGTERM)
  except ProcessLookupError: pass
for _ in range(300):
 active=[]
 for pid in processes:
  try: os.kill(pid,0)
  except ProcessLookupError: continue
  state=subprocess.run(['/bin/ps','-p',str(pid),'-o','stat='],capture_output=True,text=True).stdout.strip()
  if state and not state.startswith('Z'): active.append(pid)
 if not active: break
 time.sleep(0.1)
else:
 raise SystemExit('The exporter is still finishing a safe checkpoint. The existing app was not replaced.')
PY
fi

check_settings_unchanged
if [[ -e "$app_dir" ]]; then had_app=true; mv "$app_dir" "$stage_root/previous.app"; fi
replaced=true
mv "$app_stage" "$app_dir"
install -m 0600 "$config_temp" "$config_file"
install -m 0644 "$agent_temp" "$launch_agent"
if [[ "$skip_service" != true && "$export_enabled" == true ]]; then
  launchctl enable "$label"
  launchctl bootstrap "gui/$uid_value" "$launch_agent"
elif [[ "$skip_service" != true ]]; then
  launchctl disable "$label"
fi
codesign --verify --deep --strict "$app_dir"
committed=true

backup_dir="$support_dir/updates/install-$(date +%Y%m%d-%H%M%S)-$$"
mkdir -m 0700 -p "$backup_dir"
[[ "$had_config" != true ]] || cp -p "$stage_root/config-before" "$backup_dir/portable-export.env"
[[ "$had_agent" != true ]] || cp -p "$stage_root/agent-before.plist" "$backup_dir/com.pirouter.portable-export.plist"
[[ "$had_app" != true ]] || mv "$stage_root/previous.app" "$backup_dir/PiRouter Exporter.app"
echo "PiRouter Exporter is installed at $app_dir"
echo "Existing pairing, upload progress, and export policies are preserved."
if [[ "$no_open" != true ]]; then
  unset PYTHONHOME
  open "$app_dir"
fi
