from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest
from core.forms import PlaylistItemForm, SceneForm
from core.management.commands.run_signage_worker import assert_public_http_url
from core.models import Asset, Channel, PlaylistItem, Scene, Screen, SloganSet
from core.services.manifest import publish_channel
from core.services.schedule import is_schedule_active
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.urls import reverse


@pytest.fixture
def seeded(db):
    call_command("seed_signage", verbosity=0)
    return Channel.objects.get(slug="main")


@pytest.mark.django_db
def test_seed_creates_brand_content(seeded):
    assert seeded.playlist.items.count() == 6
    assert SloganSet.objects.get(name="Основные фразы").slogans.count() == 9
    assert seeded.theme.short_name == "ОГАУ ДО «СШ ВВЕ»"


@pytest.mark.django_db
def test_publish_creates_immutable_revision(seeded):
    first = publish_channel(seeded)
    second = publish_channel(seeded)
    assert first.number == 1
    assert second.number == 2
    assert len(first.manifest["items"]) == 6
    assert first.manifest["schemaVersion"] == 1
    seeded.refresh_from_db()
    assert seeded.published_revision == second


@pytest.mark.django_db
def test_screen_token_is_hashed_and_manifest_requires_it(seeded):
    revision = publish_channel(seeded)
    screen = Screen(name="Тестовый экран", channel=seeded)
    token = screen.issue_token()
    screen.save()
    assert token not in screen.token_hash
    assert screen.verify_token(token)
    assert not screen.verify_token("wrong")

    client = Client()
    good = client.get(reverse("player_manifest", kwargs={"screen_id": screen.id, "token": token}))
    bad = client.get(reverse("player_manifest", kwargs={"screen_id": screen.id, "token": "wrong"}))
    assert good.status_code == 200
    assert good.json()["revision"] == revision.number
    assert bad.status_code == 404

    worker = client.get(
        reverse("player_service_worker", kwargs={"screen_id": screen.id, "token": token})
    )
    assert worker.status_code == 200
    assert worker["Content-Type"].startswith("text/javascript")
    assert worker["Service-Worker-Allowed"].endswith(f"/{token}/")


def test_overnight_schedule():
    now = datetime(2026, 8, 27, 23, 30, tzinfo=ZoneInfo("Asia/Sakhalin"))
    assert is_schedule_active(
        now=now,
        timezone_name="Asia/Sakhalin",
        active_from=None,
        active_until=None,
        weekdays=[],
        daily_start=time(22, 0),
        daily_end=time(6, 0),
    )


def test_private_website_targets_are_rejected(monkeypatch):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("192.168.1.10", 443))],
    )
    with pytest.raises(ValueError, match="локальным"):
        assert_public_http_url("https://example.test/page")


@pytest.mark.django_db
def test_content_can_be_renamed_from_simple_panel(seeded, django_user_model):
    user = django_user_model.objects.create_user(username="operator", password="secret")
    asset = Asset.objects.create(name="Старое имя", kind=Asset.Kind.IMAGE)
    client = Client()
    client.force_login(user)
    response = client.post(
        reverse("asset_edit", kwargs={"asset_id": asset.id}),
        {"name": "Новое имя", "enabled": "on"},
    )
    assert response.status_code == 302
    asset.refresh_from_db()
    assert asset.name == "Новое имя"


@pytest.mark.django_db
def test_scene_form_edits_human_readable_config(seeded):
    scene = Scene.objects.get(scene_type=Scene.SceneType.ANNOUNCEMENT)
    form = SceneForm(
        {
            "name": scene.name,
            "enabled": "on",
            "theme": scene.theme_id,
            "slogan_set": scene.slogan_set_id,
            "weather_source": scene.weather_source_id,
            "kicker": "Важно",
            "title": "Новая секция",
            "text": "Начало занятий в 18:00.",
        },
        instance=scene,
    )
    assert form.is_valid(), form.errors
    updated = form.save()
    assert updated.config["title"] == "Новая секция"
    assert updated.config["text"] == "Начало занятий в 18:00."


@pytest.mark.django_db
def test_disabled_asset_waits_for_publish_without_breaking_current_revision(
    seeded, tmp_path, settings
):
    settings.MEDIA_ROOT = tmp_path
    asset = Asset.objects.create(
        name="Афиша",
        kind=Asset.Kind.IMAGE,
        file=SimpleUploadedFile("poster.png", b"image-bytes", content_type="image/png"),
        status=Asset.Status.READY,
        mime_type="image/png",
        sha256="a" * 64,
        file_size=11,
    )
    PlaylistItem.objects.create(
        playlist=seeded.playlist,
        item_type=PlaylistItem.ItemType.ASSET,
        asset=asset,
        position=100,
        duration_seconds=10,
    )
    first = publish_channel(seeded)
    screen = Screen(name="Экран", channel=seeded)
    token = screen.issue_token()
    screen.save()
    asset.enabled = False
    asset.save(update_fields=["enabled"])

    client = Client()
    media = client.get(
        reverse(
            "player_media",
            kwargs={"screen_id": screen.id, "token": token, "asset_id": asset.id},
        )
    )
    assert media.status_code == 200
    assert any(str(asset.id) == item.get("asset", {}).get("id") for item in first.manifest["items"])

    second = publish_channel(seeded)
    assert all(str(asset.id) != item.get("asset", {}).get("id") for item in second.manifest["items"])


def test_weekday_selection_is_saved_as_numbers():
    form = PlaylistItemForm(data={"weekdays": ["0", "4"]})
    form.is_valid()
    assert form.cleaned_data["weekdays"] == [0, 4]
