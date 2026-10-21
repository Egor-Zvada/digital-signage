import hashlib
import json
from urllib.parse import parse_qs, urlparse

import pytest
from core.models import Asset, Channel, Playlist, PlaylistItem, Screen
from core.services.manifest import publish_channel
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import Client
from django.urls import reverse
from django.utils import timezone


@pytest.fixture
def protocol(db, settings, tmp_path):
    settings.DEBUG = True
    settings.MEDIA_ROOT = tmp_path / "media"
    settings.SIGNAGE_SITE_SNAPSHOT_ROOT = tmp_path / "snapshots"
    settings.SIGNAGE_SITE_SNAPSHOT_ROOT.mkdir()
    call_command("seed_signage", verbosity=0)
    channel = Channel.objects.get(slug="main")
    screen = Screen(name="Приёмная", channel=channel)
    token = screen.issue_token()
    screen.save()
    paths = {
        name: reverse(name, kwargs={"screen_id": screen.id, "token": token})
        for name in ("player_manifest", "player_heartbeat")
    }
    return Client(), channel, screen, paths


def add_image(channel, name="Афиша"):
    contents = b"image-contents"
    asset = Asset.objects.create(
        name=name,
        kind=Asset.Kind.IMAGE,
        status=Asset.Status.READY,
        file=SimpleUploadedFile("poster.png", contents, content_type="image/png"),
        mime_type="image/png",
        file_size=len(contents),
        sha256=hashlib.sha256(contents).hexdigest(),
    )
    item = PlaylistItem.objects.create(playlist=channel.playlist, asset=asset, position=100)
    return asset, item


def response_bytes(response):
    try:
        return b"".join(response.streaming_content)
    finally:
        response.close()


@pytest.mark.django_db
def test_manifest_pins_media_to_its_published_revision(protocol):
    client, channel, _screen, paths = protocol
    asset, _item = add_image(channel)
    revision = publish_channel(channel)

    response = client.get(paths["player_manifest"])

    assert response.status_code == 200
    published = next(
        item["asset"]
        for item in response.json()["items"]
        if item.get("asset", {}).get("id") == str(asset.id)
    )
    assert parse_qs(urlparse(published["mediaUrl"]).query)["revision"] == [str(revision.number)]


@pytest.mark.django_db
def test_previous_revision_media_survives_playlist_republication(protocol):
    client, channel, _screen, paths = protocol
    asset, item = add_image(channel)
    publish_channel(channel)
    manifest = client.get(paths["player_manifest"]).json()
    published = next(
        row["asset"] for row in manifest["items"] if row.get("asset", {}).get("id") == str(asset.id)
    )
    item.delete()
    second = publish_channel(channel)

    historical = client.get(published["mediaUrl"])
    assert historical.status_code == 200
    assert response_bytes(historical) == b"image-contents"
    current_url = f"{urlparse(published['mediaUrl']).path}?revision={second.number}"
    assert client.get(current_url).status_code == 404


@pytest.mark.django_db
def test_historical_revision_cannot_read_another_channel_asset(protocol):
    client, channel, _screen, paths = protocol
    asset, _ = add_image(channel)
    publish_channel(channel)
    published = next(
        row["asset"]
        for row in client.get(paths["player_manifest"]).json()["items"]
        if row.get("asset", {}).get("id") == str(asset.id)
    )
    other_playlist = Playlist.objects.create(name="Закрытый плейлист")
    other_channel = Channel.objects.create(
        name="Другой канал", slug="private", playlist=other_playlist
    )
    private_asset, _ = add_image(other_channel, name="Другой файл")
    publish_channel(other_channel)
    publish_channel(other_channel)
    media_path = urlparse(published["mediaUrl"]).path.replace(str(asset.id), str(private_asset.id))

    assert client.get(f"{media_path}?revision=2").status_code == 404


@pytest.mark.django_db
@pytest.mark.parametrize("revision", ["invalid", "-1", "0", "9999999999999999999999999999"])
def test_media_rejects_invalid_revision_without_server_error(protocol, revision):
    client, channel, _screen, paths = protocol
    asset, _ = add_image(channel)
    publish_channel(channel)
    published = next(
        row["asset"]
        for row in client.get(paths["player_manifest"]).json()["items"]
        if row.get("asset", {}).get("id") == str(asset.id)
    )

    response = client.get(f"{urlparse(published['mediaUrl']).path}?revision={revision}")

    assert response.status_code == 404


