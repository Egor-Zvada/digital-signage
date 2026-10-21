from datetime import timedelta
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from core.management.commands.run_signage_worker import Command, transcode_for_browser
from core.models import Asset, BrandTheme, Channel, Scene, SloganSet, WeatherSource, WorkerJob
from core.services.weather import update_weather
from django.core.management import call_command
from django.utils import timezone
from PIL import Image

WORKER_MODULE = "core.management.commands.run_signage_worker"


def test_reprocessing_video_preserves_previous_published_rendition(settings, tmp_path, monkeypatch):
    settings.MEDIA_ROOT = tmp_path
    asset = Asset(name="Ролик", kind=Asset.Kind.VIDEO)
    source = tmp_path / "source.mov"
    source.write_bytes(b"original")
    captures = []

    def transcode(command, **_kwargs):
        captures.append(command)
        Path(command[-1]).write_bytes(f"rendition-{len(captures)}".encode())

    monkeypatch.setattr(f"{WORKER_MODULE}.subprocess.run", transcode)
    monkeypatch.setattr(
        f"{WORKER_MODULE}.ffprobe",
        lambda _path: {
            "streams": [
                {"codec_type": "video", "codec_name": "h264", "pix_fmt": "yuv420p"},
                {"codec_type": "audio", "codec_name": "aac"},
            ]
        },
    )

    first, _ = transcode_for_browser(asset, source)
    second, _ = transcode_for_browser(asset, source)

    assert first != second
    assert first.read_bytes() == b"rendition-1"
    assert second.read_bytes() == b"rendition-2"
    assert source.read_bytes() == b"original"


@pytest.mark.django_db
def test_worker_restart_recovers_jobs_from_dead_local_process(monkeypatch):
    monkeypatch.setattr(f"{WORKER_MODULE}.socket.gethostname", lambda: "signage")

    def missing_process(_pid, _signal):
        raise ProcessLookupError

    monkeypatch.setattr(f"{WORKER_MODULE}.os.kill", missing_process)
    job = WorkerJob.objects.create(
        job_type=WorkerJob.JobType.PROBE_ASSET,
        status=WorkerJob.Status.RUNNING,
        locked_by="signage:321:old-worker",
        locked_at=timezone.now(),
        attempts=1,
    )

    assert Command.recover_interrupted_jobs() == 1

    job.refresh_from_db()
    assert job.status == WorkerJob.Status.QUEUED
    assert job.run_after <= timezone.now()
    assert job.locked_at is None
    assert job.locked_by == ""
    assert job.attempts == 1


@pytest.mark.django_db
@pytest.mark.parametrize("protected_process", [False, True])
def test_worker_restart_preserves_jobs_of_live_processes(monkeypatch, protected_process):
    monkeypatch.setattr(f"{WORKER_MODULE}.socket.gethostname", lambda: "signage")

    def live_process(_pid, _signal):
        if protected_process:
            raise PermissionError

    monkeypatch.setattr(f"{WORKER_MODULE}.os.kill", live_process)
    job = WorkerJob.objects.create(
        job_type=WorkerJob.JobType.WEATHER,
        status=WorkerJob.Status.RUNNING,
        locked_by="signage:321:maintenance",
        locked_at=timezone.now(),
        attempts=1,
    )

    assert Command.recover_interrupted_jobs() == 0
    job.refresh_from_db()
    assert job.status == WorkerJob.Status.RUNNING


@pytest.mark.django_db
def test_interrupted_job_retries_remain_bounded(monkeypatch):
    asset = Asset.objects.create(name="Повреждённое видео", kind=Asset.Kind.VIDEO)
    job = WorkerJob.objects.create(
        job_type=WorkerJob.JobType.PROBE_ASSET,
        payload={"assetId": str(asset.id)},
        status=WorkerJob.Status.RUNNING,
        locked_by="old-host:321:worker",
        locked_at=timezone.now() - timedelta(hours=6),
        attempts=3,
    )

    assert Command.recover_interrupted_jobs() == 1

    job.refresh_from_db()
    asset.refresh_from_db()
    assert job.status == WorkerJob.Status.FAILED
    assert job.finished_at is not None
    assert asset.status == Asset.Status.FAILED
    assert asset.error_message


@pytest.mark.django_db
def test_completed_job_releases_lock():
    job = WorkerJob.objects.create(
        job_type=WorkerJob.JobType.CLEANUP,
        status=WorkerJob.Status.RUNNING,
        locked_by="signage:321:worker",
        locked_at=timezone.now(),
        attempts=1,
    )

    Command().run_job(job)

    job.refresh_from_db()
    assert job.status == WorkerJob.Status.DONE
    assert job.locked_at is None
    assert job.locked_by == ""


@pytest.mark.django_db
def test_failed_snapshot_cannot_reuse_old_image(settings, tmp_path, monkeypatch):
    settings.SIGNAGE_SITE_SNAPSHOT_ROOT = tmp_path
    monkeypatch.setattr(f"{WORKER_MODULE}.assert_public_http_url", lambda _url: None)
    monkeypatch.setattr(f"{WORKER_MODULE}.subprocess.run", lambda *_args, **_kwargs: None)
    asset = Asset.objects.create(
        name="Сайт", kind=Asset.Kind.WEBSITE, source_url="https://example.org"
    )
    old_snapshot = tmp_path / f"{asset.id}.png"
    old_snapshot.write_bytes(b"old-snapshot" * 1000)

    with pytest.raises(ValueError, match="не создал снимок"):
        Command().snapshot(asset)

    assert old_snapshot.read_bytes() == b"old-snapshot" * 1000
    assert list(tmp_path.iterdir()) == [old_snapshot]
    asset.refresh_from_db()
    assert asset.status != Asset.Status.READY


