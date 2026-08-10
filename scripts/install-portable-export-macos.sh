#!/bin/bash
set -euo pipefail

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This installer must run on the Mac that owns the Time Machine backup." >&2
  exit 1
fi

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
support_dir="$HOME/Library/Application Support/PiRouter"
app_dir="${PIROUTER_APP_DIR:-/Applications/PiRouter Exporter.app}"
executable="$app_dir/Contents/MacOS/PiRouterExporter"
resources_dir="$app_dir/Contents/Resources"
export_script="$resources_dir/portable-export-macos.sh"
verify_script="$resources_dir/verify-time-machine-volumes.py"
icon_file="$resources_dir/PiRouterExporter.icns"
info_plist="$app_dir/Contents/Info.plist"
config_file="$support_dir/portable-export.env"
launch_agent="$HOME/Library/LaunchAgents/com.pirouter.portable-export.plist"
uid="$(id -u)"

requested_pirouter_url="${PIROUTER_URL:-}"
requested_local_url="${PIROUTER_LOCAL_URL:-}"
requested_pirouter_token="${PIROUTER_TOKEN:-}"
requested_export_host="${EXPORT_HOST:-}"
requested_export_root="${EXPORT_ROOT:-}"
requested_export_ssh_key="${EXPORT_SSH_KEY:-}"
requested_rsync_bin="${RSYNC_BIN:-}"

if [[ -r "$config_file" ]]; then
  # Preserve the existing pairing and destination on upgrades.
  # shellcheck disable=SC1090
  source "$config_file"
fi

pirouter_url="${requested_pirouter_url:-${PIROUTER_URL:-}}"
pirouter_local_url="${requested_local_url:-${PIROUTER_LOCAL_URL:-}}"
pirouter_token="${requested_pirouter_token:-${PIROUTER_TOKEN:-}}"
export_host="${requested_export_host:-${EXPORT_HOST:-}}"
export_root="${requested_export_root:-${EXPORT_ROOT:-/mnt/user/time-machine}}"
export_ssh_key="${requested_export_ssh_key:-${EXPORT_SSH_KEY:-$HOME/.ssh/id_ed25519}}"
rsync_bin="${requested_rsync_bin:-${RSYNC_BIN:-}}"

if [[ -z "$pirouter_url" ]]; then
  read -r -p "PiRouter URL (for example http://pirouter.local:3000): " pirouter_url
fi
if [[ -z "$pirouter_token" ]]; then
  read -r -s -p "Portable-export pairing token: " pirouter_token
  echo
fi
if [[ -z "$export_host" ]]; then
  read -r -p "Unraid SSH target (for example backup@server): " export_host
fi

if [[ -z "$pirouter_local_url" ]]; then
  remote_authority="${pirouter_url#*://}"
  remote_host="${remote_authority%%/*}"
  remote_host="${remote_host%%:*}"
  remote_short_name="${remote_host%%.*}"
  if [[ "$remote_short_name" =~ ^[A-Za-z][A-Za-z0-9-]*$ ]]; then
    pirouter_local_url="http://$remote_short_name.local:3000"
  fi
  unset remote_authority remote_host remote_short_name
fi

if [[ -z "$pirouter_url" || -z "$pirouter_token" || -z "$export_host" ]]; then
  echo "PiRouter URL, pairing token, and Unraid SSH target are required." >&2
  exit 1
fi
if [[ ! -r "$export_ssh_key" ]]; then
  echo "SSH key not found: $export_ssh_key" >&2
  exit 1
fi

if [[ -z "$rsync_bin" ]]; then
  if ! command -v brew >/dev/null 2>&1; then
    echo "Homebrew is required to install rsync 3: https://brew.sh" >&2
    exit 1
  fi
  rsync_bin="$(brew --prefix)/bin/rsync"
fi
rsync_version="$("$rsync_bin" --version 2>/dev/null || true)"
if [[ ! -x "$rsync_bin" ]] || [[ "$rsync_version" != *"version 3."* ]]; then
  echo "Installing rsync 3 with Homebrew…"
  brew install rsync
fi
unset rsync_version

# Avoid replacing the executable beneath an already-running WebKit process.
/usr/bin/osascript -e \
  'tell application id "local.pirouter.portable-export" to quit' \
  >/dev/null 2>&1 || true
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
  if ! pgrep -f "^$executable$" >/dev/null 2>&1; then
    break
  fi
  sleep 0.1
