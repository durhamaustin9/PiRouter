# PiRouter

PiRouter is a touch-first, locally hosted status console for a Raspberry Pi 5 and the official 7-inch touchscreen. It is designed for the original 800×480 display, Touch Display 2 in landscape, and ordinary desktop browsers.

At a glance, the dashboard shows:

- live network throughput, latency, packet loss, DNS, and link state;
- Time Machine share activity and backup-storage capacity;
- an optional, configurable TCP service-endpoint check;
- Tailscale state and peer counts;
- CPU load, temperature, memory, storage, and uptime;
- browsable, timestamped exports of completed Time Machine snapshots;
- an operator-triggered Ookla speed test with progress reporting.

Card titles open touch-friendly explanations of each reading. The header and bottom navigation stay fixed over scrolling content, and Focus mode hides both bars while leaving a one-tap restore control visible.

## Hardware used

- Raspberry Pi 5 with 16 GB RAM
- Official Raspberry Pi 7-inch Touch Display
- NUO8 bottom-mounted USB storage adapter for Raspberry Pi 5
- 1 TB 2.5-inch SSD

## Privacy model

PiRouter is intended to run on a trusted local network or tailnet. Status, history, hostnames, and backup information remain on the device; the application does not send telemetry to a hosted service.

Keep `.env`, SQLite data, SSH keys, pairing tokens, and operator notes outside Git. The included crawler policy blocks the live dashboard and API surface, but it is not a substitute for network access controls. Do not expose the dashboard directly to the public internet.

## Stack

- Next.js 16 and React 19
- Mantine and Font Awesome
- Prisma with local SQLite history
- Tailscale CLI integration
- Raspberry Pi OS and Linux telemetry
- Samba-aware Time Machine activity detection
- macOS-assisted portable Time Machine exports

## Develop locally

```bash
cp .env.example .env
npm install
npm run dev
```

Open `http://localhost:3000`. Linux-only metrics degrade gracefully on macOS, allowing the interface to be developed away from the Pi.

## Configuration

Edit `.env` without committing it:

```dotenv
DATABASE_URL="file:./data/pirouter.db"
DASHBOARD_NAME="PiRouter"
LOCATION_LABEL="Home network"
NETWORK_INTERFACE="auto"
HEALTH_TARGET="1.1.1.1"

ENDPOINT_LABEL="Internal service"
ENDPOINT_HOST="service.example"
ENDPOINT_PORT="443"

TIME_MACHINE_PATH="/srv/timemachine"
TIME_MACHINE_SERVICE="smbd"
TIME_MACHINE_ACTIVE_WINDOW_MINUTES="3"
TIME_MACHINE_STALE_HOURS="36"

PORTABLE_EXPORT_TOKEN="replace-with-a-random-64-character-hex-token"
PORTABLE_EXPORT_AUTOMATIC="true"
PORTABLE_EXPORT_STATUS_PATH="./data/portable-export.json"
```

The endpoint monitor performs only a TCP connection handshake. Leave `ENDPOINT_HOST` empty to disable it. `TIME_MACHINE_PATH` should reference the server-side directory containing macOS backup bundles.

## Install on Raspberry Pi OS Desktop

Install Node.js 22.13 or newer and Tailscale, copy the repository onto the Pi, then run:

```bash
npm run install:pi
```

The installer builds the application, creates a user systemd service, starts the local dashboard, and registers a Chromium kiosk launcher for the desktop session. An existing `.env` file is preserved.

The touchscreen powers off after 60 seconds without input and wakes on touch. Only the display is powered down; routing, Samba, Time Machine, the dashboard service, and scheduled jobs continue running. Set `PIROUTER_DISPLAY_IDLE_SECONDS` or `PIROUTER_DISPLAY_OUTPUT` in the kiosk environment to override the defaults.

Useful service commands:

```bash
systemctl --user status pirouter-dashboard
systemctl --user restart pirouter-dashboard
journalctl --user -u pirouter-dashboard -f
```

To proxy the local dashboard through an authenticated tailnet:

```bash
tailscale serve --bg http://127.0.0.1:3000
```

## Optional speed test

The Network screen runs a test only when requested and prevents concurrent tests across connected dashboards. Install the official CLI on Raspberry Pi OS or Debian with:

```bash
bash scripts/install-speedtest.sh
```

## Optional browsable Time Machine exports

Network Time Machine destinations are sparsebundle disk images. That format is required by macOS, but Linux storage servers cannot safely present the APFS snapshots inside it as ordinary files. PiRouter therefore coordinates with a helper on the Mac that owns the backup. macOS mounts the latest completed snapshot read-only, then `rsync` writes a normal timestamped directory to a secondary server over SSH.

Each export is directly browsable. Unchanged files are hard-linked to the previous exported directory when the destination filesystem supports hard links, preserving history without recopying every unchanged file. Interrupted `.partial` directories resume on the next request. The original Time Machine sparsebundle is never modified or copied to the secondary server.

First install PiRouter on the Pi so `.env` contains a generated `PORTABLE_EXPORT_TOKEN`. On the Mac being backed up, run:

```bash
npm run install:mac-export
```

The macOS installer prompts for the Pi URL, pairing token, Unraid SSH target, destination under `/mnt/user`, and SSH key. It uses Homebrew rsync 3 to preserve metadata across macOS and Linux, then creates a background-only app and LaunchAgent that checks every five minutes. Automatic exports are limited to once per 24 hours by default; the Services screen can request one immediately. Grant the installed **PiRouter Exporter** app Full Disk Access so `tmutil` can mount completed snapshots.

Portable exports are intended for convenient file recovery. Keep the original Time Machine destination for full Migration Assistant or macOS Recovery restores.

## Local history

The status endpoint stores at most one sample per minute in `data/pirouter.db`. The UI reads the most recent 90 samples, and samples older than seven days are pruned automatically. The database and its journal files are ignored by Git.

## Project structure

- `app/` — Next.js routes, metadata, and layered styles
- `components/dashboard/` — touchscreen shell, views, UI primitives, and controllers
- `lib/status/` — cached network, service, system, and summary probes
- `lib/types.ts` — shared domain data structures
- `prisma/` — SQLite schema and migrations
- `scripts/` — Pi installation, kiosk, display, speed-test, and macOS export tooling
- `systemd/` — Samba reliability configuration

Core status refreshes every ten seconds and pauses while the page is hidden. Expensive probes have independent caches, concurrent requests share one collection pass, and speed tests and portable exports run only on demand or their configured schedules.
