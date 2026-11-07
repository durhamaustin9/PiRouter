#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
operator_user="$(id -un)"
node_path="$(command -v node || true)"
unit_path="/etc/systemd/system/pirouter-kms-relay.service"

if [[ "$(uname -s)" != "Linux" || -z "$node_path" ]]; then
  echo "Install this relay on the Pi after Node.js is installed." >&2
  exit 1
fi

listen_host="${KMS_RELAY_LISTEN_HOST:-}"
upstream_host="${KMS_RELAY_UPSTREAM_HOST:-}"
listen_port="${KMS_RELAY_LISTEN_PORT:-1688}"
upstream_port="${KMS_RELAY_UPSTREAM_PORT:-1688}"
if [[ -z "$listen_host" || -z "$upstream_host" ]]; then
  echo "Set KMS_RELAY_LISTEN_HOST to the Pi's LAN address and KMS_RELAY_UPSTREAM_HOST to the authorized server's Tailscale hostname before installing." >&2
  exit 1
fi
for value in "$project_dir" "$operator_user" "$node_path" "$listen_host" "$upstream_host" "$listen_port" "$upstream_port"; do
  if [[ "$value" == *$'\n'* || "$value" == *$'\r'* ]]; then
    echo "Newlines in relay settings or installation paths are unsupported." >&2
    exit 1
  fi
done

# Quote values for systemd, including its percent-specifier expansion.
systemd_quote() {
  local value="$1"
  value="${value//\\/\\\\}"
  value="${value//\"/\\\"}"
  value="${value//%/%%}"
  printf '"%s"' "$value"
}

if ! node -e '
  const dns = require("node:dns");
  const net = require("node:net");
  for (const value of process.argv.slice(2)) {
    if (!/^[0-9]+$/.test(value) || Number(value) < 1 || Number(value) > 65535) process.exit(1);
  }
  const tailnet = new net.BlockList();
  tailnet.addSubnet("100.64.0.0", 10, "ipv4");
  dns.lookup(process.argv[1], { family: 4 }, (error, address) => {
    if (error || !tailnet.check(address, "ipv4")) process.exitCode = 1;
  });
' "$upstream_host" "$listen_port" "$upstream_port"; then
  echo "Use valid TCP ports and an upstream hostname that resolves to its Tailscale IPv4 address on the Pi." >&2
  exit 1
fi

unit_temp="$(mktemp)"
trap 'rm -f -- "$unit_temp"' EXIT
cat > "$unit_temp" <<EOF
[Unit]
Description=PiRouter KMS TCP relay
Wants=network-online.target
After=network-online.target tailscaled.service

[Service]
Type=simple
User=$operator_user
WorkingDirectory=$(systemd_quote "$project_dir")
ExecStart=$(systemd_quote "$node_path") $(systemd_quote "$project_dir/scripts/kms-relay.mjs")
Environment=$(systemd_quote "KMS_RELAY_LISTEN_HOST=$listen_host")
Environment=$(systemd_quote "KMS_RELAY_UPSTREAM_HOST=$upstream_host")
Environment=$(systemd_quote "KMS_RELAY_LISTEN_PORT=$listen_port")
Environment=$(systemd_quote "KMS_RELAY_UPSTREAM_PORT=$upstream_port")
Restart=on-failure
RestartSec=3
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only

[Install]
WantedBy=multi-user.target
EOF

sudo install -m 0644 "$unit_temp" "$unit_path"
sudo systemctl daemon-reload
sudo systemctl enable pirouter-kms-relay.service
sudo systemctl restart pirouter-kms-relay.service
sudo systemctl is-active --quiet pirouter-kms-relay.service
sudo systemctl status --no-pager pirouter-kms-relay.service
