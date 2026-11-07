#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer is intended for Raspberry Pi OS or another Linux host." >&2
  exit 1
fi

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
operator_user="$(id -un)"
config_dir="/etc/pirouter"
environment_file="$config_dir/archive-backup.env"

existing_setting() {
  sudo awk -v key="$1" 'index($0, key "=") == 1 { print substr($0, length(key) + 2) }' \
    "$environment_file" 2>/dev/null | tail -n 1 || true
}

configured_setting() {
  local key="$1"
  local fallback="${2:-}"
  local value="${!key:-}"
  [[ -n "$value" ]] || value="$(existing_setting "$key")"
  printf '%s' "${value:-$fallback}"
}

time_machine_source="$(configured_setting TIME_MACHINE_SOURCE /srv/timemachine)"
ssh_key_file="$(configured_setting ARCHIVE_SSH_KEY "$config_dir/archive-ssh-key")"
password_file="$(configured_setting RESTIC_PASSWORD_FILE "$config_dir/restic-password")"
saved_ssh_target="$(existing_setting ARCHIVE_SSH_TARGET)"
archive_ssh_target="${ARCHIVE_SSH_TARGET:-$saved_ssh_target}"
archive_path="${ARCHIVE_PATH:-/mnt/user/time-machine-austin/PiRouter-TimeMachine.restic}"
archive_repository="${ARCHIVE_REPOSITORY:-}"
if [[ -z "$archive_repository" && -z "${ARCHIVE_PATH:-}" && "$archive_ssh_target" == "$saved_ssh_target" ]]; then
  archive_repository="$(existing_setting ARCHIVE_REPOSITORY)"
fi
ssh_key_source="${ARCHIVE_SSH_KEY_SOURCE:-}"
password_source="${RESTIC_PASSWORD_SOURCE:-}"
tailscale_target="$(configured_setting ARCHIVE_TAILSCALE_TARGET)"
remove_local_after_archive="$(configured_setting REMOVE_LOCAL_AFTER_ARCHIVE false)"
portable_export_status_file="$(configured_setting PORTABLE_EXPORT_STATUS_FILE)"
verify_restic_bin="$(configured_setting ARCHIVE_VERIFY_RESTIC_BIN)"
verify_repository_path="$(configured_setting ARCHIVE_VERIFY_REPOSITORY_PATH)"
quiet_minutes="$(configured_setting QUIET_MINUTES 20)"
copy_guard_seconds="$(configured_setting COPY_GUARD_SECONDS 30)"
connect_timeout_seconds="$(configured_setting CONNECT_TIMEOUT_SECONDS 15)"
tailscale_ping_attempts="$(configured_setting TAILSCALE_PING_ATTEMPTS 3)"
progress_fps="$(configured_setting RESTIC_PROGRESS_FPS 0.2)"
restic_cache="$(configured_setting RESTIC_CACHE_DIR /var/cache/pirouter-restic)"
backup_host="$(configured_setting RESTIC_BACKUP_HOST)"
backup_tag="$(configured_setting RESTIC_BACKUP_TAG timemachine)"
keep_daily="$(configured_setting KEEP_DAILY 14)"
keep_weekly="$(configured_setting KEEP_WEEKLY 8)"
keep_monthly="$(configured_setting KEEP_MONTHLY 12)"

# Restic reserves exit 10 for a missing repository (version 0.17 and newer).
# Authentication, connectivity, corruption, and unknown failures must never
# trigger initialization or replace an existing installation's configuration.
prepare_archive_repository() {
  local probe_result=0
  local probe_output=""
  local repository_path=""
  local remote_probe=""
  probe_output="$(sudo env RESTIC_PASSWORD_FILE="$password_file" \
    restic --no-cache --no-lock -o "sftp.command=$sftp_command" \
    --repo "$archive_repository" cat config 2>&1)" || probe_result=$?
  if [[ "$probe_result" -eq 0 ]]; then
    return 0
  fi
  if [[ "$probe_result" -ne 10 ]]; then
    printf 'Could not open the existing archive repository (Restic exit %s). Initialization was not attempted.\n%s\n' \
      "$probe_result" "$probe_output" >&2
    return 1
  fi

  if [[ "$archive_repository" != "sftp:$archive_ssh_target:"* ]]; then
    echo "The missing repository must be initialized separately; its SFTP destination does not match the archive SSH target." >&2
    return 1
  fi
  repository_path="${archive_repository#"sftp:$archive_ssh_target:"}"
  if [[ ! "$repository_path" =~ ^/[A-Za-z0-9._/-]+$ || "$repository_path" == / ||
    "$repository_path" =~ (^|/)\.\.?(/|$) ]]; then
    echo "The missing archive repository needs an absolute dedicated path without traversal components." >&2
    return 1
  fi
  remote_probe="repository_path='$repository_path'
candidate=\"\$repository_path\"
while [ \"\$candidate\" != / ]; do
  [ ! -L \"\$candidate\" ] || exit 1
  candidate=\"\${candidate%/*}\"
  [ -n \"\$candidate\" ] || candidate=/
done
if [ -d \"\$repository_path\" ]; then
  entries=\"\$(find \"\$repository_path\" -mindepth 1 -maxdepth 1 -print -quit)\" || exit 1
  [ -z \"\$entries\" ] || exit 1
elif [ -e \"\$repository_path\" ]; then
  exit 1
fi"
  if ! sudo ssh -i "$ssh_key_file" -o BatchMode=yes \
    -o StrictHostKeyChecking=yes -o ConnectTimeout=15 \
    "$archive_ssh_target" "$remote_probe"; then
    echo "The missing repository could not be confirmed absent or empty. Existing archive data was left untouched." >&2
    return 1
  fi
  sudo env RESTIC_PASSWORD_FILE="$password_file" \
    restic -o "sftp.command=$sftp_command" --repo "$archive_repository" init
}

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

sftp_command="/usr/bin/ssh -i $ssh_key_file -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=3 $archive_ssh_target -s sftp"
prepare_archive_repository

printf '%s\n' \
  "TIME_MACHINE_SOURCE=$time_machine_source" \
  "ARCHIVE_REPOSITORY=$archive_repository" \
  "ARCHIVE_SSH_TARGET=$archive_ssh_target" \
  "ARCHIVE_SSH_KEY=$ssh_key_file" \
  "RESTIC_PASSWORD_FILE=$password_file" \
  "ARCHIVE_TAILSCALE_TARGET=$tailscale_target" \
  "QUIET_MINUTES=$quiet_minutes" \
  "COPY_GUARD_SECONDS=$copy_guard_seconds" \
  "CONNECT_TIMEOUT_SECONDS=$connect_timeout_seconds" \
  "TAILSCALE_PING_ATTEMPTS=$tailscale_ping_attempts" \
  "RESTIC_PROGRESS_FPS=$progress_fps" \
  "RESTIC_CACHE_DIR=$restic_cache" \
  "RESTIC_BACKUP_HOST=$backup_host" \
  "RESTIC_BACKUP_TAG=$backup_tag" \
  "KEEP_DAILY=$keep_daily" \
  "KEEP_WEEKLY=$keep_weekly" \
  "KEEP_MONTHLY=$keep_monthly" > "$environment_temp"
printf '%s\n' \
  "REMOVE_LOCAL_AFTER_ARCHIVE=${remove_local_after_archive:-false}" \
  "PORTABLE_EXPORT_STATUS_FILE=$portable_export_status_file" \
  "ARCHIVE_VERIFY_RESTIC_BIN=$verify_restic_bin" \
  "ARCHIVE_VERIFY_REPOSITORY_PATH=$verify_repository_path" >> "$environment_temp"
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
