#!/usr/bin/env python3
"""Bundle the pinned PSF universal2 runtime without installing it on the Mac."""

import argparse
import hashlib
import os
from pathlib import Path
import plistlib
import re
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile


VERSION = "3.14.10"
PACKAGE = f"python-{VERSION}-macos11.pkg"
URL = f"https://www.python.org/ftp/python/{VERSION}/{PACKAGE}"
SHA256 = "286c7018aa2730f6747ca433dccdc2b31be45f078a9ea0ffa44d5f06368fd2d7"
PREFIX = "/Library/Frameworks/Python.framework/"
MAGIC = {b"\xfe\xed\xfa\xce", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca"}
NATIVE_ARCHITECTURES = ["arm64", "x86_64"]


def run(*arguments):
    result = subprocess.run(arguments, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(f"{arguments[0]} failed: {result.stderr.strip() or result.stdout.strip()}")
    return result.stdout


def macho_files(root):
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink():
            with path.open("rb") as stream:
                if stream.read(4) in MAGIC:
                    yield path


def relative_reference(binary, framework, original):
    target = framework / original[len(PREFIX):]
    if not target.exists():
        raise RuntimeError(f"Missing bundled dependency: {original}")
    return "@loader_path/" + os.path.relpath(target, binary.parent)


def normalize_native_execution(framework):
    """Keep the Python child native on both kinds of supported Mac hardware."""
    info = framework / "Versions/Current/Resources/Python.app/Contents/Info.plist"
    metadata = plistlib.loads(info.read_bytes())
    metadata["LSRequiresNativeExecution"] = True
    metadata["LSArchitecturePriority"] = NATIVE_ARCHITECTURES
    info.write_bytes(plistlib.dumps(metadata))


def validate_framework(framework):
    """Reject Intel-only children and machine-local runtime dependencies."""
    root = framework.resolve()
    for link in framework.rglob("*"):
        if link.is_symlink():
            try:
                target = link.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise RuntimeError(f"Broken runtime symlink: {link}") from error
            if not target.is_relative_to(root):
                raise RuntimeError(f"Runtime symlink points outside its framework: {link}")
    version = framework / "Versions/Current"
    for required in (version / "Python", version / "bin/python3", version / "Resources/Python.app/Contents/MacOS/Python"):
        if not required.is_file():
            raise RuntimeError(f"Missing required runtime binary: {required}")
        if not os.access(required, os.X_OK):
            raise RuntimeError(f"Required runtime binary is not executable: {required}")
        with required.open("rb") as stream:
            if stream.read(4) not in MAGIC:
                raise RuntimeError(f"Required runtime binary is not Mach-O: {required}")
    info = framework / "Versions/Current/Resources/Python.app/Contents/Info.plist"
    metadata = plistlib.loads(info.read_bytes())
    if metadata.get("LSRequiresNativeExecution") is not True or metadata.get("LSArchitecturePriority") != NATIVE_ARCHITECTURES:
        raise RuntimeError("The bundled Python app must require native execution with arm64 first")
    binaries = list(macho_files(framework))
    if not binaries:
        raise RuntimeError("No Mach-O runtime binaries were found")
    for binary in binaries:
        architectures = set(run("/usr/bin/lipo", "-archs", str(binary)).split())
        if architectures != set(NATIVE_ARCHITECTURES):
            raise RuntimeError(f"Runtime is not universal2: {binary}")
        dependencies = set(re.findall(r"^\s+(.+?) \(compatibility version", run("/usr/bin/otool", "-L", str(binary)), re.M))
        for dependency in dependencies:
            if dependency.startswith("/") and not dependency.startswith(("/usr/lib/", "/System/Library/")):
                raise RuntimeError(f"External runtime dependency in {binary}: {dependency}")
        load_commands = run("/usr/bin/otool", "-l", str(binary))
        for rpath in re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset", load_commands):
            if rpath.startswith("/") and not rpath.startswith(("/usr/lib/", "/System/Library/")):
                raise RuntimeError(f"External runtime search path in {binary}: {rpath}")
    return len(binaries)


def relocate(framework):
    binaries = list(macho_files(framework))
    for binary in binaries:
        architectures = set(run("/usr/bin/lipo", "-archs", str(binary)).split())
        if architectures != {"arm64", "x86_64"}:
            raise RuntimeError(f"Runtime is not universal2: {binary}")
        dependencies = set(re.findall(r"^\s+(.+?) \(compatibility version", run("/usr/bin/otool", "-L", str(binary)), re.M))
        changes = []
        # A dylib's own install name appears in -L too. Updating both its ID and
        # all references makes the framework independent of /Library/Frameworks.
        ids = run("/usr/bin/otool", "-D", str(binary)).splitlines()
        install_ids = [line.strip() for line in ids if line.strip().startswith(PREFIX)]
        if install_ids:
            changes += ["-id", "@rpath/" + str(binary.relative_to(framework.parent))]
        for dependency in sorted(dependencies):
            if dependency.startswith(PREFIX):
                changes += ["-change", dependency, relative_reference(binary, framework, dependency)]
            elif dependency.startswith("/") and not dependency.startswith(("/usr/lib/", "/System/Library/")):
                raise RuntimeError(f"External runtime dependency in {binary}: {dependency}")
        load_commands = run("/usr/bin/otool", "-l", str(binary))
        for rpath in set(re.findall(r"cmd LC_RPATH\s+cmdsize \d+\s+path (.*?) \(offset", load_commands)):
            if rpath.startswith(PREFIX):
                changes += ["-rpath", rpath, relative_reference(binary, framework, rpath)]
        if changes:
            run("/usr/bin/install_name_tool", *changes, str(binary))
        run("/usr/bin/codesign", "--force", "--sign", "-", str(binary))
    run("/usr/bin/codesign", "--force", "--deep", "--sign", "-", str(framework))
    run("/usr/bin/codesign", "--verify", "--deep", "--strict", str(framework))
    return len(binaries)


def bundle(output, cache):
    cache.mkdir(parents=True, exist_ok=True)
    package = cache / PACKAGE
    if not package.exists() or hashlib.sha256(package.read_bytes()).hexdigest() != SHA256:
        descriptor, temporary_name = tempfile.mkstemp(prefix=PACKAGE + ".", suffix=".download", dir=cache)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with urllib.request.urlopen(URL, timeout=60) as response, temporary.open("wb") as destination:
                shutil.copyfileobj(response, destination)
            if hashlib.sha256(temporary.read_bytes()).hexdigest() != SHA256:
                raise RuntimeError("The Python package did not match its pinned SHA-256 digest")
            temporary.replace(package)
        finally:
            temporary.unlink(missing_ok=True)
    signature = run("/usr/sbin/pkgutil", "--check-signature", str(package))
    if "Developer ID Installer: Python Software Foundation (BMM5U3QVKW)" not in signature:
        raise RuntimeError("The Python package is not signed by the expected Python Software Foundation identity")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise RuntimeError(f"Output already exists: {output}")
    with tempfile.TemporaryDirectory(prefix="pirouter-python-") as temporary:
        expanded = Path(temporary) / "expanded"
        run("/usr/sbin/pkgutil", "--expand-full", str(package), str(expanded))
        source = expanded / "Python_Framework.pkg" / "Payload"
        stage = Path(temporary) / "Python.framework"
        shutil.copytree(source, stage, symlinks=True)
        version = stage / "Versions" / "3.14"
        wheels = list((version / "lib/python3.14/ensurepip/_bundled").glob("pip-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError("Could not locate the bundled certificate authority package")
        with zipfile.ZipFile(wheels[0]) as wheel:
            ca = wheel.read("pip/_vendor/certifi/cacert.pem")
            license_text = wheel.read("pip/_vendor/certifi/LICENSE")
        (version / "etc").mkdir(exist_ok=True)
        (version / "etc/pirouter-ca.pem").write_bytes(ca)
        (version / "etc/pirouter-ca-LICENSE").write_bytes(license_text)
        # This is a headless export runtime. The native interface uses AppKit;
        # Tk, developer examples and CPython's tests are not application assets.
        for unused in (version / "share", version / "lib/python3.14/test", version / "Frameworks", version / "lib/python3.14/tkinter", version / "lib/python3.14/idlelib"):
            shutil.rmtree(unused)
        for unused in (version / "lib/python3.14/lib-dynload").glob("_tkinter.*"):
            unused.unlink()
        for unused in (version / "bin").glob("*intel64*"):
            unused.unlink()
        normalize_native_execution(stage)
        count = relocate(stage)
        validate_framework(stage)
        shutil.copytree(stage, output, symlinks=True)
    print(f"Bundled Python {VERSION}: {count} universal2 binaries at {output}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--cache-dir", type=Path, default=Path.home() / "Library/Caches/PiRouter/build")
    parser.add_argument("--validate", action="store_true", help="Validate an existing bundled framework without changing it")
    arguments = parser.parse_args()
    if arguments.validate:
        count = validate_framework(arguments.output.absolute())
        print(f"Validated {count} universal2 runtime binaries with native execution required")
    else:
        bundle(arguments.output.absolute(), arguments.cache_dir.expanduser().absolute())


if __name__ == "__main__":
    main()
