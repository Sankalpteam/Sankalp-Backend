"""Read-only Linux memory audit for the ONE existing AWS backend host.

No secrets, environment variables, private files or credentials are reported.
This script does not deploy, restart, connect to AWS or change any settings.

On the existing host, run:
    .venv/bin/python -m scripts.ops.memory_report --service indoone-backend

It reports system available RAM, production service RSS and cgroup usage (when
available). Human review must include the database and all other processes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess


def _read_int(path: Path) -> int | None:
    try:
        value = path.read_text(encoding="utf-8").strip()
        return int(value) if value.isdecimal() else None
    except (OSError, ValueError):
        return None


def _meminfo(path: Path) -> dict[str, int]:
    result: dict[str, int] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return result
    for row in lines:
        key, delimiter, rest = row.partition(":")
        if not delimiter or key not in {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}:
            continue
        fields = rest.split()
        if not fields or not fields[0].isdecimal():
            continue
        result[key] = int(fields[0]) * 1024  # /proc/meminfo reports kB
    return result


def _process_rss(status_path: Path) -> int | None:
    try:
        lines = status_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        if line.startswith("VmRSS:"):
            bits = line.split()
            if len(bits) >= 2 and bits[1].isdecimal():
                return int(bits[1]) * 1024
    return None


def service_pid(service: str) -> int:
    """Request the MainPID only; never dump unit env or service credentials."""
    if service != "indoone-backend":
        raise ValueError("Only the already deployed indoone-backend unit is supported")
    result = subprocess.run(
        ["systemctl", "show", "--property=MainPID", "--value", service],
        check=True, capture_output=True, text=True, timeout=5,
    )
    value = result.stdout.strip()
    if not value.isdecimal() or int(value) < 1:
        raise RuntimeError("service has no running MainPID")
    return int(value)


def snapshot(
    pid: int,
    *,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
) -> dict[str, int | None]:
    """Snapshot memory usage in bytes; none indicates unavailable metrics."""
    if pid <= 0:
        raise ValueError("pid must be a positive integer")
    mem = _meminfo(proc_root / "meminfo")
    return {
        "host_total_bytes": mem.get("MemTotal"),
        "host_available_bytes": mem.get("MemAvailable"),
        "swap_total_bytes": mem.get("SwapTotal"),
        "swap_free_bytes": mem.get("SwapFree"),
        "backend_process_rss_bytes": _process_rss(proc_root / str(pid) / "status"),
        "cgroup_current_bytes": _read_int(cgroup_root / "memory.current"),
        "cgroup_limit_bytes": _read_int(cgroup_root / "memory.max"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--service", choices=("indoone-backend",), default="indoone-backend")
    args = parser.parse_args()
    report = snapshot(service_pid(args.service))
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