done
running_app_pid="$(pgrep -f "^$executable$" | head -n 1 || true)"
if [[ "$running_app_pid" =~ ^[0-9]+$ ]]; then
  kill -TERM "$running_app_pid"
  sleep 1
fi
unset attempt running_app_pid

mkdir -p \
  "$support_dir" \
  "$app_dir/Contents/MacOS" \
  "$resources_dir" \
  "$HOME/Library/LaunchAgents"
install -m 0755 "$project_dir/scripts/portable-export-macos.sh" "$export_script"
install -m 0644 "$project_dir/scripts/verify-time-machine-volumes.py" "$verify_script"
install -m 0644 "$project_dir/assets/PiRouterExporter.icns" "$icon_file"
xcrun swiftc \
  -O \
  -framework AppKit \
  -framework WebKit \
  "$project_dir/scripts/portable-export-launcher.swift" \
  -o "$executable"

config_temp="$(mktemp)"
plist_temp="$(mktemp)"
agent_temp="$(mktemp)"
trap 'rm -f -- "$config_temp" "$plist_temp" "$agent_temp"' EXIT
chmod 0600 "$config_temp"

printf '%s\n' \
  "PIROUTER_URL=$pirouter_url" \
  "PIROUTER_LOCAL_URL=$pirouter_local_url" \
  "PIROUTER_TOKEN=$pirouter_token" \
  "EXPORT_HOST=$export_host" \
  "EXPORT_ROOT=$export_root" \
  "EXPORT_SSH_KEY=$export_ssh_key" \
  "RSYNC_BIN=$rsync_bin" \
  'AUTOMATIC_EXPORT=true' \
  'AUTOMATIC_EXPORT_MIN_HOURS=24' \
  'RSYNC_BANDWIDTH_LIMIT_KB=40000' > "$config_temp"
install -m 0600 "$config_temp" "$config_file"

cat > "$plist_temp" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDisplayName</key>
  <string>PiRouter Exporter</string>
  <key>CFBundleExecutable</key>
  <string>PiRouterExporter</string>
  <key>CFBundleIdentifier</key>
  <string>local.pirouter.portable-export</string>
  <key>CFBundleIconFile</key>
  <string>PiRouterExporter</string>
  <key>CFBundleInfoDictionaryVersion</key>
  <string>6.0</string>
  <key>CFBundleName</key>
  <string>PiRouter Exporter</string>
  <key>CFBundlePackageType</key>
  <string>APPL</string>
  <key>CFBundleShortVersionString</key>
  <string>2.0</string>
  <key>CFBundleVersion</key>
  <string>2</string>
  <key>LSApplicationCategoryType</key>
  <string>public.app-category.utilities</string>
  <key>LSMinimumSystemVersion</key>
  <string>13.0</string>
  <key>NSHighResolutionCapable</key>
  <true/>
  <key>NSAppTransportSecurity</key>
  <dict>
    <key>NSAllowsLocalNetworking</key>
    <true/>
    <key>NSAllowsArbitraryLoadsInWebContent</key>
    <true/>
  </dict>
</dict>
</plist>
PLIST
install -m 0644 "$plist_temp" "$info_plist"
/usr/bin/codesign \
  --force \
  --deep \
  --sign - \
  --identifier local.pirouter.portable-export \
  --requirements '=designated => identifier "local.pirouter.portable-export"' \
  "$app_dir" >/dev/null

cat > "$agent_temp" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.pirouter.portable-export</string>
  <key>ProgramArguments</key>
  <array>
    <string>$executable</string>
    <string>--background</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>StartInterval</key>
  <integer>300</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>LowPriorityIO</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$support_dir/export.log</string>
  <key>StandardErrorPath</key>
  <string>$support_dir/export-error.log</string>
</dict>
</plist>
PLIST
plutil -lint "$agent_temp" >/dev/null
install -m 0644 "$agent_temp" "$launch_agent"

launchctl bootout "gui/$uid" "$launch_agent" >/dev/null 2>&1 || true
launchctl enable "gui/$uid/com.pirouter.portable-export"
launchctl bootstrap "gui/$uid" "$launch_agent"

echo
echo "PiRouter Exporter is installed in /Applications with live local/Tailscale dashboard access."
echo "The background exporter checks every five minutes, even while the app is closed."
echo "Grant Full Disk Access to this app before the first export:"
echo "  $app_dir"
# Give the RunAtLoad background check its own launch before opening the UI.
sleep 2
open -n "$app_dir"
