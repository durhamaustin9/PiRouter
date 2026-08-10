#!/bin/bash
set -uo pipefail

verifier_pid="${1:?verifier PID is required}"
backup_disk="${2:-/dev/disk4}"
uid_value="$(id -u)"
agent_plist="$HOME/Library/LaunchAgents/com.pirouter.portable-export.plist"

if [[ ! "$verifier_pid" =~ ^[1-9][0-9]*$ ]]; then
  exit 64
fi
if [[ ! "$backup_disk" =~ ^/dev/disk[0-9]+$ ]]; then
  exit 64
fi

while kill -0 "$verifier_pid" 2>/dev/null; do
  sleep 30
done

# Let Time Machine release the mounted snapshot before detaching its image.
sleep 10
for _ in 1 2 3 4 5; do
  if hdiutil detach "$backup_disk" >/dev/null 2>&1; then
    break
  fi
  sleep 10
done

pkill -TERM -f '^/usr/bin/dns-sd -P ARBITER _smb\._tcp local 445 arbiter-tail\.local 100\.84\.179\.69$' \
  >/dev/null 2>&1 || true

ssh -o BatchMode=yes -o ConnectTimeout=8 arbiter-pirouter \
  'sudo systemctl start pirouter-archive-backup.timer' >/dev/null 2>&1 || true

launchctl enable "gui/$uid_value/com.pirouter.portable-export" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$uid_value" "$agent_plist" >/dev/null 2>&1 || true
