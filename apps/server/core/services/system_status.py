from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
from pathlib import Path

from django.conf import settings


def _read_text(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return ""


def _positive_int(value: str) -> int | None:
    try:
        parsed = int(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _percent(used: int | float, total: int | float) -> int:
    return max(0, min(100, round(used / total * 100))) if total > 0 else 0


def _human_bytes(value: int) -> str:
    size = float(max(0, value))
    for unit in ("Б", "КиБ", "МиБ", "ГиБ", "ТиБ"):
        if size < 1024 or unit == "ТиБ":
            return f"{size:.0f} {unit}" if unit in {"Б", "КиБ"} else f"{size:.1f} {unit}"
        size /= 1024
    return "0 Б"


def _duration(seconds: float) -> str:
    total_minutes = max(0, int(seconds // 60))
    days, remaining = divmod(total_minutes, 1440)
    hours, minutes = divmod(remaining, 60)
    if days:
        return f"{days} д. {hours} ч."
    if hours:
        return f"{hours} ч. {minutes} мин."
    return f"{minutes} мин."


def _read_meminfo() -> dict[str, int]:
    values: dict[str, int] = {}
    try:
        lines = Path("/proc/meminfo").read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for line in lines:
        key, _, raw = line.partition(":")
        try:
            values[key] = int(raw.strip().split()[0]) * 1024
        except (ValueError, IndexError):
            continue
    return values


def _cpu_count() -> int | float:
    raw = _read_text("/sys/fs/cgroup/cpuset.cpus.effective")
    count = 0
    try:
        for part in raw.split(","):
            if not part:
                continue
            if "-" in part:
                start, end = (int(value) for value in part.split("-", 1))
                count += max(0, end - start + 1)
            else:
                int(part)
                count += 1
    except ValueError:
        count = 0
    count = count or os.cpu_count() or 1
    quota = _read_text("/sys/fs/cgroup/cpu.max").split()
    if len(quota) == 2:
        quota_value, period = (_positive_int(value) for value in quota)
        if quota_value and period:
            capacity = min(float(count), quota_value / period)
            return int(capacity) if capacity.is_integer() else capacity
    return count


def _memory_usage() -> tuple[int, int]:
    memory = _read_meminfo()
    total = memory.get("MemTotal", 0)
    available = memory.get("MemAvailable", memory.get("MemFree", 0))
    used = max(0, total - available)
    limit = _positive_int(_read_text("/sys/fs/cgroup/memory.max"))
    try:
        current = max(0, int(_read_text("/sys/fs/cgroup/memory.current")))
    except ValueError:
        current = None
    if limit and (not total or limit < total):
        total = limit
        if current is not None:
            # Reclaimable file cache is not application memory pressure.
            stats = dict(
                line.split(maxsplit=1)
                for line in _read_text("/sys/fs/cgroup/memory.stat").splitlines()
                if len(line.split()) == 2
            )
            inactive_file = _positive_int(stats.get("inactive_file", "")) or 0
            used = max(0, current - inactive_file)
    return min(total, used), total


def _existing_disk_path() -> Path:
    path = Path(settings.MEDIA_ROOT).resolve()
    while not path.exists() and path != path.parent:
        path = path.parent
    return path


def _service_states() -> list[dict[str, str]]:
    units = [
        "signage-web.service",
        "signage-worker.service",
        "nginx.service",
        "postgresql.service",
        "signage-backup.timer",
        "signage-maintenance.timer",
    ]
    try:
        result = subprocess.run(
            ["systemctl", "is-active", *units],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        states = result.stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        states = []
    labels = [
        "Веб-приложение",
        "Фоновая обработка",
        "Nginx",
        "PostgreSQL",
        "Резервные копии",
        "Обслуживание",
    ]
    return [
        {
            "name": label,
            "state": states[index] if index < len(states) else "unknown",
            "ok": states[index] == "active" if index < len(states) else False,
        }
        for index, label in enumerate(labels)
    ]


def collect_system_status() -> dict[str, object]:
    cpu_count = _cpu_count()
    try:
        load_1, load_5, load_15 = os.getloadavg()
    except (AttributeError, OSError):
        load_1 = load_5 = load_15 = 0.0
    load_percent = _percent(load_1, cpu_count)

    memory_used, memory_total = _memory_usage()
    memory_percent = _percent(memory_used, memory_total)

    try:
        disk = shutil.disk_usage(_existing_disk_path())
    except OSError:
        disk = None
    disk_percent = _percent(disk.used, disk.total) if disk else 0

    try:
        uptime_seconds = float(Path("/proc/uptime").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        uptime_seconds = 0

    container = ""
    try:
        container = Path("/run/systemd/container").read_text(encoding="utf-8").strip()
    except OSError:
        pass

    return {
        "hostname": socket.gethostname(),
        "platform": f"{platform.system()} {platform.release()}",
        "container": container.upper() if container else "Система",
        "cpu_count": cpu_count,
        "load": f"{load_1:.2f} / {load_5:.2f} / {load_15:.2f}",
        "load_percent": load_percent,
        "memory_used": _human_bytes(memory_used),
        "memory_total": _human_bytes(memory_total),
        "memory_percent": memory_percent,
        "disk_used": _human_bytes(disk.used) if disk else "—",
        "disk_total": _human_bytes(disk.total) if disk else "—",
        "disk_free": _human_bytes(disk.free) if disk else "—",
        "disk_available": disk is not None,
        "disk_percent": disk_percent,
        "uptime": _duration(uptime_seconds),
        "services": _service_states(),
    }
