from datetime import datetime, time
from io import BytesIO
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from core.forms import AssetForm, PlaylistItemForm
from core.management.commands.run_signage_worker import Command, needs_browser_rendition
from core.models import Asset, Playlist, PlaylistItem, WorkerJob
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from PIL import Image


def upload(name: str, content_type: str, content: bytes = b"test-content"):
    return SimpleUploadedFile(name, content, content_type=content_type)


def test_upload_type_is_inferred_from_mime_type():
    image_form = AssetForm(
        data={"name": "Афиша", "enabled": "on"},
        files={"file": upload("asset.bin", "image/jpeg")},
    )
    video_form = AssetForm(
        data={"name": "Ролик", "enabled": "on"},
        files={"file": upload("asset.bin", "video/mp4")},
    )

    assert image_form.is_valid(), image_form.errors
    assert video_form.is_valid(), video_form.errors
    assert image_form.save(commit=False).kind == Asset.Kind.IMAGE
    assert video_form.save(commit=False).kind == Asset.Kind.VIDEO


def test_generic_upload_type_is_inferred_from_actual_image_contents():
    buffer = BytesIO()
    Image.new("RGB", (2, 2), "blue").save(buffer, format="PNG")
    form = AssetForm(
        data={"name": "Логотип", "enabled": "on"},
        files={"file": upload("asset.bin", "application/octet-stream", buffer.getvalue())},
    )

    assert form.is_valid(), form.errors
    assert form.save(commit=False).kind == Asset.Kind.IMAGE


def test_url_is_inferred_as_live_website():
    form = AssetForm(
        data={"name": "Расписание", "source_url": "https://example.org/schedule", "enabled": "on"}
    )

    assert form.is_valid(), form.errors
    asset = form.save(commit=False)
    assert asset.kind == Asset.Kind.WEBSITE
    assert asset.website_mode == Asset.WebsiteMode.LIVE
    assert not asset.file


@pytest.mark.parametrize(
    ("data", "files", "message"),
    [
        ({"name": "Пусто"}, {}, "Загрузите фото или видео"),
        (
            {"name": "Два источника", "source_url": "https://example.org"},
            {"file": upload("clip.mp4", "video/mp4")},
            "но не оба сразу",
        ),
        (
            {"name": "Архив"},
            {"file": upload("archive.bin", "application/octet-stream")},
            "Не удалось определить тип файла",
        ),
    ],
)
def test_upload_rejects_ambiguous_or_unsupported_source(data, files, message):
    form = AssetForm(data=data, files=files)

    assert not form.is_valid()
    assert message in str(form.errors)


@pytest.mark.django_db
def test_upload_view_persists_inferred_video_type(
    django_user_model, settings, tmp_path
):
    settings.MEDIA_ROOT = tmp_path
    user = django_user_model.objects.create_user(username="operator", password="secret")
    client = Client()
    client.force_login(user)

    response = client.post(
        reverse("assets"),
        {
            "name": "Новый ролик",
            "file": upload("training.mp4", "video/mp4"),
            "enabled": "on",
        },
    )

    assert response.status_code == 302
    asset = Asset.objects.get(name="Новый ролик")
    assert asset.kind == Asset.Kind.VIDEO
    assert asset.mime_type == "video/mp4"
    assert WorkerJob.objects.filter(
        job_type=WorkerJob.JobType.PROBE_ASSET,
        payload={"assetId": str(asset.id)},
    ).exists()


