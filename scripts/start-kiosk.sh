#!/usr/bin/env bash
set -euo pipefail

dashboard_url="${PIROUTER_URL:-http://127.0.0.1:3000}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v chromium >/dev/null 2>&1; then
  chromium_command="$(command -v chromium)"
elif command -v chromium-browser >/dev/null 2>&1; then
  chromium_command="$(command -v chromium-browser)"
else
  echo "PiRouter kiosk: Chromium is not installed." >&2
  exit 1
fi

for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error "$dashboard_url" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

# Power down only the touchscreen after inactivity. The Pi and dashboard keep
# running, and the Wayland idle manager restores the display on touch input.
if ! pgrep -u "$(id -u)" -x swayidle >/dev/null 2>&1; then
  "$script_dir/display-idle.sh" &
fi

exec "$chromium_command" \
  --kiosk \
  --app="$dashboard_url" \
  --noerrdialogs \
  --disable-infobars \
  --disable-session-crashed-bubble \
  --disable-features=Translate \
  --touch-events=enabled \
  --disable-pinch \
  --overscroll-history-navigation=0 \
  --check-for-update-interval=31536000
