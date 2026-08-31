from __future__ import annotations

import re
from datetime import timedelta

import pytest
from core.forms import PlaylistItemForm, UserUpdateForm
from core.management.commands.seed_signage import SCENES
from core.models import AuditEvent, Channel, PlaylistItem, Scene, Screen
from core.permissions import Role, get_user_role, set_user_role
from core.services.manifest import build_manifest
from core.wiki_content import WIKI_ARTICLES
from django.contrib import admin
from django.core.management import call_command
from django.test import Client, RequestFactory
from django.urls import reverse
from django.utils import timezone


@pytest.fixture
def seeded_channel(db):
    call_command("seed_signage", verbosity=0)
    return Channel.objects.get(slug="main")


@pytest.fixture
def role_users(seeded_channel, django_user_model):
    users = {}
    for role in (Role.USER, Role.MODERATOR, Role.ADMIN):
        user = django_user_model.objects.create_user(
            username=f"test_{role}",
            password="Initial-Pass_2026!",
        )
        set_user_role(user, role)
        users[role] = user
    return users


ROLE_ACCESS = {
    Role.USER: {
        "allowed": {"dashboard", "assets", "playlists", "wiki"},
        "forbidden": {"channels", "screens", "widgets", "slogans", "users"},
    },
    Role.MODERATOR: {
        "allowed": {
            "dashboard",
            "assets",
            "playlists",
            "channels",
            "screens",
            "widgets",
            "slogans",
            "wiki",
        },
        "forbidden": {"users"},
    },
    Role.ADMIN: {
        "allowed": {
            "dashboard",
            "assets",
            "playlists",
            "channels",
            "screens",
            "widgets",
            "slogans",
            "wiki",
            "users",
        },
        "forbidden": set(),
    },
}


@pytest.mark.django_db
@pytest.mark.parametrize("role", [Role.USER, Role.MODERATOR, Role.ADMIN])
def test_role_matrix_and_sidebar_navigation(role, role_users):
    client = Client()
    client.force_login(role_users[role])

    for url_name in ROLE_ACCESS[role]["allowed"]:
        assert client.get(reverse(url_name)).status_code == 200, (role, url_name)
    for url_name in ROLE_ACCESS[role]["forbidden"]:
        assert client.get(reverse(url_name)).status_code == 403, (role, url_name)

    dashboard = client.get(reverse("dashboard"))
    html = dashboard.content.decode()
    navigation = html.split('<nav aria-label="Основная навигация">', maxsplit=1)[1].split(
        "</nav>", maxsplit=1
    )[0]
    labels = set(re.findall(r">\s*([^<>]+?)\s*</a>", navigation))
    expected = {"Обзор", "Контент", "Плейлисты", "Вики"}
    if role in (Role.MODERATOR, Role.ADMIN):
        expected.update({"Каналы", "Экраны", "Виджеты", "Фразы"})
    if role == Role.ADMIN:
        expected.add("Администрирование")
    assert labels == expected


