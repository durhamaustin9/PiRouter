#!/usr/bin/python3
"""Read and hash every regular file in selected Time Machine volume trees."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import selectors
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, TextIO, Tuple


READ_SIZE = 4 * 1024 * 1024
PROGRESS_INTERVAL_SECONDS = 10.0
MOUNT_TIMEOUT_SECONDS = 10 * 60


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def json_line(handle: TextIO, value: Dict[str, Any]) -> bytes:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    handle.write(encoded.decode("utf-8"))
    handle.write("\n")
    return encoded + b"\n"


def time_machine_running() -> bool:
    result = subprocess.run(
        ["/usr/bin/tmutil", "status"],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return "Running = 1" in result.stdout


def mount_latest_snapshot() -> Tuple[Path, subprocess.Popen[str]]:
    process = subprocess.Popen(
        ["/usr/bin/tmutil", "latestbackup", "-m"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    deadline = time.monotonic() + MOUNT_TIMEOUT_SECONDS
    candidate = ""

    while time.monotonic() < deadline:
        remaining = max(0.0, deadline - time.monotonic())
        events = selector.select(timeout=min(5.0, remaining))
        if events:
            line = process.stdout.readline()
            if line:
                candidate = line.strip()
                if candidate:
                    break
        if process.poll() is not None:
            break

    selector.close()
    if not candidate:
        if process.poll() is None:
            process.terminate()
        try:
            _, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            _, stderr = process.communicate()
        message = stderr.strip() or "Time Machine did not return a snapshot path"
        raise RuntimeError(message)

    snapshot = Path(candidate)
    os.chdir(snapshot)
    try:
        process.wait(timeout=60)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=15)
    if process.returncode not in (0, None):
        assert process.stderr is not None
        message = process.stderr.read().strip()
        raise RuntimeError(message or f"tmutil exited with status {process.returncode}")
    return snapshot, process


def sorted_entries(directory: Path) -> Iterable[os.DirEntry[str]]:
    with os.scandir(directory) as iterator:
        entries = list(iterator)
    entries.sort(key=lambda entry: os.fsencode(entry.name))
    return entries


def verify_volume(
    snapshot: Path,
    volume_name: str,
    run_directory: Path,
    shared_progress: Dict[str, Any],
    progress_path: Path,
) -> Dict[str, Any]:
    root = snapshot / volume_name
    if not root.is_dir():
        raise RuntimeError(f"Backup volume is missing: {root}")

    manifest_path = run_directory / f"{volume_name}.sha256.jsonl"
    errors_path = run_directory / f"{volume_name}.errors.jsonl"
    counts: Dict[str, int] = {
        "directories": 0,
        "regularFiles": 0,
        "symlinks": 0,
        "specialFiles": 0,
        "bytesRead": 0,
        "errors": 0,
    }
    manifest_digest = hashlib.sha256()
    started = time.monotonic()
    last_progress = 0.0
    stack: List[Tuple[Path, str]] = [(root, "")]

    with manifest_path.open("w", encoding="utf-8", buffering=1024 * 1024) as manifest, \
         errors_path.open("w", encoding="utf-8", buffering=1) as errors:
        while stack:
            directory, relative_directory = stack.pop()
            counts["directories"] += 1
            try:
                entries = sorted_entries(directory)
            except OSError as error:
                counts["errors"] += 1
                json_line(errors, {
                    "operation": "scan-directory",
                    "path": relative_directory or ".",
                    "error": str(error),
                })
                continue

            child_directories: List[Tuple[Path, str]] = []
            for entry in entries:
                relative_path = (
                    f"{relative_directory}/{entry.name}"
                    if relative_directory
                    else entry.name
                )
                try:
                    metadata = entry.stat(follow_symlinks=False)
                    mode = metadata.st_mode
                    if stat.S_ISDIR(mode):
                        child_directories.append((Path(entry.path), relative_path))
                    elif stat.S_ISREG(mode):
                        digest = hashlib.sha256()
                        bytes_read = 0
                        with open(entry.path, "rb", buffering=0) as source:
                            while True:
                                chunk = source.read(READ_SIZE)
                                if not chunk:
                                    break
                                digest.update(chunk)
                                bytes_read += len(chunk)
                        if bytes_read != metadata.st_size:
                            raise OSError(
                                f"short read: expected {metadata.st_size}, read {bytes_read}"
                            )
                        record = {
                            "path": relative_path,
                            "size": metadata.st_size,
                            "mtimeNs": metadata.st_mtime_ns,
                            "sha256": digest.hexdigest(),
                        }
                        manifest_digest.update(json_line(manifest, record))
                        counts["regularFiles"] += 1
                        counts["bytesRead"] += bytes_read
                    elif stat.S_ISLNK(mode):
                        target = os.readlink(entry.path)
                        record = {"path": relative_path, "symlink": target}
                        manifest_digest.update(json_line(manifest, record))
                        counts["symlinks"] += 1
                    else:
                        record = {"path": relative_path, "mode": mode, "special": True}
                        manifest_digest.update(json_line(manifest, record))
                        counts["specialFiles"] += 1
                except OSError as error:
                    counts["errors"] += 1
                    json_line(errors, {
                        "operation": "read-entry",
                        "path": relative_path,
                        "error": str(error),
                    })

                now = time.monotonic()
                if now - last_progress >= PROGRESS_INTERVAL_SECONDS:
                    shared_progress.update({
                        "state": "running",
                        "updatedAt": utc_now(),
                        "currentVolume": volume_name,
                        "currentPath": relative_path,
                        "volumeCounts": dict(counts),
                        "elapsedSeconds": round(now - started, 1),
                    })
                    atomic_json(progress_path, shared_progress)
                    print(
                        f"VERIFY_PROGRESS volume={volume_name} "
                        f"files={counts['regularFiles']} "
                        f"bytes={counts['bytesRead']} errors={counts['errors']}",
                        flush=True,
                    )
                    last_progress = now

            for child in reversed(child_directories):
                stack.append(child)

        manifest.flush()
        errors.flush()
        os.fsync(manifest.fileno())
        os.fsync(errors.fileno())

    elapsed = time.monotonic() - started
    return {
        "volume": volume_name,
        **counts,
        "elapsedSeconds": round(elapsed, 3),
        "manifestSha256": manifest_digest.hexdigest(),
        "manifestPath": str(manifest_path),
        "errorsPath": str(errors_path),
        "passed": counts["errors"] == 0,
    }


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--snapshot-path",
        help="Use an already-mounted snapshot path (intended for verifier testing)",
    )
    parser.add_argument(
        "--report-root",
        default=str(
            Path.home()
            / "Library"
            / "Application Support"
            / "PiRouter"
            / "restore-verification"
        ),
    )
    parser.add_argument("volumes", nargs="+", help="Top-level backup volume names")
    return parser.parse_args()


def main() -> int:
    arguments = parse_arguments()
    report_root = Path(arguments.report_root)
    run_id = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + f"-{uuid.uuid4().hex[:8]}"
    run_directory = report_root / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    progress_path = report_root / "progress.json"
    report_path = run_directory / "report.json"
    progress: Dict[str, Any] = {
        "runId": run_id,
        "state": "mounting",
        "startedAt": utc_now(),
        "updatedAt": utc_now(),
        "requestedVolumes": arguments.volumes,
        "reportPath": str(report_path),
    }
    atomic_json(progress_path, progress)
    snapshot: Optional[Path] = None
    mounted_process: Optional[subprocess.Popen[str]] = None

    try:
        if arguments.snapshot_path:
            snapshot = Path(arguments.snapshot_path).resolve()
            os.chdir(snapshot)
        else:
            if time_machine_running():
                raise RuntimeError("Time Machine is currently running")
            print("VERIFY_MOUNT starting", flush=True)
            snapshot, mounted_process = mount_latest_snapshot()
            print(f"VERIFY_MOUNT ready snapshot={snapshot.name}", flush=True)

        progress.update({
            "state": "running",
            "snapshotPath": str(snapshot),
            "snapshotName": snapshot.name,
            "updatedAt": utc_now(),
        })
        atomic_json(progress_path, progress)

        volume_results = []
        for volume_name in arguments.volumes:
            print(f"VERIFY_VOLUME starting volume={volume_name}", flush=True)
            result = verify_volume(
                snapshot, volume_name, run_directory, progress, progress_path
            )
            volume_results.append(result)
            print(
                f"VERIFY_VOLUME finished volume={volume_name} "
                f"files={result['regularFiles']} bytes={result['bytesRead']} "
                f"errors={result['errors']}",
                flush=True,
            )

        report = {
            "runId": run_id,
            "startedAt": progress["startedAt"],
            "finishedAt": utc_now(),
            "snapshotPath": str(snapshot),
            "snapshotName": snapshot.name,
            "volumes": volume_results,
            "passed": all(result["passed"] for result in volume_results),
        }
        atomic_json(report_path, report)
        progress.update({
            "state": "passed" if report["passed"] else "failed",
            "updatedAt": utc_now(),
            "finishedAt": report["finishedAt"],
            "report": report,
        })
        atomic_json(progress_path, progress)
        print(f"VERIFY_RESULT passed={str(report['passed']).lower()}", flush=True)
        return 0 if report["passed"] else 2
    except BaseException as error:
        progress.update({
            "state": "error",
            "updatedAt": utc_now(),
            "finishedAt": utc_now(),
            "error": str(error),
        })
        atomic_json(progress_path, progress)
        print(f"VERIFY_ERROR {error}", file=sys.stderr, flush=True)
        return 1
    finally:
        os.chdir("/")
        if mounted_process is not None and mounted_process.poll() is None:
            mounted_process.terminate()


if __name__ == "__main__":
    raise SystemExit(main())
