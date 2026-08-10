#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer is intended for Raspberry Pi OS or another Linux host." >&2
  exit 1
fi

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
operator_user="$(id -un)"
config_dir="/etc/pirouter"
ssh_key_file="$config_dir/archive-ssh-key"
password_file="$config_dir/restic-password"
environment_file="$config_dir/archive-backup.env"
time_machine_source="${TIME_MACHINE_SOURCE:-/srv/timemachine}"
archive_ssh_target="${ARCHIVE_SSH_TARGET:-}"
archive_path="${ARCHIVE_PATH:-/mnt/user/backups/PiRouter-TimeMachine.restic}"
archive_repository="${ARCHIVE_REPOSITORY:-}"
ssh_key_source="${ARCHIVE_SSH_KEY_SOURCE:-}"
password_source="${RESTIC_PASSWORD_SOURCE:-}"
tailscale_target="${ARCHIVE_TAILSCALE_TARGET:-}"

if [[ -z "$archive_ssh_target" ]]; then
  read -r -p "Unraid SSH target (for example backup@server): " archive_ssh_target
fi
if [[ -z "$archive_repository" ]]; then
  archive_repository="sftp:$archive_ssh_target:$archive_path"
fi

if [[ -z "$archive_ssh_target" ]]; then
  echo "Unraid SSH target is required." >&2
  exit 1
fi
if [[ -n "$ssh_key_source" ]] && ! sudo test -r "$ssh_key_source"; then
  echo "Archive SSH key is unavailable: $ssh_key_source" >&2
  exit 1
fi
if [[ -n "$password_source" ]] && ! sudo test -r "$password_source"; then
  echo "Restic recovery password is unavailable: $password_source" >&2
  exit 1
fi

missing_packages=()
command -v jq >/dev/null 2>&1 || missing_packages+=(jq)
command -v openssl >/dev/null 2>&1 || missing_packages+=(openssl)
command -v restic >/dev/null 2>&1 || missing_packages+=(restic)
command -v ssh >/dev/null 2>&1 || missing_packages+=(openssh-client)
if (( ${#missing_packages[@]} > 0 )); then
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y "${missing_packages[@]}"
fi

password_temp="$(mktemp)"
environment_temp="$(mktemp)"
sudoers_temp="$(mktemp)"
trap 'rm -f -- "$password_temp" "$environment_temp" "$sudoers_temp"' EXIT
chmod 0600 "$password_temp" "$environment_temp" "$sudoers_temp"

sudo install -d -m 0700 "$config_dir" /root/.ssh
if ! sudo test -s "$ssh_key_file"; then
  if [[ -n "$ssh_key_source" ]]; then
    sudo install -m 0600 "$ssh_key_source" "$ssh_key_file"
    if sudo test -r "${ssh_key_source}.pub"; then
      sudo install -m 0644 "${ssh_key_source}.pub" "${ssh_key_file}.pub"
    fi
  else
    sudo ssh-keygen -q -t ed25519 -N "" -f "$ssh_key_file" \
      -C "pirouter-archive@$(hostname --short)"
  fi
fi

if ! sudo test -s "$password_file"; then
  if [[ -n "$password_source" ]]; then
    sudo install -m 0600 "$password_source" "$password_file"
  else
    openssl rand -base64 48 > "$password_temp"
    sudo install -m 0600 "$password_temp" "$password_file"
  fi
fi

sudo ssh -i "$ssh_key_file" \
  -o BatchMode=yes \
  -o StrictHostKeyChecking=accept-new \
  -o ConnectTimeout=15 \
  "$archive_ssh_target" true

printf '%s\n' \
  "TIME_MACHINE_SOURCE=$time_machine_source" \
  "ARCHIVE_REPOSITORY=$archive_repository" \
  "ARCHIVE_SSH_TARGET=$archive_ssh_target" \
  "ARCHIVE_SSH_KEY=$ssh_key_file" \
  "RESTIC_PASSWORD_FILE=$password_file" \
  "ARCHIVE_TAILSCALE_TARGET=$tailscale_target" \
  'QUIET_MINUTES=20' \
  'COPY_GUARD_SECONDS=30' \
  'CONNECT_TIMEOUT_SECONDS=15' \
  'TAILSCALE_PING_ATTEMPTS=3' \
  'RESTIC_PROGRESS_FPS=0.2' \
  'RESTIC_CACHE_DIR=/var/cache/pirouter-restic' \
  'KEEP_DAILY=14' \
  'KEEP_WEEKLY=8' \
  'KEEP_MONTHLY=12' > "$environment_temp"
sudo install -m 0600 "$environment_temp" "$environment_file"

sftp_command="/usr/bin/ssh -i $ssh_key_file -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 $archive_ssh_target -s sftp"
if ! sudo env RESTIC_PASSWORD_FILE="$password_file" \
  restic -o "sftp.command=$sftp_command" --repo "$archive_repository" \
  snapshots >/dev/null 2>&1; then
  sudo env RESTIC_PASSWORD_FILE="$password_file" \
    restic -o "sftp.command=$sftp_command" --repo "$archive_repository" init
fi

sudo install -m 0755 "$project_dir/scripts/archive-backup.sh" \
  /usr/local/sbin/pirouter-archive-backup
for unit in \
  pirouter-archive-backup.service \
  pirouter-archive-backup.timer \
  pirouter-archive-maintenance.service \
  pirouter-archive-maintenance.timer; do
  sudo install -m 0644 "$project_dir/systemd/$unit" "/etc/systemd/system/$unit"
done

sudo install -d -m 0755 /var/lib/pirouter

printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block pirouter-archive-backup.service\n' \
  "$operator_user" > "$sudoers_temp"
sudo visudo -cf "$sudoers_temp"
sudo install -m 0440 "$sudoers_temp" /etc/sudoers.d/pirouter-archive-backup

sudo systemctl daemon-reload
sudo systemctl enable --now \
  pirouter-archive-backup.timer \
  pirouter-archive-maintenance.timer
sudo systemctl start --no-block pirouter-archive-backup.service

echo
echo "Independent Unraid archiving is installed."
echo "The Pi checks every 30 minutes and copies only after Time Machine is quiet."
echo "Save a secure recovery copy of the Restic password at $password_file."