def announcement_payload(**overrides):
    payload = {
        "name": "Второе объявление",
        "scene_type": Scene.SceneType.ANNOUNCEMENT,
        "enabled": "on",
        "kicker": "Важно",
        "title": "Тренировка переносится",
        "text": "Начало в 19:00.",
        "subtitle": "31 августа",
        "title_scale": "50",
        "text_scale": "160",
        "subtitle_scale": "105",
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_second_announcement_is_created_and_available_to_playlist_and_manifest(
    seeded_channel, role_users
):
    initial_count = Scene.objects.filter(scene_type=Scene.SceneType.ANNOUNCEMENT).count()
    assert initial_count == sum(
        scene_type == Scene.SceneType.ANNOUNCEMENT for _, scene_type, _, _ in SCENES
    )

    client = Client()
    client.force_login(role_users[Role.MODERATOR])
    response = client.post(reverse("widget_create"), announcement_payload())
    assert response.status_code == 302
    assert response.url == reverse("widgets")

    announcement = Scene.objects.get(name="Второе объявление")
    assert Scene.objects.filter(scene_type=Scene.SceneType.ANNOUNCEMENT).count() == initial_count + 1
    assert announcement.config == {
        "kicker": "Важно",
        "title": "Тренировка переносится",
        "text": "Начало в 19:00.",
        "subtitle": "31 августа",
        "titleScale": 50,
        "textScale": 160,
        "subtitleScale": 105,
    }

    form = PlaylistItemForm()
    assert announcement in form.fields["scene"].queryset

    item = PlaylistItem.objects.create(
        playlist=seeded_channel.playlist,
        item_type=PlaylistItem.ItemType.SCENE,
        scene=announcement,
        position=999,
        duration_seconds=17,
    )
    manifest = build_manifest(seeded_channel, revision_number=42)
    entry = next(row for row in manifest["items"] if row["key"] == f"playlist-item-{item.id}")
    assert entry["durationMs"] == 17_000
    assert entry["scene"]["id"] == announcement.id
    assert entry["scene"]["type"] == Scene.SceneType.ANNOUNCEMENT
    assert entry["scene"]["config"]["titleScale"] == 50
    assert entry["scene"]["config"]["textScale"] == 160


@pytest.mark.django_db
def test_empty_announcement_is_rejected_by_widget_create(seeded_channel, role_users):
    client = Client()
    client.force_login(role_users[Role.MODERATOR])
    response = client.post(
        reverse("widget_create"),
        announcement_payload(
            name="Пустое объявление",
            kicker="",
            title="",
            text="",
            subtitle="",
        ),
    )

    assert response.status_code == 200
    assert "Для объявления заполните заголовок или основной текст" in response.content.decode()
    assert not Scene.objects.filter(name="Пустое объявление").exists()


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("title_scale", "49"),
        ("title_scale", "161"),
        ("text_scale", "49"),
        ("text_scale", "161"),
        ("subtitle_scale", "49"),
        ("subtitle_scale", "161"),
    ],
)
def test_widget_text_scales_outside_50_to_160_are_rejected(
    field_name, value, seeded_channel, role_users
):
    client = Client()
    client.force_login(role_users[Role.MODERATOR])
    name = f"Некорректный масштаб {field_name} {value}"
    response = client.post(
        reverse("widget_create"),
        announcement_payload(name=name, **{field_name: value}),
    )

    assert response.status_code == 200
    assert not Scene.objects.filter(name=name).exists()
    assert response.context["form"].errors[field_name]


@pytest.mark.django_db
def test_wiki_index_articles_search_and_unknown_article(role_users):
    client = Client()
    client.force_login(role_users[Role.USER])

    index = client.get(reverse("wiki"))
    assert index.status_code == 200
    assert len(index.context["articles"]) == len(WIKI_ARTICLES)
    for article in WIKI_ARTICLES:
        assert reverse("wiki_article", kwargs={"slug": article["slug"]}) in index.content.decode()

    article = client.get(reverse("wiki_article", kwargs={"slug": "widgets"}))
    assert article.status_code == 200
    assert article.context["article"]["slug"] == "widgets"
    assert "Виджеты и объявления" in article.content.decode()

    search = client.get(reverse("wiki"), {"q": "объявлен"})
    assert search.status_code == 200
    assert any(row["slug"] == "widgets" for row in search.context["articles"])

    missing = client.get(reverse("wiki_article", kwargs={"slug": "missing-article"}))
    assert missing.status_code == 404


@pytest.mark.django_db
def test_admin_can_create_user_and_change_role_and_password(role_users, django_user_model):
    client = Client()
    client.force_login(role_users[Role.ADMIN])

    create_response = client.post(
        reverse("users"),
        {
            "username": "new_operator",
            "first_name": "Новый",
            "last_name": "Оператор",
            "email": "operator@example.test",
            "is_active": "on",
            "role": Role.MODERATOR,
            "password1": "Create-Strong_Pass-2026!",
            "password2": "Create-Strong_Pass-2026!",
        },
    )
    assert create_response.status_code == 302
    created = django_user_model.objects.get(username="new_operator")
    assert get_user_role(created) == Role.MODERATOR
    assert created.check_password("Create-Strong_Pass-2026!")

    update_response = client.post(
        reverse("user_edit", kwargs={"user_id": created.id}),
        {
            "username": "new_operator",
            "first_name": "Обновлённый",
            "last_name": "Оператор",
            "email": "updated@example.test",
            "is_active": "on",
            "role": Role.USER,
            "password1": "Updated-Strong_Pass-2026!",
            "password2": "Updated-Strong_Pass-2026!",
        },
    )
    assert update_response.status_code == 302
    created.refresh_from_db()
    assert created.first_name == "Обновлённый"
    assert created.email == "updated@example.test"
    assert get_user_role(created) == Role.USER
    assert created.check_password("Updated-Strong_Pass-2026!")


