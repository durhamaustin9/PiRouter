#!/usr/bin/env bash
set -euo pipefail

idle_seconds="${PIROUTER_DISPLAY_IDLE_SECONDS:-60}"
display_output="${PIROUTER_DISPLAY_OUTPUT:-DSI-1}"

if [[ ! "$idle_seconds" =~ ^[1-9][0-9]*$ ]]; then
  echo "PiRouter display idle: PIROUTER_DISPLAY_IDLE_SECONDS must be a positive integer." >&2
  exit 1
fi

if [[ ! "$display_output" =~ ^[A-Za-z0-9._*-]+$ ]]; then
  echo "PiRouter display idle: PIROUTER_DISPLAY_OUTPUT contains unsupported characters." >&2
  exit 1
fi

if ! command -v swayidle >/dev/null 2>&1 || ! command -v wlopm >/dev/null 2>&1; then
  echo "PiRouter display idle: swayidle and wlopm are required." >&2
  exit 1
fi

printf -v quoted_output "%q" "$display_output"
printf -v quoted_wlopm "%q" "$(command -v wlopm)"

exec "$(command -v swayidle)" -w \
  timeout "$idle_seconds" "$quoted_wlopm --off $quoted_output" \
  resume "$quoted_wlopm --on $quoted_output"
