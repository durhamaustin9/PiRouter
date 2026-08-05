#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer is intended for Raspberry Pi OS or another Linux host." >&2
  exit 1
fi

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
operator_user="$(id -un)"
config_dir="/etc/pirouter"
credentials_file="$config_dir/archive-smb.credentials"
password_file="$config_dir/restic-password"
environment_file="$config_dir/archive-backup.env"
mount_path="${ARCHIVE_MOUNT:-/mnt/pirouter-archive}"
repository_path="${ARCHIVE_REPOSITORY:-$mount_path/TimeMachine.restic}"
time_machine_source="${TIME_MACHINE_SOURCE:-/srv/timemachine}"
archive_share="${ARCHIVE_SHARE:-}"
archive_username="${ARCHIVE_USERNAME:-}"

if [[ -z "$archive_share" ]]; then
  read -r -p "Archive SMB share (for example //server/backup): " archive_share
fi
if [[ -z "$archive_username" ]]; then
  read -r -p "Archive SMB username: " archive_username
fi
read -r -s -p "Archive SMB password: " archive_password
echo

if [[ -z "$archive_share" || -z "$archive_username" || -z "$archive_password" ]]; then
  echo "Archive share, username, and password are required." >&2
  exit 1
fi

sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y cifs-utils jq openssl restic

credentials_temp="$(mktemp)"
password_temp="$(mktemp)"
environment_temp="$(mktemp)"
sudoers_temp="$(mktemp)"
trap 'rm -f -- "$credentials_temp" "$password_temp" "$environment_temp" "$sudoers_temp"' EXIT
chmod 0600 "$credentials_temp" "$password_temp" "$environment_temp" "$sudoers_temp"

printf 'username=%s\npassword=%s\n' "$archive_username" "$archive_password" > "$credentials_temp"
unset archive_password

sudo install -d -m 0700 "$config_dir"
sudo install -m 0600 "$credentials_temp" "$credentials_file"

if ! sudo test -s "$password_file"; then
  openssl rand -base64 48 > "$password_temp"
  sudo install -m 0600 "$password_temp" "$password_file"
fi

printf '%s\n' \
  "TIME_MACHINE_SOURCE=$time_machine_source" \
  "ARCHIVE_SHARE=$archive_share" \
  "ARCHIVE_MOUNT=$mount_path" \
  "ARCHIVE_REPOSITORY=$repository_path" \
  "ARCHIVE_CREDENTIALS_FILE=$credentials_file" \
  "RESTIC_PASSWORD_FILE=$password_file" \
  'QUIET_MINUTES=20' \
  'COPY_GUARD_SECONDS=30' \
  'RESTIC_CACHE_DIR=/var/cache/pirouter-restic' \
  'KEEP_DAILY=14' \
  'KEEP_WEEKLY=8' \
  'KEEP_MONTHLY=12' > "$environment_temp"
sudo install -m 0600 "$environment_temp" "$environment_file"

sudo install -m 0755 "$project_dir/scripts/archive-backup.sh" \
  /usr/local/sbin/pirouter-archive-backup
for unit in \
  pirouter-archive-backup.service \
  pirouter-archive-backup.timer \
  pirouter-archive-maintenance.service \
  pirouter-archive-maintenance.timer; do
  sudo install -m 0644 "$project_dir/systemd/$unit" "/etc/systemd/system/$unit"
done

sudo install -d -m 0755 "$mount_path" /var/lib/pirouter
if ! mountpoint -q "$mount_path"; then
  sudo mount -t cifs "$archive_share" "$mount_path" \
    -o "credentials=$credentials_file,vers=3.1.1,iocharset=utf8,noserverino,uid=0,gid=0,file_mode=0600,dir_mode=0700"
fi

if ! sudo env RESTIC_PASSWORD_FILE="$password_file" \
  restic --repo "$repository_path" snapshots >/dev/null 2>&1; then
  sudo env RESTIC_PASSWORD_FILE="$password_file" \
    restic --repo "$repository_path" init
fi

printf '%s ALL=(root) NOPASSWD: /usr/bin/systemctl start --no-block pirouter-archive-backup.service\n' \
  "$operator_user" > "$sudoers_temp"
sudo visudo -cf "$sudoers_temp"
sudo install -m 0440 "$sudoers_temp" /etc/sudoers.d/pirouter-archive-backup

sudo systemctl daemon-reload
sudo systemctl enable --now \
  pirouter-archive-backup.timer \
  pirouter-archive-maintenance.timer
sudo systemctl start pirouter-archive-backup.service

echo
echo "Secondary archive replication is installed."
echo "Automatic checks run every 30 minutes and copy only when Time Machine is idle."
echo "Save a secure recovery copy of the Restic password stored at $password_file."
