"""Read-only memory audit and opt-in single-host release safeguards."""
from pathlib import Path

import pytest

from scripts.ops.memory_report import snapshot, service_pid
from scripts.ops.run_server import server_settings


def test_memory_snapshot_reads_only_system_metrics(tmp_path: Path):
    (tmp_path / "proc" / "123").mkdir(parents=True)
    (tmp_path / "cgroup").mkdir()
    (tmp_path / "proc" / "meminfo").write_text(
        "MemTotal: 2000000 kB\nMemAvailable: 850000 kB\nSwapTotal: 0 kB\nSwapFree: 0 kB\n"
    )
    (tmp_path / "proc" / "123" / "status").write_text("Name: python\nVmRSS:\t144400 kB\n")
    (tmp_path / "cgroup" / "memory.current").write_text("55000000\n")
    (tmp_path / "cgroup" / "memory.max").write_text("max\n")
    actual = snapshot(123, proc_root=tmp_path / "proc", cgroup_root=tmp_path / "cgroup")
    assert actual["host_total_bytes"] == 2_000_000 * 1024
    assert actual["host_available_bytes"] == 850_000 * 1024
    assert actual["backend_process_rss_bytes"] == 144_400 * 1024
    assert actual["cgroup_current_bytes"] == 55_000_000
    assert actual["cgroup_limit_bytes"] is None


def test_memory_audit_reports_unknown_without_guessing(tmp_path: Path):
    actual = snapshot(123, proc_root=tmp_path / "missing", cgroup_root=tmp_path / "missing")
    assert all(value is None for value in actual.values())


def test_memory_audit_does_not_accept_other_units():
    with pytest.raises(ValueError):
        service_pid("unrelated-server")


def test_2gb_budget_sets_single_worker_and_reduces_default_concurrency(monkeypatch):
    monkeypatch.setenv("INDOONE_SINGLE_HOST_RAM_MB", "2048")
    monkeypatch.delenv("INDOONE_SERVER_WORKERS", raising=False)
    monkeypatch.delenv("INDOONE_SERVER_LIMIT_CONCURRENCY", raising=False)
    settings = server_settings()
    assert settings["workers"] == 1
    assert settings["limit_concurrency"] == 32


@pytest.mark.parametrize(("workers", "concurrency"), [("2", "32"), ("1", "200")])
def test_2gb_budget_rejects_unsafe_workers_and_concurrency(monkeypatch, workers, concurrency):
    monkeypatch.setenv("INDOONE_SINGLE_HOST_RAM_MB", "2048")
    monkeypatch.setenv("INDOONE_SERVER_WORKERS", workers)
    monkeypatch.setenv("INDOONE_SERVER_LIMIT_CONCURRENCY", concurrency)
    with pytest.raises(RuntimeError, match="single 2 GiB AWS host"):
        server_settings()
