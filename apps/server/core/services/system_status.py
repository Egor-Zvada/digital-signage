from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
from pathlib import Path

from django.conf import settings


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


def _cpu_count() -> int:
    try:
        raw = Path("/sys/fs/cgroup/cpuset.cpus.effective").read_text().strip()
    except OSError:
        raw = ""
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
    return count or os.cpu_count() or 1


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
    load_percent = min(100, round(load_1 / cpu_count * 100))

    memory = _read_meminfo()
    memory_total = memory.get("MemTotal", 0)
    memory_available = memory.get("MemAvailable", memory.get("MemFree", 0))
    memory_used = max(0, memory_total - memory_available)
    memory_percent = round(memory_used / memory_total * 100) if memory_total else 0

    disk = shutil.disk_usage(_existing_disk_path())
    disk_percent = round(disk.used / disk.total * 100) if disk.total else 0

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
        "disk_used": _human_bytes(disk.used),
        "disk_total": _human_bytes(disk.total),
        "disk_free": _human_bytes(disk.free),
        "disk_percent": disk_percent,
        "uptime": _duration(uptime_seconds),
        "services": _service_states(),
    }
