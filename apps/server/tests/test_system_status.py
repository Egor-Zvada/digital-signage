from types import SimpleNamespace

from core.services import system_status


def test_lxc_cpu_and_memory_limits_are_used(monkeypatch):
    values = {
        "/sys/fs/cgroup/cpuset.cpus.effective": "0-7",
        "/sys/fs/cgroup/cpu.max": "50000 100000",
        "/sys/fs/cgroup/memory.max": str(2 * 1024**3),
        "/sys/fs/cgroup/memory.current": str(768 * 1024**2),
        "/sys/fs/cgroup/memory.stat": f"inactive_file {256 * 1024**2}\ninvalid-line",
    }
    monkeypatch.setattr(system_status, "_read_text", lambda path: values.get(path, ""))
    monkeypatch.setattr(system_status, "_read_meminfo", lambda: {
        "MemTotal": 64 * 1024**3, "MemAvailable": 16 * 1024**3,
    })
    assert system_status._cpu_count() == 0.5
    assert system_status._memory_usage() == (512 * 1024**2, 2 * 1024**3)


def test_unlimited_cgroup_uses_lxcfs_memory_and_cpuset(monkeypatch):
    values = {
        "/sys/fs/cgroup/cpuset.cpus.effective": "7,18,20,23",
        "/sys/fs/cgroup/cpu.max": "max 100000",
        "/sys/fs/cgroup/memory.max": "max",
    }
    monkeypatch.setattr(system_status, "_read_text", lambda path: values.get(path, ""))
    monkeypatch.setattr(system_status, "_read_meminfo", lambda: {
        "MemTotal": 4 * 1024**3, "MemAvailable": 3 * 1024**3,
    })
    assert system_status._cpu_count() == 4
    assert system_status._memory_usage() == (1024**3, 4 * 1024**3)


def test_missing_disk_and_systemctl_do_not_break_dashboard(monkeypatch, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    monkeypatch.setattr(system_status, "_read_text", lambda _path: "")
    monkeypatch.setattr(system_status, "_read_meminfo", lambda: {})
    def unavailable(*_args, **_kwargs):
        raise OSError("not available")
    monkeypatch.setattr(system_status.shutil, "disk_usage", unavailable)
    monkeypatch.setattr(system_status.subprocess, "run", unavailable)
    result = system_status.collect_system_status()
    assert result["disk_available"] is False
    assert result["disk_total"] == "—"
    assert result["disk_percent"] == 0
    assert result["memory_percent"] == 0
    assert len(result["services"]) == 6
    assert all(item["state"] == "unknown" for item in result["services"])


def test_systemctl_is_fixed_argument_list_without_shell(monkeypatch):
    captured = []
    def run(args, **kwargs):
        captured.append((args, kwargs))
        return SimpleNamespace(stdout="active\ninactive\nactive\nactive\nactive\nactive\n")
    monkeypatch.setattr(system_status.subprocess, "run", run)
    result = system_status._service_states()
    args, options = captured[0]
    assert args[:2] == ["systemctl", "is-active"]
    assert len(args) == 8
    assert not options.get("shell")
    assert options["timeout"] <= 2
    assert result[0]["ok"] is True
    assert result[1]["ok"] is False
