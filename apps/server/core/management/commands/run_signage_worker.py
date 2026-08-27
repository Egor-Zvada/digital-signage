from __future__ import annotations

import hashlib
import ipaddress
import json
import mimetypes
import os
import socket
import subprocess
import time
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from PIL import Image

from core.models import Asset, WeatherSource, WorkerJob
from core.services.weather import update_weather


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def assert_public_http_url(raw_url: str) -> None:
    parsed = urlparse(raw_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Разрешены только полные HTTP/HTTPS адреса")
    if parsed.username or parsed.password:
        raise ValueError("Адреса со встроенными логинами и паролями запрещены")
    try:
        addresses = {item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)}
    except socket.gaierror as exc:
        raise ValueError("Не удалось разрешить имя сайта") from exc
    for raw_address in addresses:
        address = ipaddress.ip_address(raw_address)
        if not address.is_global:
            raise ValueError("Доступ к локальным и служебным адресам запрещён")


class Command(BaseCommand):
    help = "Обрабатывает файлы, снимки сайтов и обновления погоды"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")
        parser.add_argument("--poll-seconds", type=float, default=2.0)

    def handle(self, *args, **options):
        worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self.stdout.write(f"signage worker {worker_id}")
        while True:
            self.enqueue_due_weather()
            job = self.claim_job(worker_id)
            if job:
                self.run_job(job)
            elif options["once"]:
                break
            else:
                time.sleep(options["poll_seconds"])

    @staticmethod
    def claim_job(worker_id: str) -> WorkerJob | None:
        with transaction.atomic():
            job = (
                WorkerJob.objects.select_for_update(skip_locked=True)
                .filter(status=WorkerJob.Status.QUEUED, run_after__lte=timezone.now())
                .order_by("run_after", "id")
                .first()
            )
            if not job:
                return None
            job.status = WorkerJob.Status.RUNNING
            job.locked_at = timezone.now()
            job.locked_by = worker_id
            job.attempts += 1
            job.save(update_fields=["status", "locked_at", "locked_by", "attempts"])
            return job

    def run_job(self, job: WorkerJob) -> None:
        try:
            if job.job_type == WorkerJob.JobType.PROBE_ASSET:
                self.probe_asset(Asset.objects.get(pk=job.payload["assetId"]))
            elif job.job_type == WorkerJob.JobType.SNAPSHOT:
                self.snapshot(Asset.objects.get(pk=job.payload["assetId"]))
            elif job.job_type == WorkerJob.JobType.WEATHER:
                update_weather(WeatherSource.objects.get(pk=job.payload["sourceId"]))
            elif job.job_type == WorkerJob.JobType.CLEANUP:
                self.cleanup()
            else:
                raise ValueError(f"Неподдерживаемое задание: {job.job_type}")
        except Exception as exc:
            job.error = str(exc)[:4000]
            job.locked_at = None
            job.locked_by = ""
            if job.attempts < 3:
                job.status = WorkerJob.Status.QUEUED
                job.run_after = timezone.now() + timedelta(minutes=2**job.attempts)
            else:
                job.status = WorkerJob.Status.FAILED
                job.finished_at = timezone.now()
                asset_id = job.payload.get("assetId")
                if asset_id:
                    Asset.objects.filter(pk=asset_id).update(
                        status=Asset.Status.FAILED, error_message=job.error
                    )
            job.save(
                update_fields=[
                    "error",
                    "locked_at",
                    "locked_by",
                    "status",
                    "run_after",
                    "finished_at",
                ]
            )
            self.stderr.write(f"job {job.id}: {exc}")
            return
        job.status = WorkerJob.Status.DONE
        job.finished_at = timezone.now()
        job.error = ""
        job.save(update_fields=["status", "finished_at", "error"])

    def probe_asset(self, asset: Asset) -> None:
        if asset.kind == Asset.Kind.WEBSITE:
            assert_public_http_url(asset.source_url)
            if asset.website_mode == Asset.WebsiteMode.SNAPSHOT:
                WorkerJob.objects.create(
                    job_type=WorkerJob.JobType.SNAPSHOT, payload={"assetId": str(asset.id)}
                )
            else:
                asset.status = Asset.Status.READY
                asset.error_message = ""
                asset.save(update_fields=["status", "error_message", "updated_at"])
            return

        if not asset.file:
            raise ValueError("Файл не найден")
        path = Path(asset.file.path)
        asset.file_size = path.stat().st_size
        asset.sha256 = sha256_file(path)
        if asset.kind == Asset.Kind.IMAGE:
            with Image.open(path) as image:
                image.verify()
            with Image.open(path) as image:
                asset.width, asset.height = image.size
                asset.mime_type = (
                    Image.MIME.get(image.format, mimetypes.guess_type(path.name)[0]) or "image/jpeg"
                )
        elif asset.kind == Asset.Kind.VIDEO:
            command = [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ]
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=120, check=True
            )
            probe = json.loads(completed.stdout)
            video = next(
                (
                    stream
                    for stream in probe.get("streams", [])
                    if stream.get("codec_type") == "video"
                ),
                None,
            )
            if not video:
                raise ValueError("В файле не найден видеопоток")
            asset.width = int(video.get("width") or 0) or None
            asset.height = int(video.get("height") or 0) or None
            duration = video.get("duration") or probe.get("format", {}).get("duration")
            asset.duration_ms = int(float(duration) * 1000) if duration else None
            asset.mime_type = mimetypes.guess_type(path.name)[0] or "video/mp4"
            asset.metadata = {"probe": {"format": probe.get("format", {}), "video": video}}
        asset.status = Asset.Status.READY
        asset.error_message = ""
        asset.save(
            update_fields=[
                "file_size",
                "sha256",
                "width",
                "height",
                "duration_ms",
                "mime_type",
                "metadata",
                "status",
                "error_message",
                "updated_at",
            ]
        )

    def snapshot(self, asset: Asset) -> None:
        assert_public_http_url(asset.source_url)
        output_dir = settings.SIGNAGE_SITE_SNAPSHOT_ROOT
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"{asset.id}.png"
        output = output_dir / filename
        command = [
            "chromium",
            "--headless=new",
            "--no-sandbox",
            "--disable-gpu",
            "--hide-scrollbars",
            "--disable-dev-shm-usage",
            "--window-size=1920,1080",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=10000",
            f"--screenshot={output}",
            asset.source_url,
        ]
        subprocess.run(command, capture_output=True, text=True, timeout=45, check=True)
        if not output.is_file() or output.stat().st_size < 1000:
            raise ValueError("Chromium не создал снимок страницы")
        metadata = dict(asset.metadata)
        metadata["snapshotPath"] = filename
        metadata["snapshotAt"] = timezone.now().isoformat()
        asset.metadata = metadata
        asset.mime_type = "image/png"
        asset.file_size = output.stat().st_size
        asset.sha256 = sha256_file(output)
        asset.width = 1920
        asset.height = 1080
        asset.status = Asset.Status.READY
        asset.error_message = ""
        asset.save(
            update_fields=[
                "metadata",
                "mime_type",
                "file_size",
                "sha256",
                "width",
                "height",
                "status",
                "error_message",
                "updated_at",
            ]
        )

    @staticmethod
    def enqueue_due_weather() -> None:
        now = timezone.now()
        for source in WeatherSource.objects.filter(enabled=True):
            due = not source.last_attempt_at or source.last_attempt_at <= now - timedelta(
                minutes=source.update_interval_minutes
            )
            pending = WorkerJob.objects.filter(
                job_type=WorkerJob.JobType.WEATHER,
                status__in=[WorkerJob.Status.QUEUED, WorkerJob.Status.RUNNING],
                payload__sourceId=source.id,
            ).exists()
            if due and not pending:
                WorkerJob.objects.create(
                    job_type=WorkerJob.JobType.WEATHER, payload={"sourceId": source.id}
                )

    @staticmethod
    def cleanup() -> None:
        threshold = timezone.now() - timedelta(days=7)
        WorkerJob.objects.filter(status=WorkerJob.Status.DONE, finished_at__lt=threshold).delete()