@pytest.mark.django_db
def test_snapshot_media_uses_published_capture_after_new_snapshot(protocol, settings):
    client, channel, _screen, paths = protocol
    root = settings.SIGNAGE_SITE_SNAPSHOT_ROOT
    (root / "first.png").write_bytes(b"first snapshot")
    (root / "second.png").write_bytes(b"second snapshot")
    asset = Asset.objects.create(
        name="Снимок сайта",
        kind=Asset.Kind.WEBSITE,
        website_mode=Asset.WebsiteMode.SNAPSHOT,
        source_url="https://example.org",
        status=Asset.Status.READY,
        metadata={
            "snapshotPath": "first.png",
            "snapshotSize": len(b"first snapshot"),
            "snapshotSha256": hashlib.sha256(b"first snapshot").hexdigest(),
        },
    )
    PlaylistItem.objects.create(playlist=channel.playlist, asset=asset, position=100)
    first = publish_channel(channel)
    first_data = client.get(paths["player_manifest"]).json()
    old_asset = next(
        item["asset"]
        for item in first_data["items"]
        if item.get("asset", {}).get("id") == str(asset.id)
    )
    asset.metadata = {
        "snapshotPath": "second.png",
        "snapshotSize": len(b"second snapshot"),
        "snapshotSha256": hashlib.sha256(b"second snapshot").hexdigest(),
    }
    asset.save(update_fields=["metadata"])
    publish_channel(channel)

    old_response = client.get(old_asset["mediaUrl"])

    assert first.manifest["items"][-1]["asset"]["mediaPath"] == "first.png"
    assert old_asset["size"] == len(b"first snapshot")
    assert old_asset["sha256"] == hashlib.sha256(b"first snapshot").hexdigest()
    assert old_response.status_code == 200
    assert response_bytes(old_response) == b"first snapshot"


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bad_path", ["../secret.png", "..\\secret.png", "/etc/passwd", "C:\\secret.png"]
)
def test_snapshot_rejects_paths_outside_snapshot_root(protocol, bad_path):
    client, channel, _screen, paths = protocol
    asset = Asset.objects.create(
        name="Снимок",
        kind=Asset.Kind.WEBSITE,
        website_mode=Asset.WebsiteMode.SNAPSHOT,
        source_url="https://example.org",
        status=Asset.Status.READY,
        metadata={"snapshotPath": bad_path},
    )
    PlaylistItem.objects.create(playlist=channel.playlist, asset=asset, position=100)
    publish_channel(channel)
    published = next(
        row["asset"]
        for row in client.get(paths["player_manifest"]).json()["items"]
        if row.get("asset", {}).get("id") == str(asset.id)
    )

    assert client.get(published["mediaUrl"]).status_code == 404


@pytest.mark.django_db
def test_weather_refresh_changes_etag_without_republishing_and_keeps_revision_immutable(protocol):
    client, channel, _screen, paths = protocol
    source = channel.weather_source
    source.current_data = {"temperature": 16}
    source.last_success_at = timezone.now()
    source.save()
    first_revision = publish_channel(channel)
    first = client.get(paths["player_manifest"])
    old_etag = first["ETag"]
    source.current_data = {"temperature": 18}
    source.last_success_at = timezone.now()
    source.save()

    refreshed = client.get(paths["player_manifest"], HTTP_IF_NONE_MATCH=old_etag)

    assert refreshed.status_code == 200
    assert refreshed["ETag"] != old_etag
    assert refreshed.json()["revision"] == first_revision.number
    assert refreshed.json()["weather"]["data"]["temperature"] == 18
    assert all(
        item["scene"]["weather"]["data"]["temperature"] == 18
        for item in refreshed.json()["items"]
        if item.get("scene")
    )
    first_revision.refresh_from_db()
    assert first_revision.manifest["weather"]["data"]["temperature"] == 16


@pytest.mark.django_db
def test_etag_changes_for_screen_settings_but_not_for_clock_ticks(protocol):
    client, channel, screen, paths = protocol
    publish_channel(channel)
    first = client.get(paths["player_manifest"])
    assert client.get(paths["player_manifest"], HTTP_IF_NONE_MATCH=first["ETag"]).status_code == 304
    screen.settings = {"playbackMode": "independent", "offlineEnabled": True}
    screen.save(update_fields=["settings"])

    changed = client.get(paths["player_manifest"], HTTP_IF_NONE_MATCH=first["ETag"])

    assert changed.status_code == 200
    assert changed.json()["screen"]["settings"] == screen.settings
    unchanged = client.get(paths["player_manifest"], HTTP_IF_NONE_MATCH=changed["ETag"])
    assert unchanged.status_code == 304
    assert "no-cache" in unchanged["Cache-Control"]


@pytest.mark.django_db
@pytest.mark.parametrize("body", [b"[]", b"null", b'"string"', b"true", b"not json", b"\xff"])
def test_heartbeat_rejects_non_object_and_invalid_json(protocol, body):
    client, _channel, screen, paths = protocol

    response = client.post(paths["player_heartbeat"], data=body, content_type="application/json")

    assert response.status_code == 400
    screen.refresh_from_db()
    assert screen.last_seen_at is None


