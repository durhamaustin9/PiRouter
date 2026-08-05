#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer is intended for Raspberry Pi OS or Debian Linux." >&2
  exit 1
fi

if command -v speedtest >/dev/null 2>&1; then
  echo "Ookla Speedtest CLI is already installed:"
  speedtest --version
  exit 0
fi

case "$(uname -m)" in
  aarch64|arm64)
    speedtest_arch="aarch64"
    speedtest_sha256="3953d231da3783e2bf8904b6dd72767c5c6e533e163d3742fd0437affa431bd3"
    ;;
  *)
    echo "This installer currently supports the Raspberry Pi ARM64 architecture." >&2
    exit 1
    ;;
esac

speedtest_temp="$(mktemp -d)"
trap 'rm -rf -- "$speedtest_temp"' EXIT

speedtest_version="1.2.0"
speedtest_archive="$speedtest_temp/speedtest.tgz"
curl -fsSL \
  "https://install.speedtest.net/app/cli/ookla-speedtest-${speedtest_version}-linux-${speedtest_arch}.tgz" \
  -o "$speedtest_archive"
printf '%s  %s\n' "$speedtest_sha256" "$speedtest_archive" | sha256sum -c -
tar -xzf "$speedtest_archive" -C "$speedtest_temp"

sudo install -m 0755 "$speedtest_temp/speedtest" /usr/local/bin/speedtest
sudo install -d -m 0755 /usr/local/share/man/man5
sudo install -m 0644 "$speedtest_temp/speedtest.5" \
  /usr/local/share/man/man5/speedtest.5

echo
echo "Ookla Speedtest CLI installed successfully:"
speedtest --version
