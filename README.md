# PiRouter

PiRouter is a touch-first, locally hosted status console for a Raspberry Pi 5 and the official 7-inch touchscreen. It is designed for the original 800×480 display, Touch Display 2 in landscape, and ordinary desktop browsers.

At a glance, the dashboard shows:

- live network throughput, latency, packet loss, DNS, and link state;
- Time Machine share activity and backup-storage capacity;
- an optional, configurable TCP service-endpoint check;
- Tailscale state and peer counts;
- CPU load, temperature, memory, storage, and uptime;
- independent encrypted Time Machine archives uploaded by the Pi;
- optional browsable, timestamped exports of completed snapshots;
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
- Pi-to-Unraid Restic replication with automatic retry
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
ARCHIVE_STATUS_PATH="/var/lib/pirouter/archive-status.json"
```

The endpoint monitor performs only a TCP connection handshake. Leave `ENDPOINT_HOST` empty to disable it. `TIME_MACHINE_PATH` should reference the server-side directory containing macOS backup bundles.

## KMS relay for local DNS

When local DNS points a KMS hostname at the Pi, clients connect to the Pi
on TCP port 1688. The dashboard's outbound endpoint check does not test this
incoming connection. Install the separate relay with explicit listen and
upstream hosts, replacing the examples with your own deployment values:

```bash
KMS_RELAY_LISTEN_HOST=192.0.2.10 \
KMS_RELAY_UPSTREAM_HOST=kms-server.example npm run install:kms-relay
```

The relay listens only on the configured address and forwards TCP connections
to the configured upstream, which the Pi must resolve and reach. If that route
uses Tailscale, the upstream server must allow the Pi's Tailscale source address.
Check the incoming port from another LAN device with
`nc -vz router.example 1688`, then verify KMS activation from a client.
The relay's system service is
`pirouter-kms-relay.service`.

If the Pi's address or upstream hostname changes, set `KMS_RELAY_LISTEN_HOST` or
`KMS_RELAY_UPSTREAM_HOST` in a systemd service override before restarting it.

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

The dashboard listens on the Pi's LAN interfaces so companion devices on the
trusted home network can connect directly. To also proxy it through an
authenticated tailnet:

```bash
tailscale serve --bg http://127.0.0.1:3000
```

## Vilros GPIO fan

This three-wire Vilros fan profile uses physical pin 4 for 5V, pin 9 for
ground, and pin 8 (GPIO14) for control. Its profile is in
`config/vilros-fan.txt`, installed as `/boot/firmware/pirouter-fan.txt` and
included from `/boot/firmware/config.txt` under `[all]`.

The fan runs continuously at 80% PWM and increases to full power at 55°C.
It returns to 80% after cooling below 50°C. The 80% minimum prevents stalls
at lower duty cycles. The kernel's `pwm-gpio-fan` driver controls it independently of the dashboard
and backup services.
Changes to this boot profile take effect after restarting the Pi. This fan
does not report RPM, so its physical rotation must be checked directly.

## Optional speed test

The Network screen runs a test only when requested and prevents concurrent tests across connected dashboards. Install the official CLI on Raspberry Pi OS or Debian with:

```bash
bash scripts/install-speedtest.sh
```

## Independent Unraid archive

The primary secondary-backup path runs entirely on the Pi. After the Time Machine
bundle has been quiet for 20 minutes, the Pi stores an encrypted, deduplicated
Restic snapshot on Unraid over SSH/SFTP. A Mac does not need to be awake or on the
same network. If Tailscale or SMB disconnects, the service fails safely, retries
after five minutes, and reuses chunks that already reached Unraid.

The Pi monitors the source throughout each transfer. If Time Machine begins
writing, the incomplete Restic snapshot is discarded and retried later. The
original sparsebundle is only read unless verified cleanup is enabled below.
Install the archive on the Pi with:

```bash
npm run install:archive
```

The installer asks for the Unraid SSH target, initializes a
Restic repository, and schedules checks every 30 minutes. Save a separate secure
copy of `/etc/pirouter/restic-password`; both the repository and that password
are required for recovery. Restic archives protect the full Time Machine bundle
but are not directly browsable—restore the bundle with Restic before opening it
with Time Machine or Migration Assistant.

On Unraid, PiRouter uses the `time-machine-austin` share for its backup data.
New archive installations default to
`/mnt/user/time-machine-austin/PiRouter-TimeMachine.restic`; new Mac exporter
installations default to `/mnt/user/time-machine-austin`, with browsable
copies beneath `snapshots/`. The encrypted Restic repository is essential for
restoring the full backup and must be retained alongside the browsable copies.

Reinstalling preserves the configured destination, recovery credentials, retention,
and cleanup settings. Initialization requires Restic's explicit missing-repository
result and an absent or empty destination. Authentication and connection failures
stop installation with diagnostics. A recreated repository needs a fresh archive
before it contains a recoverable backup.

Changing a destination setting does not move existing backup files. A repository
relocation must preserve the complete repository and recovery password and update
`ARCHIVE_REPOSITORY` and any `ARCHIVE_VERIFY_REPOSITORY_PATH` together. Existing
export destinations are also preserved on upgrades rather than silently redirecting
an unfinished upload.

### Free local SSD space after upload

Set `REMOVE_LOCAL_AFTER_ARCHIVE=true` in `/etc/pirouter/archive-backup.env`
to remove local backup bundles after a successful upload and a full Restic
`check --read-data` verification. Each subsequent Time Machine backup starts a
new full bundle. Only `.sparsebundle` and `.backupbundle` directories directly
inside the dedicated backup mount are removed; the filesystem, share settings,
and other files are preserved.

Routine cleanup requires a successful Time Machine result, an idle source, a
valid SMB lock report, and an unchanged source throughout upload and verification.
Samba briefly stops during the final check and removal and restarts afterward.
Interrupted backups stay local so Time Machine can resume them. To also require a
completed, checksum-verified browsable export, set `PORTABLE_EXPORT_STATUS_FILE`
to the dashboard's `data/portable-export.json` file. The export must be newer than
the local bundle's latest change.

Optional `ARCHIVE_VERIFY_RESTIC_BIN` and `ARCHIVE_VERIFY_REPOSITORY_PATH` settings
run full data verification on the archive server over SSH. The repository path
must match the SFTP upload destination; the recovery password is supplied over
SSH stdin. Without these settings verification runs from the Pi.

An operator can explicitly reset an interrupted backup for a fresh test with
`sudo /usr/local/sbin/pirouter-archive-backup reset`. This still uploads and fully
verifies the archive, checks active writers, and preserves unrelated SSD files.
It bypasses the successful-Time-Machine-result and portable-export prerequisites.

## Optional browsable Time Machine exports

Network Time Machine destinations are sparsebundle disk images. That format is required by macOS, but Linux storage servers cannot safely present the APFS snapshots inside it as ordinary files. For ordinary folders in addition to the independent Restic archive, PiRouter coordinates with a helper on the Mac that owns the backup. macOS mounts a completed snapshot read-only, then sends independently verified blocks to Unraid over SSH. The Mac can disconnect between sessions: completed blocks, files, and folder-scanning checkpoints are retained and resume when it reconnects.

`EXPORT_SSH_PROXY_COMMAND` optionally routes the encrypted upload through a trusted
Linux gateway. The gateway helper, `scripts/portable-export-relay.py`, connects to
the fixed destination supplied by its administrator. Install it as a root-owned
file and bind a dedicated SSH key to its forced command with `restrict`. The Mac
continues to verify and authenticate the final destination inside the tunnel.
Measure the gateway's connection before choosing a congestion-control setting;
loss-tolerant TCP on the gateway can improve transfers over a lossy route.

After each export session, the Mac releases the exact Time Machine disk image
used by that job once its readers have stopped and Time Machine reports idle.
The saved image identity is rechecked before a normal eject. Busy images and
uncertain reports stay attached for a later retry. Interrupted mount receipts
are recovered on the next launch.

New installations enable `CONTINUOUS_EXPORT=true`. After an archive has
completed, an unfinished pinned folder copy continues until it finishes while
the Mac is available. Its ten-minute internal checkpoints immediately start the
next session under the same local lock; they do not wait for the five-minute
retry schedule. Connection failures retain progress, release the source image,
and use that retry schedule when the Mac is available again. The Pi can archive
while the Mac is offline.

An active or uncertain archive worker still blocks mounting. Source writer and
image-release checks run again at every session boundary. Before the first
completed archive, a saved handoff keeps the source closed through the Pi's
quiet period, upload, verification, and cleanup. Every original source-write
guard remains in force. `CONTINUOUS_EXPORT=false` retains the bounded policy:
one hour of useful copy time per completed archive, expiring after six hours.

Completed exports are directly browsable. Folders ending in `.partial` are unfinished. Each block is checksum-verified, flushed, and checkpointed on Unraid before it is acknowledged. Large files resume at their last verified block. The helper scans folders incrementally and remembers its position rather than scanning the entire backup again after every reconnect. An unfinished job keeps the same snapshot even when a newer backup becomes available. Existing partial files are reused only after their contents match the source checksums.

Folders, symlinks, and small files travel in bounded groups of up to 64 entries
and 512 KiB of file data. Each small file is at most 512 KiB. Unraid verifies the
contents, synchronizes files and their directories, and commits the whole group
before acknowledging it. The Mac then saves the group's completed entries and
receiver counters together. A lost connection retries an unacknowledged group
without discarding earlier progress or counting files twice. Larger and partly
copied files continue to use the existing resumable block transfer.

Batch writes are submitted together before each file is synchronized and read
back for verification. Typical documents avoid several separate network round
trips, while acknowledgments still wait for every durability check. Bandwidth
pacing credits time already spent transferring and verifying data, so a slow
connection or disk does not receive an additional full transfer-sized delay.

Healthy sessions explicitly close their receiver and release its lock before
the Mac disconnects. Heartbeats keep the receiver alive during source scanning
or transfer pacing. A connection that stops sending or receiving bytes for
three minutes exits automatically, including a partially received frame or
file block. The timer applies only while waiting on the connection, so hashing,
disk synchronization, and active database work keep their write lock. Retries
use the same lock file and saved checkpoints; they never delete a lock file or
take a lock away from an active writer.

Documents, Desktop, photos, downloads, and cloud folders are copied ahead of
regenerable caches. Existing jobs adopt this order without resetting completed
files or block checkpoints; lower-priority paths remain included.

Every backed-up volume must contain readable files, and all discovered entries must finish before completion markers are written and the folder is published. Failed or incomplete transfers remain private partial folders. Mount and transfer diagnostics appear in the dashboard alerts. Completed timestamped export folders are preserved: PiRouter never prunes, rotates, overwrites, or deletes them. The portable exporter only reads the original sparsebundle; optional archive cleanup removes it later under the policy above. The Unraid receiver requires PHP 8.1 or newer with SQLite3; it runs only for the SSH session and does not install a daemon or open another network port.

### Mac application and installer

PiRouter Exporter supports **macOS 13 or later on Apple Silicon and Intel**. Open
`PiRouter-Exporter-3.0.1.dmg`, quit an existing PiRouter Exporter, drag the app to
**Applications**, and open it there. The app includes a private Python runtime;
other Macs do not need Xcode, Command Line Tools, or a separate Python install.
Version 3.0.1 requires native execution for both the app and its private runtime:
Apple Silicon uses arm64 and Intel Macs use x86_64, without Rosetta.

First-run setup asks for the PiRouter address. The dashboard needs only that
address. It prefers a saved direct local-network address and falls back to the
Tailscale address when available. Settings can update the connection later.
Optional exports require that Mac's own authorized pairing token, SSH destination
and key, and Full Disk Access for the installed app. The Pi installation generates
`PORTABLE_EXPORT_TOKEN` in its `.env`. Installer images include **no pairing
settings, passwords, SSH keys, logs, or upload state**.

This local build is signed for integrity but **is not notarized by Apple**. On
another Mac, the first launch may need **System Settings → Privacy & Security →
Open Anyway**. Keep Gatekeeper enabled. A Developer ID identity can sign a future
release, but signing alone does not notarize it.

Build an app and verified drag-to-Applications DMG on a development Mac with:

```bash
npm run build:mac-app
```

Artifacts are written to `dist/macos/`. The build compiles both architectures,
verifies a pinned official Python runtime, signs nested code, verifies the app,
and verifies the disk image. A Developer ID Application identity is selected when
available; otherwise it uses a local ad-hoc signature. Developer builds require
Command Line Tools and Python 3; these tools are unnecessary on recipient Macs.
`PIROUTER_PYTHON_FRAMEWORK` can reuse an already prepared private framework, and
`PIROUTER_BUILD_DIR` can change the artifact directory.

For a safe source-based install or upgrade on this Mac, run:

```bash
npm run install:mac-export
```

The installer prepares and validates a complete replacement before stopping the
running app and its exporter. It preserves pairing, custom settings, export
policies, and resumable upload progress, and restores the previous app and
settings if installation fails. `PIROUTER_PREBUILT_APP` can select a previously
built app and `PIROUTER_NO_OPEN=1` leaves it closed after installation. Automated
checks can use isolated `PIROUTER_APP_DIR`, `PIROUTER_SUPPORT_DIR`, and
`PIROUTER_LAUNCH_AGENT_DIR` paths with `PIROUTER_SKIP_SERVICE=1`.

When exports are enabled, a separate LaunchAgent checks for work every five
minutes even while the dashboard app is closed. Saved settings and upload state
live in `~/Library/Application Support/PiRouter`, outside the app bundle.
The helper preserves file contents, symbolic links, directory structure,
permissions, and timestamps across macOS and Linux. The Services screen shows
retained bytes, completed files, and the last exporter contact. Lost contact
shows a paused job; reconnecting resumes its saved work. macOS ACLs and
extended attributes are omitted because Linux storage cannot represent every APFS
ACL value or macOS resource fork. Root-level Time Machine control files and
protected macOS operating-system state that macOS denies direct access to are also
omitted; they are not part of the browsable user data. The portable exporter
does not modify the original Time Machine backup. Automatic exports are limited to once per 24
hours by default; the Services screen can request one immediately. Grant the
installed app Full Disk Access so `tmutil` can mount completed snapshots.

Portable exports are intended for convenient file recovery. For full Migration Assistant or macOS Recovery restores, keep the original Time Machine destination or restore its full bundle from the encrypted Restic archive.

## Alerts

Tap the header status indicator, overall health card, or Services → Attention
to read every alert and its diagnostics. “Clear for now” hides an alert for one
hour, 24 hours, or seven days, including its health-score deduction. Cleared
alerts remain visible in the dialog and can be restored immediately. They return
when their dismissal expires, their failure details or severity changes, or the condition
recovers and recurs. Dismissals survive dashboard and Pi restarts and apply to all
connected dashboards. Both archive and browsable-export failures are included.

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