@pytest.mark.django_db
@pytest.mark.parametrize("revision", [-1, True, "1", 10**30])
def test_heartbeat_rejects_invalid_revision_without_database_error(protocol, revision):
    client, _channel, screen, paths = protocol

    response = client.post(
        paths["player_heartbeat"],
        data=json.dumps({"revision": revision}),
        content_type="application/json",
    )

    assert response.status_code == 400
    screen.refresh_from_db()
    assert screen.last_seen_at is None


@pytest.mark.django_db
def test_heartbeat_uses_validated_proxy_address_instead_of_user_forwarded_header(protocol):
    client, _channel, screen, paths = protocol

    response = client.post(
        paths["player_heartbeat"],
        data="{}",
        content_type="application/json",
        HTTP_X_FORWARDED_FOR="not-an-ip",
        HTTP_X_REAL_IP="192.168.110.10",
    )

    assert response.status_code == 200
    screen.refresh_from_db()
    assert screen.last_ip == "192.168.110.10"


@pytest.mark.django_db
def test_session_close_beacon_marks_latest_client_offline_without_revoking_link(protocol):
    client, _channel, screen, paths = protocol
    screen.status = Screen.Status.ERROR
    screen.capabilities = {"sessionId": "current-session", "offline": {"ready": True}}
    screen.last_error = "Old playback error"
    screen.save()
    token_hash = screen.token_hash
    response = client.post(
        paths["player_heartbeat"],
        data=json.dumps({"closing": True, "sessionId": "current-session"}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json() == {"closed": True}
    screen.refresh_from_db()
    assert screen.status == Screen.Status.OFFLINE
    assert screen.capabilities["sessionClosedAt"] == screen.last_seen_at.isoformat()
    assert screen.capabilities["offline"] == {"ready": True}
    assert screen.last_error == ""
    assert screen.enabled
    assert screen.token_hash == token_hash


@pytest.mark.django_db
@pytest.mark.parametrize("session_id", ["old-session", "", None, 123])
def test_old_or_invalid_close_beacon_cannot_close_replacement_session(protocol, session_id):
    client, _channel, screen, paths = protocol
    screen.status = Screen.Status.ONLINE
    screen.capabilities = {"sessionId": "new-session"}
    screen.last_seen_at = timezone.now()
    screen.save()
    previous_seen = screen.last_seen_at

    response = client.post(
        paths["player_heartbeat"],
        data=json.dumps({"closing": True, "sessionId": session_id}),
        content_type="application/json",
    )

    assert response.status_code == 200
    assert response.json() == {"closed": False}
    screen.refresh_from_db()
    assert screen.status == Screen.Status.ONLINE
    assert screen.last_seen_at == previous_seen
    assert screen.capabilities == {"sessionId": "new-session"}


@pytest.mark.django_db
def test_reopened_client_heartbeat_removes_closed_session_marker(protocol):
    client, _channel, screen, paths = protocol
    screen.status = Screen.Status.OFFLINE
    screen.capabilities = {
        "sessionId": "closed-session",
        "sessionClosedAt": timezone.now().isoformat(),
    }
    screen.save()

    response = client.post(
        paths["player_heartbeat"],
        data=json.dumps({"capabilities": {"sessionId": "new-session", "sessionClosedAt": "stale"}}),
        content_type="application/json",
    )

    assert response.status_code == 200
    screen.refresh_from_db()
    assert screen.status == Screen.Status.ONLINE
    assert screen.capabilities == {"sessionId": "new-session"}


@pytest.mark.django_db
def test_close_beacon_cannot_win_race_with_new_session(protocol, monkeypatch):
    from core import player_auth

    client, _channel, screen, paths = protocol
    screen.status = Screen.Status.ONLINE
    screen.capabilities = {"sessionId": "old-session"}
    screen.save()
    original_get = player_auth.get_screen_or_404

    def get_then_replace_session(screen_id, token):
        stale_screen = original_get(screen_id, token)
        Screen.objects.filter(pk=screen_id).update(capabilities={"sessionId": "new-session"})
        return stale_screen

    monkeypatch.setattr(player_auth, "get_screen_or_404", get_then_replace_session)

    response = client.post(
        paths["player_heartbeat"],
        data=json.dumps({"closing": True, "sessionId": "old-session"}),
        content_type="application/json",
    )

    assert response.json() == {"closed": False}
    screen.refresh_from_db()
    assert screen.status == Screen.Status.ONLINE
    assert screen.capabilities == {"sessionId": "new-session"}