@pytest.mark.django_db
def test_snapshot_uses_private_profile_and_preserves_published_images(
    settings, tmp_path, monkeypatch
):
    settings.SIGNAGE_SITE_SNAPSHOT_ROOT = tmp_path
    monkeypatch.setattr(f"{WORKER_MODULE}.assert_public_http_url", lambda _url: None)
    asset = Asset.objects.create(
        name="Сайт", kind=Asset.Kind.WEBSITE, source_url="https://example.org"
    )
    previous = tmp_path / f"{asset.id}.png"
    previous.write_bytes(b"previous snapshot")
    profiles = []

    def capture(command, **_kwargs):
        profile = Path(
            next(x.split("=", 1)[1] for x in command if x.startswith("--user-data-dir="))
        )
        screenshot = Path(
            next(x.split("=", 1)[1] for x in command if x.startswith("--screenshot="))
        )
        assert profile.is_dir()
        assert Path(_kwargs["env"]["XDG_CONFIG_HOME"]).parent == profile
        assert Path(_kwargs["env"]["XDG_CACHE_HOME"]).parent == profile
        assert screenshot != previous
        assert previous.read_bytes() == b"previous snapshot"
        profiles.append(profile)
        Image.new("RGB", (1920, 1080), "blue").save(screenshot)

    monkeypatch.setattr(f"{WORKER_MODULE}.subprocess.run", capture)

    Command().snapshot(asset)

    asset.refresh_from_db()
    output = tmp_path / asset.metadata["snapshotPath"]
    assert asset.status == Asset.Status.READY
    assert asset.metadata["snapshotPath"] == output.name
    assert output != previous
    assert asset.metadata["snapshotSha256"] == asset.sha256
    assert asset.metadata["snapshotSize"] == output.stat().st_size
    assert not profiles[0].exists()
    assert set(tmp_path.iterdir()) == {output, previous}
    assert previous.read_bytes() == b"previous snapshot"
    with Image.open(output) as image:
        assert image.size == (1920, 1080)


@pytest.mark.django_db
def test_deploy_seed_does_not_restore_deleted_widgets_playlist_items_or_phrases():
    call_command("seed_signage", verbosity=0)
    channel = Channel.objects.get(slug="main")
    scene = Scene.objects.get(scene_type=Scene.SceneType.ANNOUNCEMENT)
    channel.playlist.items.filter(scene=scene).delete()
    scene.delete()
    phrases = SloganSet.objects.get(name="Основные фразы")
    phrase = phrases.slogans.first()
    phrase.delete()
    playlist_count = channel.playlist.items.count()
    phrase_count = phrases.slogans.count()

    call_command("seed_signage", verbosity=0)

    assert channel.playlist.items.count() == playlist_count
    assert phrases.slogans.count() == phrase_count
    assert not Scene.objects.filter(scene_type=Scene.SceneType.ANNOUNCEMENT).exists()


@pytest.mark.django_db
def test_deploy_seed_preserves_custom_default_theme():
    call_command("seed_signage", verbosity=0)
    BrandTheme.objects.update(is_default=False)
    custom = BrandTheme.objects.create(
        name="Своя тема", short_name="Школа", full_name="Своя школа", is_default=True
    )

    call_command("seed_signage", verbosity=0)

    custom.refresh_from_db()
    assert custom.is_default


@pytest.mark.django_db
@pytest.mark.parametrize("payload", [{}, {"current": {}}, {"current": None}])
def test_incomplete_weather_response_does_not_destroy_previous_forecast(monkeypatch, payload):
    last_success = timezone.now() - timedelta(hours=1)
    source = WeatherSource.objects.create(
        name="Южно-Сахалинск",
        latitude="46.95910",
        longitude="142.73800",
        current_data={"temperature": 16, "condition": "Облачно"},
        last_success_at=last_success,
    )
    response = httpx.Response(
        200, json=payload, request=httpx.Request("GET", "https://api.open-meteo.com")
    )
    client = MagicMock()
    client.__enter__.return_value.get.return_value = response
    monkeypatch.setattr("core.services.weather.httpx.Client", lambda **_kwargs: client)

    with pytest.raises(ValueError, match="неполные данные"):
        update_weather(source)

    source.refresh_from_db()
    assert source.current_data == {"temperature": 16, "condition": "Облачно"}
    assert source.last_success_at == last_success
    assert source.last_attempt_at is not None
    assert source.last_error


@pytest.mark.django_db
def test_zero_temperature_and_clear_weather_are_valid(monkeypatch):
    source = WeatherSource.objects.create(
        name="Южно-Сахалинск", latitude="46.95910", longitude="142.73800"
    )
    response = httpx.Response(
        200,
        json={"current": {"time": "2026-10-21T11:00", "temperature_2m": 0, "weather_code": 0}},
        request=httpx.Request("GET", "https://api.open-meteo.com"),
    )
    client = MagicMock()
    client.__enter__.return_value.get.return_value = response
    monkeypatch.setattr("core.services.weather.httpx.Client", lambda **_kwargs: client)

    data = update_weather(source)

    source.refresh_from_db()
    assert data["temperature"] == 0
    assert data["condition"] == "Ясно"
    assert source.last_success_at is not None
    assert source.last_error == ""
