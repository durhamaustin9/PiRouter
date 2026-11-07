#!/bin/bash
set -uo pipefail

verifier_pid="${1:?verifier PID is required}"
backup_disk="${2:?the verified backup image disk is required}"
discovery_pid="${PIROUTER_DISCOVERY_PID:-}"
archive_ssh_target="${ARCHIVE_SSH_TARGET:-}"
uid_value="$(id -u)"
agent_plist="$HOME/Library/LaunchAgents/com.pirouter.portable-export.plist"

if [[ ! "$verifier_pid" =~ ^[1-9][0-9]*$ ]]; then
  exit 64
fi
if [[ ! "$backup_disk" =~ ^/dev/disk[0-9]+$ ]]; then
  exit 64
fi
if [[ -n "$discovery_pid" && ! "$discovery_pid" =~ ^[1-9][0-9]*$ ]]; then
  exit 64
fi
if [[ -n "$archive_ssh_target" && ! "$archive_ssh_target" =~ ^[A-Za-z0-9][A-Za-z0-9._@:-]*$ ]]; then
  exit 64
fi

while kill -0 "$verifier_pid" 2>/dev/null; do
  sleep 30
done

# Let Time Machine release the mounted snapshot before detaching its image.
sleep 10
detached=false
for _ in 1 2 3 4 5; do
  if hdiutil detach "$backup_disk" >/dev/null 2>&1; then
    detached=true
    break
  fi
  sleep 10
done
if [[ "$detached" != true ]]; then
  echo "The verified backup image is still busy; background work remains paused." >&2
  exit 1
fi

# Only stop an explicitly supplied discovery helper belonging to this user.
if [[ -n "$discovery_pid" ]] && kill -0 "$discovery_pid" 2>/dev/null; then
  discovery_owner="$(ps -p "$discovery_pid" -o uid=)"
  discovery_command="$(ps -p "$discovery_pid" -o comm=)"
  if [[ "${discovery_owner//[[:space:]]/}" != "$uid_value" || "${discovery_command//[[:space:]]/}" != /usr/bin/dns-sd ]]; then
    echo "The supplied discovery PID is not this user's dns-sd helper." >&2
    exit 1
  fi
  kill -TERM "$discovery_pid" 2>/dev/null || true
fi

# Remote archive resumption is optional and requires an explicit trusted target.
if [[ -n "$archive_ssh_target" ]]; then
  ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 "$archive_ssh_target" \
    'sudo -n systemctl start pirouter-archive-backup.timer' >/dev/null 2>&1 || true
fi

launchctl enable "gui/$uid_value/com.pirouter.portable-export" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$uid_value" "$agent_plist" >/dev/null 2>&1 || true
