from __future__ import annotations

import pytest
from core.models import Channel, Screen
from core.permissions import Role, set_user_role
from django.core.management import call_command
from django.test import Client, override_settings
from django.urls import reverse


@pytest.fixture
def display(db):
    call_command("seed_signage", verbosity=0)
    screen = Screen(name="Дисплей", channel=Channel.objects.get(slug="main"))
    legacy_token = screen.issue_token()
    screen.save()
    return screen, legacy_token


@pytest.fixture
def moderator_client(display, django_user_model):
    user = django_user_model.objects.create_user(username="link_moderator")
    set_user_role(user, Role.MODERATOR)
    client = Client()
    client.force_login(user)
    return client


def test_copyable_link_is_stable_and_keeps_legacy_access(display):
    screen, legacy_token = display
    link_token = screen.get_link_token()
    assert link_token.startswith("link_")
    assert len(link_token) == 69
    assert screen.verify_token(link_token)
    assert screen.verify_token(legacy_token)
    screen.name = "Переименованный дисплей"
    screen.save(update_fields=["name"])
    screen.refresh_from_db()
    assert screen.get_link_token() == link_token
    assert link_token not in screen.settings.values()
    assert legacy_token not in screen.settings.values()


def test_link_token_is_bound_to_screen_and_revoked_with_legacy_token(display):
    screen, legacy_token = display
    old_link = screen.get_link_token()
    other = Screen(name="Другой экран", channel=screen.channel, token_hash=screen.token_hash)
    assert not other.verify_token(old_link)
    replacement = screen.issue_token()
    assert not screen.verify_token(old_link)
    assert not screen.verify_token(legacy_token)
    assert screen.verify_token(replacement)
    assert screen.verify_token(screen.get_link_token())


def test_link_token_supports_secret_rotation_fallback(display):
    screen, _ = display
    with override_settings(SECRET_KEY="old-key", SECRET_KEY_FALLBACKS=[]):
        old_link = screen.get_link_token()
    with override_settings(SECRET_KEY="new-key", SECRET_KEY_FALLBACKS=["old-key"]):
        assert screen.verify_token(old_link)
        assert screen.verify_token(screen.get_link_token())
    with override_settings(SECRET_KEY="new-key", SECRET_KEY_FALLBACKS=[]):
        assert not screen.verify_token(old_link)


@pytest.mark.parametrize("value", ["", "link_", "link_" + "g" * 64, "link_" + "a" * 65])
def test_malformed_link_tokens_are_rejected(display, value):
    screen, _ = display
    assert not screen.verify_token(value)


def test_screen_list_and_settings_always_show_copyable_url(display, moderator_client):
    screen, legacy_token = display
    expected_path = reverse(
        "player", kwargs={"screen_id": screen.pk, "token": screen.get_link_token()}
    )
    for url in [reverse("screens"), reverse("screens"), reverse("screen_edit", args=[screen.pk])]:
        response = moderator_client.get(url)
        assert response.status_code == 200
        html = response.content.decode()
        assert expected_path in html
        assert "Копировать ссылку" in html
        assert "Открыть экран" in html
        assert legacy_token not in html
        assert response["Cache-Control"] == "private, no-store"
        assert response["Referrer-Policy"] == "no-referrer"
    screen.refresh_from_db()
    assert screen.verify_token(legacy_token)


def test_user_cannot_read_display_links(display, django_user_model):
    screen, _ = display
    user = django_user_model.objects.create_user(username="link_viewer")
    set_user_role(user, Role.USER)
    client = Client()
    client.force_login(user)
    for path in [reverse("screens"), reverse("screen_edit", args=[screen.pk])]:
        assert client.get(path).status_code == 403
    assert screen.get_link_token() not in client.get(reverse("dashboard")).content.decode()


def test_derived_and_legacy_player_urls_both_work(display):
    screen, legacy_token = display
    client = Client()
    for token in [legacy_token, screen.get_link_token()]:
        assert client.get(
            reverse("player", kwargs={"screen_id": screen.pk, "token": token})
        ).status_code == 200


def test_disabled_channel_rejects_both_player_links(display):
    screen, legacy_token = display
    screen.channel.enabled = False
    screen.channel.save(update_fields=["enabled"])
    client = Client()
    for token in [legacy_token, screen.get_link_token()]:
        for route in ["player", "player_manifest", "player_service_worker"]:
            assert client.get(
                reverse(route, kwargs={"screen_id": screen.pk, "token": token})
            ).status_code == 404


def test_offline_readiness_is_separate_from_network_state_and_escaped(display, moderator_client):
    screen, _ = display
    screen.settings = {"offlineEnabled": True}
    screen.capabilities = {
        "offline": {"state": "ready", "completed": 3, "total": 3, "bytes": 1024, "revision": 7}
    }
    screen.save()
    html = moderator_client.get(reverse("screens")).content.decode()
    assert "Готов к работе без сети" in html
    assert "Файлы: 3 / 3" in html
    assert "Сохранена ревизия 7" in html
    screen.capabilities = {"offline": {"state": "error", "error": "<script>alert(1)</script>"}}
    screen.save()
    html = moderator_client.get(reverse("screens")).content.decode()
    assert "Кеш не готов" in html
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html