@pytest.mark.django_db
def test_moderator_cannot_create_or_edit_users(role_users):
    client = Client()
    client.force_login(role_users[Role.MODERATOR])

    create_response = client.post(
        reverse("users"),
        {
            "username": "forbidden_account",
            "role": Role.USER,
            "password1": "Create-Strong_Pass-2026!",
            "password2": "Create-Strong_Pass-2026!",
        },
    )
    edit_response = client.post(
        reverse("user_edit", kwargs={"user_id": role_users[Role.USER].id}),
        {
            "username": role_users[Role.USER].username,
            "is_active": "on",
            "role": Role.ADMIN,
        },
    )

    assert create_response.status_code == 403
    assert edit_response.status_code == 403
    assert get_user_role(role_users[Role.USER]) == Role.USER


@pytest.mark.django_db
def test_django_admin_is_superuser_only_and_role_assignment_revokes_staff(
    role_users, django_user_model
):
    staff_admin = role_users[Role.ADMIN]
    staff_admin.is_staff = True
    staff_admin.save(update_fields=["is_staff"])
    request = RequestFactory().get("/django-admin/")
    request.user = staff_admin
    assert admin.site.has_permission(request) is False

    set_user_role(staff_admin, Role.USER)
    staff_admin.refresh_from_db()
    assert staff_admin.is_staff is False

    superuser = django_user_model.objects.create_superuser(
        username="technical_superuser",
        password="Technical-Superuser-2026!",
    )
    request.user = superuser
    assert admin.site.has_permission(request) is True


@pytest.mark.django_db
def test_password_similarity_uses_new_username(role_users):
    target = role_users[Role.USER]
    form = UserUpdateForm(
        data={
            "username": "similarity-target-alpha",
            "first_name": "",
            "last_name": "",
            "email": "",
            "is_active": "on",
            "role": Role.USER,
            "password1": "similarity-target-alpha",
            "password2": "similarity-target-alpha",
        },
        instance=target,
        acting_user=role_users[Role.ADMIN],
    )

    assert not form.is_valid()
    assert form.errors["password1"]


@pytest.mark.django_db
def test_creator_footer_is_in_admin_panel_but_not_player(seeded_channel, role_users):
    client = Client()
    client.force_login(role_users[Role.USER])
    admin_page = client.get(reverse("dashboard"))
    assert admin_page.status_code == 200
    assert 'class="admin-footer"' in admin_page.content.decode()
    assert "egor-zvada.ru" in admin_page.content.decode()

    screen = Screen(name="Экран без административного футера", channel=seeded_channel)
    token = screen.issue_token()
    screen.save()
    player_page = client.get(
        reverse("player", kwargs={"screen_id": screen.id, "token": token})
    )
    player_html = player_page.content.decode()
    assert player_page.status_code == 200
    assert "admin-footer" not in player_html
    assert "egor-zvada.ru" not in player_html


SYSTEM_STATUS = {
    "load_percent": 4,
    "cpu_count": 2,
    "load": "0.04 / 0.03 / 0.02",
    "memory_percent": 25,
    "memory_used": "512 МБ",
    "memory_total": "2 ГБ",
    "disk_percent": 50,
    "disk_used": "10 ГБ",
    "disk_total": "20 ГБ",
    "disk_free": "10 ГБ",
    "container": "LXC",
    "hostname": "signage",
    "uptime": "1 день",
    "platform": "Debian GNU/Linux 13",
    "services": [],
}


@pytest.mark.django_db
def test_dashboard_limits_recent_events_and_shows_system_only_to_moderator_or_admin(
    monkeypatch, role_users
):
    now = timezone.now()
    events = []
    for index in range(7):
        event = AuditEvent.objects.create(
            actor=role_users[Role.MODERATOR],
            action=f"event.{index}",
        )
        AuditEvent.objects.filter(pk=event.pk).update(created_at=now + timedelta(seconds=index))
        events.append(event)

    monkeypatch.setattr("core.views.collect_system_status", lambda: SYSTEM_STATUS)
    client = Client()

    client.force_login(role_users[Role.USER])
    user_dashboard = client.get(reverse("dashboard"))
    assert user_dashboard.status_code == 200
    assert user_dashboard.context["system"] is None
    assert user_dashboard.context["recent_events"] == []
    assert "system-hero" not in user_dashboard.content.decode()

    for role in (Role.MODERATOR, Role.ADMIN):
        client.force_login(role_users[role])
        dashboard = client.get(reverse("dashboard"))
        recent_events = dashboard.context["recent_events"]
        assert dashboard.status_code == 200
        assert dashboard.context["system"] == SYSTEM_STATUS
        assert len(recent_events) == 5
        assert [event.action for event in recent_events] == [
            "event.6",
            "event.5",
            "event.4",
            "event.3",
            "event.2",
        ]
        assert "system-hero" in dashboard.content.decode()
