from datetime import datetime, time
from zoneinfo import ZoneInfo

import pytest
from core.management.commands.run_signage_worker import assert_public_http_url
from core.models import Channel, Screen, SloganSet
from core.services.manifest import publish_channel
from core.services.schedule import is_schedule_active
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
