#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != Darwin ]]; then
  echo "The Mac app must be built on macOS." >&2
  exit 1
fi

project_dir="${PIROUTER_SOURCE_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
build_dir="${PIROUTER_BUILD_DIR:-$project_dir/dist/macos}"
app_output="${PIROUTER_APP_OUTPUT:-$build_dir/PiRouter Exporter.app}"
version="${PIROUTER_APP_VERSION:-3.0.1}"
build_number="${PIROUTER_APP_BUILD:-4}"
build_dmg="${PIROUTER_BUILD_DMG:-true}"
dmg_output="${PIROUTER_DMG_OUTPUT:-$build_dir/PiRouter-Exporter-$version.dmg}"
identity="${PIROUTER_CODE_SIGN_IDENTITY:-}"
framework_source="${PIROUTER_PYTHON_FRAMEWORK:-}"

if [[ "$app_output" != /* || "$dmg_output" != /* || "$build_dir" != /* ]]; then
  echo "Build and output paths must be absolute." >&2
  exit 1
fi
case "$app_output" in
  /Applications/*|"$HOME/Applications/"*)
    echo "Build into a staging directory, then use the installer to update Applications." >&2
    exit 1
    ;;
esac
[[ "$build_dmg" == true || "$build_dmg" == false ]] || { echo "PIROUTER_BUILD_DMG must be true or false." >&2; exit 1; }

for dependency in xcrun lipo codesign ditto hdiutil python3; do
  command -v "$dependency" >/dev/null || { echo "Build dependency is missing: $dependency" >&2; exit 1; }
done

mkdir -p "$build_dir" "$(dirname "$app_output")" "$(dirname "$dmg_output")"
work_dir="$(mktemp -d "$build_dir/.build.XXXXXX")"
trap 'rm -rf -- "$work_dir"' EXIT
app="$work_dir/PiRouter Exporter.app"
contents="$app/Contents"
resources="$contents/Resources"
mkdir -p "$contents/MacOS" "$contents/Frameworks" "$resources"

# Explicit resources prevent pairing secrets, SSH keys, logs, and job databases
# from being copied into an installer intended for other computers.
for resource in \
  portable-export-macos.sh \
  verify-time-machine-volumes.py \
  validate-portable-snapshot.py \
  portable-export-lock.py \
  portable-export-mounts.py \
  resumable-portable-export.py \
  portable-export-receiver.php; do
  install -m 0644 "$project_dir/scripts/$resource" "$resources/$resource"
done
chmod 0755 "$resources/portable-export-macos.sh"
install -m 0644 "$project_dir/assets/PiRouterExporter.icns" "$resources/PiRouterExporter.icns"

if [[ -z "$framework_source" ]]; then
  framework_source="$work_dir/runtime/Python.framework"
  python3 "$project_dir/scripts/bundle-python-runtime-macos.py" \
    --output "$framework_source" \
    --cache-dir "${PIROUTER_RUNTIME_CACHE_DIR:-$build_dir/runtime-cache}"
fi
[[ -d "$framework_source" ]] || { echo "The private Python.framework is missing." >&2; exit 1; }
ditto "$framework_source" "$contents/Frameworks/Python.framework"
python_home="$contents/Frameworks/Python.framework/Versions/Current"
python_bin="$python_home/bin/python3"
[[ -x "$python_bin" ]] || { echo "The private Python interpreter is missing." >&2; exit 1; }
python3 - "$python_home/Resources/Python.app/Contents/Info.plist" <<'PY'
from pathlib import Path
import plistlib, sys
path = Path(sys.argv[1])
metadata = plistlib.loads(path.read_bytes())
metadata.update(LSRequiresNativeExecution=True, LSArchitecturePriority=['arm64', 'x86_64'])
path.write_bytes(plistlib.dumps(metadata))
PY
for architecture in arm64 x86_64; do lipo "$python_bin" -verify_arch "$architecture"; done

sdk="$(xcrun --show-sdk-path)"
# Both architecture slices must come from the same immutable source revision,
# even if another developer edits the launcher during a long package build.
cp "$project_dir/scripts/portable-export-launcher.swift" "$work_dir/launcher.swift"
grep -Fq -- '"--runtime-check"' "$work_dir/launcher.swift" || {
  echo "The native launcher is missing its runtime compatibility check." >&2; exit 1;
}
for architecture in arm64 x86_64; do
  xcrun swiftc -O -target "$architecture-apple-macosx13.0" -sdk "$sdk" \
    -framework AppKit -framework WebKit \
    "$work_dir/launcher.swift" \
    -o "$work_dir/launcher-$architecture"
done
lipo -create "$work_dir/launcher-arm64" "$work_dir/launcher-x86_64" \
  -output "$contents/MacOS/PiRouterExporter"
for architecture in arm64 x86_64; do lipo "$contents/MacOS/PiRouterExporter" -verify_arch "$architecture"; done

python3 - "$contents/Info.plist" "$version" "$build_number" <<'PY'
from pathlib import Path
import plistlib, sys
metadata = {
    'CFBundleDisplayName': 'PiRouter Exporter',
    'CFBundleExecutable': 'PiRouterExporter',
    'CFBundleIdentifier': 'local.pirouter.portable-export',
    'CFBundleIconFile': 'PiRouterExporter',
    'CFBundleInfoDictionaryVersion': '6.0',
    'CFBundleName': 'PiRouter Exporter',
    'CFBundlePackageType': 'APPL',
    'CFBundleShortVersionString': sys.argv[2],
    'CFBundleVersion': sys.argv[3],
    'CFBundleSupportedPlatforms': ['MacOSX'],
    'LSApplicationCategoryType': 'public.app-category.utilities',
    'LSMinimumSystemVersion': '13.0',
    'LSRequiresNativeExecution': True,
    'LSArchitecturePriority': ['arm64', 'x86_64'],
    'NSHighResolutionCapable': True,
    'NSLocalNetworkUsageDescription': 'PiRouter connects to your Pi to show its dashboard and coordinate optional backup exports.',
    'NSAppTransportSecurity': {
        'NSAllowsLocalNetworking': True,
        'NSAllowsArbitraryLoadsInWebContent': True,
    },
}
Path(sys.argv[1]).write_bytes(plistlib.dumps(metadata))
PY

if [[ -z "$identity" ]]; then
  identity="$(security find-identity -v -p codesigning 2>/dev/null | awk -F '"' '/"Developer ID Application:/{ print $2; exit }')"
  identity="${identity:--}"
fi
sign_options=(--force --sign "$identity")
if [[ "$identity" != - ]]; then sign_options+=(--timestamp --options runtime); fi

# Sign nested code before sealing its containing bundles. Apple Development
# certificates are not selected automatically for an externally shared app.
while IFS= read -r -d '' binary; do
  if file -b "$binary" | grep -q 'Mach-O'; then codesign "${sign_options[@]}" "$binary"; fi
done < <(find "$contents/Frameworks" -type f -print0)
while IFS= read -r -d '' bundle; do codesign "${sign_options[@]}" "$bundle"; done \
  < <(find "$contents/Frameworks" -depth -type d -name '*.app' -print0)
codesign "${sign_options[@]}" "$contents/Frameworks/Python.framework"
codesign "${sign_options[@]}" --identifier local.pirouter.portable-export \
  "$app"
codesign --verify --deep --strict "$app"

# Validate cached/overridden frameworks as rigorously as newly downloaded ones.
# Normalize and sign metadata first, so the interpreter is never executed with
# an invalidated nested application seal.
python3 "$project_dir/scripts/bundle-python-runtime-macos.py" \
  --validate --output "$contents/Frameworks/Python.framework"
if [[ "$(/usr/sbin/sysctl -n hw.optional.arm64 2>/dev/null || true)" == 1 ]]; then
  native_arch=arm64
else
  native_arch=x86_64
fi
PYTHONHOME="$python_home" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 "$python_bin" - "$native_arch" <<'PY'
import ctypes, errno, hashlib, lzma, platform, sqlite3, ssl, sys, zlib
assert sys.version_info >= (3, 10)
translated = ctypes.c_int(0)
size = ctypes.c_size_t(ctypes.sizeof(translated))
libc = ctypes.CDLL(None, use_errno=True)
result = libc.sysctlbyname(b'sysctl.proc_translated', ctypes.byref(translated), ctypes.byref(size), None, 0)
if result and ctypes.get_errno() != errno.ENOENT:
    raise RuntimeError('Could not verify native Python execution')
if translated.value or platform.machine() != sys.argv[1]:
    raise RuntimeError('Private Python must run natively, without Rosetta translation')
assert sqlite3.sqlite_version
print('Private Python runtime verified: native ' + platform.machine())
PY
"$contents/MacOS/PiRouterExporter" --runtime-check

cat > "$work_dir/Install Readme.txt" <<'HELP'
PiRouter Exporter
macOS 13 or later • Apple Silicon and Intel

INSTALL
1. Quit an existing PiRouter Exporter before replacing it.
2. Drag PiRouter Exporter to Applications.
3. Open it from Applications and follow the connection setup.

CONNECT ANOTHER MAC
Enter your PiRouter address in Settings. The dashboard works without export
credentials. Optional Time Machine exports need that Mac's own pairing token,
SSH destination/key, and Full Disk Access. No account details or SSH keys are
included in this installer. The app includes its own private Python runtime;
Xcode and a separate Python installation are not required.
The app and its private runtime run natively on each Mac; Rosetta is not required.

Your existing settings and resumable upload progress remain in your user
Library when replacing the application. The destination for new export setups
is the time-machine-austin share.

Python licensing information is included with the bundled runtime.
HELP
if [[ "$identity" == - ]]; then
  cat >> "$work_dir/Install Readme.txt" <<'HELP'

APPLE SECURITY
This build has a local code signature and is not notarized by Apple. On a
different Mac, macOS may ask you to approve it through System Settings >
Privacy & Security > Open Anyway after the first attempt to open it.
Do not disable Gatekeeper or other system security settings.
HELP
fi
install -m 0644 "$work_dir/Install Readme.txt" "$resources/INSTALLATION.txt"
# Adding the installation guide changes the bundle seal, so sign it once more.
codesign "${sign_options[@]}" --identifier local.pirouter.portable-export \
  "$app"
codesign --verify --deep --strict "$app"

if [[ "$build_dmg" == true ]]; then
  dmg_root="$work_dir/dmg-root"
  mkdir "$dmg_root"
  ditto "$app" "$dmg_root/PiRouter Exporter.app"
  ln -s /Applications "$dmg_root/Applications"
  cp "$work_dir/Install Readme.txt" "$dmg_root/Install Readme.txt"
  hdiutil create -quiet -fs HFS+ -volname 'PiRouter Exporter' -srcfolder "$dmg_root" \
    -format UDZO -imagekey zlib-level=9 "$work_dir/PiRouter-Exporter.dmg"
  hdiutil verify -quiet "$work_dir/PiRouter-Exporter.dmg"
  if [[ "$identity" != - ]]; then codesign --sign "$identity" --timestamp "$work_dir/PiRouter-Exporter.dmg"; fi
fi

# Publish only after compilation, signature checks, and image verification have
# all succeeded. A failed build leaves the previous distributable available.
previous_app=""
if [[ -e "$app_output" ]]; then
  previous_app="$app_output.previous-$(date +%Y%m%d-%H%M%S)-$$"
  mv "$app_output" "$previous_app"
fi
if ! mv "$app" "$app_output"; then
  [[ -z "$previous_app" ]] || mv "$previous_app" "$app_output"
  exit 1
fi
if [[ "$build_dmg" == true ]]; then mv -f "$work_dir/PiRouter-Exporter.dmg" "$dmg_output"; fi

python3 - "$build_dir/distribution-manifest.json" "$app_output" "$dmg_output" "$build_dmg" "$identity" <<'PY'
from pathlib import Path
import hashlib, json, sys
app = Path(sys.argv[2])
manifest = {
    'app': app.name,
    'architectures': ['arm64', 'x86_64'],
    'minimumMacOS': '13.0',
    'bundledPython': True,
    'requiresNativeExecution': True,
    'signing': 'ad-hoc' if sys.argv[5] == '-' else 'Developer ID',
    'notarized': False,
    'launcherSHA256': hashlib.sha256((app/'Contents/MacOS/PiRouterExporter').read_bytes()).hexdigest(),
}
if sys.argv[4] == 'true':
    dmg = Path(sys.argv[3]); manifest.update(dmg=dmg.name, dmgSHA256=hashlib.sha256(dmg.read_bytes()).hexdigest())
Path(sys.argv[1]).write_text(json.dumps(manifest, indent=2)+'\n')
PY
printf 'Built %s\n' "$app_output"
if [[ "$build_dmg" == true ]]; then printf 'Installer image: %s\n' "$dmg_output"; fi
if [[ "$identity" == - ]]; then echo 'This local-signature build is not Apple-notarized.'; fi