@pytest.mark.django_db
def test_worker_corrects_provisional_type_from_actual_image(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    buffer = BytesIO()
    Image.new("RGB", (3, 2), "red").save(buffer, format="PNG")
    asset = Asset.objects.create(
        name="Изображение с неточным MIME",
        kind=Asset.Kind.VIDEO,
        file=upload("unknown.bin", "application/octet-stream", buffer.getvalue()),
    )

    Command().probe_asset(asset)

    asset.refresh_from_db()
    assert asset.kind == Asset.Kind.IMAGE
    assert asset.status == Asset.Status.READY
    assert asset.mime_type == "image/png"
    assert (asset.width, asset.height) == (3, 2)
    assert asset.duration_ms is None


@pytest.mark.django_db
def test_worker_corrects_video_type_and_updates_existing_playlist_duration(
    settings, tmp_path, monkeypatch
):
    settings.MEDIA_ROOT = tmp_path
    probe = {
        "format": {"duration": "12.501", "format_name": "mov,mp4"},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "pix_fmt": "yuv420p",
                "width": 1920,
                "height": 1080,
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }
    monkeypatch.setattr(
        "core.management.commands.run_signage_worker.ffprobe", lambda _path: probe
    )
    asset = Asset.objects.create(
        name="Видео с неточным MIME",
        kind=Asset.Kind.IMAGE,
        file=upload("training.mp4", "application/octet-stream", b"video-content"),
    )
    playlist = Playlist.objects.create(name="Видео")
    item = PlaylistItem.objects.create(
        playlist=playlist,
        asset=asset,
        duration_seconds=10,
    )

    Command().probe_asset(asset)

    asset.refresh_from_db()
    item.refresh_from_db()
    assert asset.kind == Asset.Kind.VIDEO
    assert asset.status == Asset.Status.READY
    assert asset.duration_ms == 12_501
    assert asset.metadata["playback"]["transcoded"] is False
    assert item.duration_seconds == 13


@pytest.mark.django_db
def test_new_video_playlist_item_uses_video_duration_and_open_schedule():
    asset = Asset.objects.create(
        name="Тренировка",
        kind=Asset.Kind.VIDEO,
        duration_ms=12_501,
        status=Asset.Status.READY,
    )
    before = timezone.now()
    form = PlaylistItemForm(
        data={
            "item_type": PlaylistItem.ItemType.ASSET,
            "asset": asset.pk,
            "enabled": "on",
            "duration_seconds": "",
            "fit_mode": PlaylistItem.FitMode.COVER,
            "volume": 100,
            "overlay_mode": PlaylistItem.OverlayMode.CHANNEL,
            "active_from": "",
            "active_until": "",
            "daily_start": "",
            "daily_end": "",
        }
    )
    assert form.is_valid(), form.errors
    after = timezone.now()
    item = form.save(commit=False)
    assert item.duration_seconds == 13
    assert before <= item.active_from <= after
    assert item.active_until is None
    assert item.weekdays == []
    assert item.daily_start is None
    assert item.daily_end is None


@pytest.mark.django_db
@pytest.mark.parametrize("kind", [Asset.Kind.IMAGE, Asset.Kind.WEBSITE])
def test_new_photo_or_website_uses_ten_seconds_and_open_schedule(kind):
    asset = Asset.objects.create(
        name="Обычный контент",
        kind=kind,
        status=Asset.Status.READY,
        source_url="https://example.org" if kind == Asset.Kind.WEBSITE else "",
    )
    form = PlaylistItemForm(
        data={
            "item_type": PlaylistItem.ItemType.SCENE,
            "asset": asset.pk,
            "enabled": "on",
            "duration_seconds": "",
            "fit_mode": PlaylistItem.FitMode.COVER,
            "volume": 100,
            "overlay_mode": PlaylistItem.OverlayMode.CHANNEL,
        }
    )

    assert form.is_valid(), form.errors
    item = form.save(commit=False)
    assert item.item_type == PlaylistItem.ItemType.ASSET
    assert item.duration_seconds == 10
    assert item.active_from is not None
    assert item.active_until is None
    assert item.weekdays == []
    assert item.daily_start is None
    assert item.daily_end is None


@pytest.mark.django_db
def test_playlist_view_persists_smart_defaults(django_user_model):
    user = django_user_model.objects.create_user(username="operator", password="secret")
    playlist = Playlist.objects.create(name="Главный")
    asset = Asset.objects.create(
        name="Тренировка",
        kind=Asset.Kind.VIDEO,
        duration_ms=12_501,
        status=Asset.Status.READY,
    )
    client = Client()
    client.force_login(user)
    before = timezone.now()

    response = client.post(
        reverse("playlist_edit", kwargs={"playlist_id": playlist.id}),
        {
            "item_type": PlaylistItem.ItemType.ASSET,
            "asset": asset.pk,
            "enabled": "on",
            "duration_seconds": "",
            "fit_mode": PlaylistItem.FitMode.COVER,
            "volume": 100,
            "overlay_mode": PlaylistItem.OverlayMode.CHANNEL,
            "active_from": "",
            "active_until": "",
            "daily_start": "",
            "daily_end": "",
        },
    )

    assert response.status_code == 302
    item = playlist.items.get()
    assert item.duration_seconds == 13
    assert item.active_from >= before
    assert item.active_until is None
    assert item.weekdays == []
    assert item.daily_start is None
    assert item.daily_end is None


@pytest.mark.django_db
def test_new_image_playlist_item_preserves_manual_duration_and_schedule():
    asset = Asset.objects.create(
        name="Афиша",
        kind=Asset.Kind.IMAGE,
        status=Asset.Status.READY,
    )
    form = PlaylistItemForm(
        data={
            "item_type": PlaylistItem.ItemType.ASSET,
            "asset": asset.pk,
            "enabled": "on",
            "duration_seconds": 23,
            "fit_mode": PlaylistItem.FitMode.CONTAIN,
            "volume": 75,
            "muted": "on",
            "overlay_mode": PlaylistItem.OverlayMode.HIDDEN,
            "active_from": "2026-09-01T08:30",
            "active_until": "2026-10-01T20:00",
            "weekdays": ["0", "2", "4"],
            "daily_start": "08:00",
            "daily_end": "20:00",
        }
    )

    assert form.is_valid(), form.errors
    item = form.save(commit=False)
    zone = ZoneInfo("Asia/Sakhalin")
    assert item.duration_seconds == 23
    assert item.active_from == datetime(2026, 9, 1, 8, 30, tzinfo=zone)
    assert item.active_until == datetime(2026, 10, 1, 20, 0, tzinfo=zone)
    assert item.weekdays == [0, 2, 4]
    assert item.daily_start == time(8, 0)
    assert item.daily_end == time(20, 0)
    assert item.volume == 75
    assert item.muted


@pytest.mark.django_db
def test_playlist_form_exposes_video_metadata_to_defaulting_ui():
    asset = Asset.objects.create(
        name="Ролик",
        kind=Asset.Kind.VIDEO,
        duration_ms=5_250,
        status=Asset.Status.READY,
    )

    form = PlaylistItemForm()
    rendered_select = str(form["asset"])
    assert f'value="{asset.pk}"' in rendered_select
    assert 'data-kind="video"' in rendered_select
    assert 'data-duration-ms="5250"' in rendered_select
    assert 'value="10"' in str(form["duration_seconds"])
    assert 'value="20' in str(form["active_from"])
    assert 'type="datetime-local"' in str(form["active_from"])


@pytest.mark.django_db
def test_new_playlist_only_offers_ready_enabled_assets():
    ready = Asset.objects.create(
        name="Готов",
        kind=Asset.Kind.IMAGE,
        status=Asset.Status.READY,
        enabled=True,
    )
    Asset.objects.create(
        name="Обрабатывается",
        kind=Asset.Kind.VIDEO,
        status=Asset.Status.PROCESSING,
        enabled=True,
    )
    Asset.objects.create(
        name="Выключен",
        kind=Asset.Kind.IMAGE,
        status=Asset.Status.READY,
        enabled=False,
    )

    assert list(PlaylistItemForm().fields["asset"].queryset) == [ready]


def test_non_mp4_container_gets_browser_rendition():
    video = {"codec_name": "h264", "pix_fmt": "yuv420p"}
    audio = {"codec_name": "aac"}

    assert not needs_browser_rendition(Path("clip.mp4"), video, audio)
    assert needs_browser_rendition(Path("clip.mkv"), video, audio)
    assert needs_browser_rendition(Path("clip.mov"), video, audio)
