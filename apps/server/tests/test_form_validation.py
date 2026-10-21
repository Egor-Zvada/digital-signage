from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from core.forms import (
    AssetEditForm,
    ChannelForm,
    PlaylistItemForm,
    ScreenForm,
    SloganForm,
    UserUpdateForm,
)
from core.models import Asset, Channel, Playlist, Slogan
from core.permissions import Role, set_user_role


@pytest.fixture
def channel(db):
    playlist = Playlist.objects.create(name="Проверка форм")
    return Channel.objects.create(name="Проверка форм", slug="forms", playlist=playlist)


@pytest.mark.django_db
def test_channel_rejects_unknown_timezone(channel):
    payload = {
        "name": channel.name, "slug": channel.slug, "playlist": channel.playlist_id,
        "enabled": "on", "default_volume": 100,
    }
    invalid = ChannelForm({**payload, "timezone_name": "Asia/DoesNotExist"}, instance=channel)
    assert not invalid.is_valid()
    assert "timezone_name" in invalid.errors
    valid = ChannelForm({**payload, "timezone_name": "Asia/Sakhalin"}, instance=channel)
    assert valid.is_valid(), valid.errors


@pytest.mark.django_db
def test_playlist_and_slogan_reject_inverted_date_range():
    asset = Asset.objects.create(name="Афиша", kind=Asset.Kind.IMAGE, status=Asset.Status.READY)
    schedule = {"active_from": "2026-10-22T09:00", "active_until": "2026-10-21T18:00"}
    item = PlaylistItemForm({
        "asset": asset.pk, "fit_mode": "cover", "volume": 100,
        "overlay_mode": "channel", **schedule,
    })
    assert not item.is_valid()
    assert "active_until" in item.errors
    slogan = SloganForm({"text": "Начало в 09:00", **schedule})
    assert not slogan.is_valid()
    assert "active_until" in slogan.errors


def test_slogan_schedule_renders_browser_datetime_value():
    date = datetime(2026, 10, 21, 9, 30, tzinfo=ZoneInfo("Asia/Sakhalin"))
    form = SloganForm(instance=Slogan(active_from=date, active_until=date))
    assert 'value="2026-10-21T09:30"' in str(form["active_from"])
    assert 'value="2026-10-21T09:30"' in str(form["active_until"])


@pytest.mark.django_db
def test_website_edit_requires_http_and_defaults_live_mode():
    asset = Asset.objects.create(name="Сайт", kind=Asset.Kind.WEBSITE, source_url="https://example.org")
    ftp = AssetEditForm({"name": "Сайт", "source_url": "ftp://example.org"}, instance=asset)
    assert not ftp.is_valid()
    assert "source_url" in ftp.errors
    http = AssetEditForm({"name": "Сайт", "source_url": "https://example.org"}, instance=asset)
    assert http.is_valid(), http.errors
    assert http.save().website_mode == Asset.WebsiteMode.LIVE


@pytest.mark.django_db
def test_editing_disabled_admin_does_not_block_when_an_active_admin_exists(django_user_model):
    active = django_user_model.objects.create_user(username="active-admin")
    set_user_role(active, Role.ADMIN)
    inactive = django_user_model.objects.create_user(username="inactive-admin", is_active=False)
    set_user_role(inactive, Role.ADMIN)
    form = UserUpdateForm(
        {"username": inactive.username, "role": Role.USER}, instance=inactive, acting_user=active,
    )
    assert form.is_valid(), form.errors


@pytest.mark.django_db
def test_screen_interval_settings_preserve_other_options(channel):
    form = ScreenForm({
        "name": "Камера / объявления", "enabled": "on", "channel": channel.pk,
        "display_mode": "interval", "display_interval_minutes": "120",
        "display_duration_minutes": "15", "display_daily_start": "08:00",
        "display_daily_end": "20:00", "display_weekdays": ["0", "2", "4"],
    })
    assert form.is_valid(), form.errors
    screen = form.save()
    assert screen.settings["offlineEnabled"] is False
    assert screen.settings["displaySchedule"] == {
        "mode": "interval", "intervalMinutes": 120, "durationMinutes": 15,
        "dailyStart": "08:00", "dailyEnd": "20:00", "weekdays": [0, 2, 4], "windows": [],
    }
    screen.settings["otherOption"] = "retained"
    screen.save()
    edit = ScreenForm({"name": screen.name, "channel": channel.pk}, instance=screen)
    assert edit.is_valid(), edit.errors
    assert edit.save().settings["otherOption"] == "retained"


@pytest.mark.django_db
@pytest.mark.parametrize("windows", ["", "25:00-26:00", "09:00-09:00", "09:00-09:20\n09:10-09:30"])
def test_screen_windows_reject_invalid_or_overlapping_ranges(channel, windows):
    form = ScreenForm({
        "name": "Показ", "channel": channel.pk, "display_mode": "windows",
        "display_windows": windows,
    })
    assert not form.is_valid()
    assert "display_windows" in form.errors


@pytest.mark.django_db
def test_screen_windows_support_overnight_and_populate_editor(channel):
    form = ScreenForm({
        "name": "Показ", "channel": channel.pk, "display_mode": "windows",
        "display_windows": "23:55-00:10\n09:00-09:15", "offline_enabled": "on",
    })
    assert form.is_valid(), form.errors
    screen = form.save()
    assert screen.settings["offlineEnabled"] is True
    assert screen.settings["displaySchedule"]["windows"] == [
        {"start": "23:55", "end": "00:10"}, {"start": "09:00", "end": "09:15"},
    ]
    edit = ScreenForm(instance=screen)
    assert edit.initial["display_mode"] == "windows"
    assert edit.initial["display_windows"] == "23:55-00:10\n09:00-09:15"


@pytest.mark.django_db
def test_screen_rejects_duration_exceeding_launch_interval(channel):
    form = ScreenForm({
        "name": "Показ", "channel": channel.pk, "display_mode": "interval",
        "display_interval_minutes": "60", "display_duration_minutes": "90",
    })
    assert not form.is_valid()
    assert "display_duration_minutes" in form.errors
