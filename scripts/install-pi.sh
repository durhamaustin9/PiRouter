#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
operator_user="$(id -un)"
config_root="${XDG_CONFIG_HOME:-${HOME}/.config}"
systemd_user_dir="$config_root/systemd/user"
labwc_dir="$config_root/labwc"
service_file="$systemd_user_dir/pirouter-dashboard.service"
labwc_autostart="$labwc_dir/autostart"
samba_dropin_dir="/etc/systemd/system/smbd.service.d"

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer is intended for Raspberry Pi OS." >&2
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "Node.js 22.13 or newer is required. Install it, then run this command again." >&2
  exit 1
fi

if ! node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit((major === 22 && minor >= 13) || major >= 24 ? 0 : 1)'; then
  echo "Node.js 22.13 or newer is required; found $(node --version)." >&2
  exit 1
fi

missing_packages=()
if ! command -v chromium >/dev/null 2>&1 && ! command -v chromium-browser >/dev/null 2>&1; then
  missing_packages+=(chromium)
fi
if ! command -v swayidle >/dev/null 2>&1; then
  missing_packages+=(swayidle)
fi
if ! command -v wlopm >/dev/null 2>&1; then
  missing_packages+=(wlopm)
fi

if (( ${#missing_packages[@]} > 0 )); then
  echo "Installing touchscreen kiosk dependencies…"
  sudo apt-get update
  sudo apt-get install -y "${missing_packages[@]}"
fi

cd "$project_dir"
if [[ ! -f .env ]]; then
  cp .env.example .env
  echo "Created .env from the included example. Configure ENDPOINT_HOST and TIME_MACHINE_PATH after setup."
fi

if ! grep -Eq '^PORTABLE_EXPORT_TOKEN="?[[:xdigit:]]{64}"?$' .env; then
  portable_export_token="$(openssl rand -hex 32)"
  env_temp="$(mktemp)"
  awk -v token="$portable_export_token" '
    BEGIN { replaced = 0 }
    /^PORTABLE_EXPORT_TOKEN=/ {
      print "PORTABLE_EXPORT_TOKEN=\"" token "\""
      replaced = 1
      next
    }
    { print }
    END {
      if (!replaced) print "PORTABLE_EXPORT_TOKEN=\"" token "\""
    }
  ' .env > "$env_temp"
  install -m 0600 "$env_temp" .env
  rm -f -- "$env_temp"
  unset portable_export_token
  echo "Generated a private macOS portable-export pairing token in .env."
fi

mkdir -p data "$systemd_user_dir" "$labwc_dir"
npm ci --no-audit --no-fund
npm run build

{
  echo "[Unit]"
  echo "Description=PiRouter touchscreen dashboard"
  echo "After=network-online.target tailscaled.service"
  echo "Wants=network-online.target"
  echo
  echo "[Service]"
  echo "Type=simple"
  printf 'WorkingDirectory=%s\n' "$project_dir"
  printf 'EnvironmentFile=-%s/.env\n' "$project_dir"
  echo "Environment=NODE_ENV=production"
  echo "ExecStart=/usr/bin/env npm start"
  echo "Restart=on-failure"
  echo "RestartSec=3"
  echo
  echo "[Install]"
  echo "WantedBy=default.target"
} > "$service_file"

touch "$labwc_autostart"
autostart_temp="$(mktemp)"
awk '
  /^# BEGIN PIROUTER$/ { skip = 1; next }
  /^# END PIROUTER$/ { skip = 0; next }
  !skip { print }
' "$labwc_autostart" > "$autostart_temp"
{
  cat "$autostart_temp"
  echo "# BEGIN PIROUTER"
  printf '%s/scripts/start-kiosk.sh &\n' "$project_dir"
  echo "# END PIROUTER"
} > "$labwc_autostart"
rm "$autostart_temp"

chmod +x \
  "$project_dir/scripts/display-idle.sh" \
  "$project_dir/scripts/start-kiosk.sh"

sudo install -d -m 0755 "$samba_dropin_dir"
sudo install -m 0644 \
  "$project_dir/systemd/smbd-pirouter-reliability.conf" \
  "$samba_dropin_dir/pirouter-reliability.conf"

sudoers_temp="$(mktemp)"
printf '%s ALL=(root) NOPASSWD: /usr/bin/smbstatus --locks --json\n' \
  "$operator_user" > "$sudoers_temp"
sudo visudo -cf "$sudoers_temp"
sudo install -m 0440 "$sudoers_temp" /etc/sudoers.d/pirouter-dashboard
rm -f -- "$sudoers_temp"
sudo systemctl daemon-reload

systemctl --user daemon-reload
systemctl --user enable --now pirouter-dashboard.service

echo
echo "PiRouter is installed and running at http://127.0.0.1:3000"
echo "Edit $project_dir/.env to add an optional service endpoint and Time Machine share, then run:"
echo "  systemctl --user restart pirouter-dashboard"
echo "The touchscreen kiosk will open automatically through labwc at your next desktop login."
echo "To create browsable Time Machine copies on another server, pair the Mac with:"
echo "  npm run install:mac-export"
