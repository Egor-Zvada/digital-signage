from __future__ import annotations

import json
from datetime import timedelta
from pathlib import PurePosixPath

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Max, Q, Sum
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .forms import (
    AssetEditForm,
    AssetForm,
    ChannelForm,
    PlaylistForm,
    PlaylistItemForm,
    SceneForm,
    ScreenForm,
    SloganForm,
    UserCreateForm,
    UserUpdateForm,
)
from .models import (
    Asset,
    AuditEvent,
    Channel,
    PlayerCommand,
    Playlist,
    PlaylistItem,
    Scene,
    Screen,
    Slogan,
    SloganSet,
    WorkerJob,
)
from .permissions import ROLE_LABELS, Role, get_user_role, role_at_least, role_required
from .player_auth import screen_token_required
from .services.manifest import PublicationError, publish_channel
from .services.system_status import collect_system_status
from .wiki_content import WIKI_ARTICLES, WIKI_BY_SLUG

User = get_user_model()

AUDIT_LABELS = {
    "asset.created": "Добавлен контент",
    "asset.updated": "Изменён контент",
    "asset.enabled": "Включён контент",
    "asset.disabled": "Выключен контент",
    "asset.deleted": "Удалён контент",
    "playlist.created": "Создан плейлист",
    "playlist.updated": "Изменён плейлист",
    "playlist.item_added": "Добавлен элемент плейлиста",
    "playlist.item_updated": "Изменён элемент плейлиста",
    "playlist.item_deleted": "Удалён элемент плейлиста",
    "playlist.reordered": "Изменён порядок плейлиста",
    "channel.created": "Создан канал",
    "channel.updated": "Изменён канал",
    "channel.published": "Опубликован канал",
    "screen.created": "Создан экран",
    "screen.updated": "Изменён экран",
    "screen.command": "Отправлена команда экрану",
    "scene.created": "Создан виджет",
    "scene.updated": "Изменён виджет",
    "scene.deleted": "Удалён виджет",
    "slogan.created": "Добавлена фраза",
    "slogan.updated": "Изменена фраза",
    "slogan.deleted": "Удалена фраза",
    "user.created": "Создан пользователь",
    "user.updated": "Изменён пользователь",
}


def _client_ip(request: HttpRequest) -> str | None:
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[0].strip() or request.META.get("REMOTE_ADDR"))[:45]


def _audit(request: HttpRequest, action: str, obj=None, details=None) -> None:
    AuditEvent.objects.create(
        actor=request.user if request.user.is_authenticated else None,
        action=action,
        object_type=obj.__class__.__name__ if obj else "",
        object_id=str(obj.pk) if obj else "",
        details=details or {},
        ip_address=_client_ip(request),
    )


@require_GET
def healthz(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok", "time": timezone.now().isoformat()})


@login_required
def dashboard(request: HttpRequest) -> HttpResponse:
    now = timezone.now()
    online_after = now - timedelta(seconds=90)
    assets = Asset.objects.filter(deleted_at__isnull=True)
    operational_access = role_at_least(request.user, Role.MODERATOR)
    recent_events = []
    if operational_access:
        recent_events = list(AuditEvent.objects.select_related("actor")[:5])
        for event in recent_events:
            event.display_action = AUDIT_LABELS.get(event.action, event.action)
    context = {
        "assets_count": assets.count(),
        "playlists_count": Playlist.objects.count(),
        "channels_count": Channel.objects.count(),
        "screens_count": Screen.objects.count(),
        "screens_online": Screen.objects.filter(last_seen_at__gte=online_after).count(),
        "screens": Screen.objects.select_related("channel").order_by("name")[:10],
        "recent_events": recent_events,
        "operational_access": operational_access,
        "system": collect_system_status() if operational_access else None,
        "content_ready": assets.filter(status=Asset.Status.READY).count(),
        "content_processing": assets.filter(status=Asset.Status.PROCESSING).count(),
        "content_failed": assets.filter(status=Asset.Status.FAILED).count(),
        "content_bytes": assets.aggregate(total=Sum("file_size"))["total"] or 0,
        "jobs_waiting": WorkerJob.objects.filter(
            status__in=[WorkerJob.Status.QUEUED, WorkerJob.Status.RUNNING]
        ).count(),
    }
    return render(request, "core/dashboard.html", context)


@login_required
@require_http_methods(["GET", "POST"])
def asset_list(request: HttpRequest) -> HttpResponse:
    form = AssetForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        asset = form.save(commit=False)
        if asset.kind == Asset.Kind.WEBSITE:
            asset.status = Asset.Status.PROCESSING
        else:
            asset.status = Asset.Status.PROCESSING
            if asset.file:
                asset.file_size = asset.file.size
                asset.mime_type = getattr(asset.file.file, "content_type", "") or ""
        asset.save()
        WorkerJob.objects.create(
            job_type=WorkerJob.JobType.PROBE_ASSET, payload={"assetId": str(asset.id)}
        )
        _audit(request, "asset.created", asset)
        messages.success(request, "Контент добавлен в очередь обработки.")
        return redirect("assets")

    assets = Asset.objects.filter(deleted_at__isnull=True).order_by("-created_at")
    query = request.GET.get("q", "").strip()
    if query:
        assets = assets.filter(Q(name__icontains=query) | Q(source_url__icontains=query))
    kind = request.GET.get("kind", "")
    if kind:
        assets = assets.filter(kind=kind)
    ordering = {
        "name": ("name",),
        "oldest": ("created_at",),
        "type": ("kind", "name"),
        "newest": ("-created_at",),
    }.get(request.GET.get("sort", "newest"), ("-created_at",))
    assets = assets.order_by(*ordering)
    return render(request, "core/assets.html", {"assets": assets, "form": form})


@login_required
@require_http_methods(["GET", "POST"])
def asset_edit(request: HttpRequest, asset_id) -> HttpResponse:
    asset = get_object_or_404(Asset, pk=asset_id, deleted_at__isnull=True)
    old_source = asset.source_url
    old_mode = asset.website_mode
    form = AssetEditForm(request.POST or None, instance=asset)
    if request.method == "POST" and form.is_valid():
        asset = form.save()
        website_changed = asset.kind == Asset.Kind.WEBSITE and (
            asset.source_url != old_source or asset.website_mode != old_mode
        )
        if website_changed:
            asset.status = Asset.Status.PROCESSING
            asset.error_message = ""
            asset.save(update_fields=["status", "error_message", "updated_at"])
            WorkerJob.objects.create(
                job_type=WorkerJob.JobType.PROBE_ASSET, payload={"assetId": str(asset.id)}
            )
        _audit(request, "asset.updated", asset)
        messages.success(request, "Контент изменён. Опубликуйте канал, когда будете готовы.")
        return redirect("assets")
    return render(
        request, "core/edit_form.html", {"form": form, "object": asset, "kind": "Контент"}
    )


@login_required
@require_POST
def asset_toggle(request: HttpRequest, asset_id) -> HttpResponse:
    asset = get_object_or_404(Asset, pk=asset_id, deleted_at__isnull=True)
    asset.enabled = not asset.enabled
    asset.save(update_fields=["enabled", "updated_at"])
    _audit(request, "asset.enabled" if asset.enabled else "asset.disabled", asset)
    return redirect("assets")


@login_required
@require_POST
def asset_delete(request: HttpRequest, asset_id) -> HttpResponse:
    asset = get_object_or_404(Asset, pk=asset_id, deleted_at__isnull=True)
    if asset.playlist_items.exists():
        messages.error(request, "Сначала удалите контент из плейлистов.")
    else:
        asset.soft_delete()
        _audit(request, "asset.deleted", asset)
        messages.success(request, "Контент перемещён в корзину.")
    return redirect("assets")


@login_required
@require_http_methods(["GET", "POST"])
def playlist_list(request: HttpRequest) -> HttpResponse:
    form = PlaylistForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        playlist = form.save()
        _audit(request, "playlist.created", playlist)
        return redirect("playlist_edit", playlist_id=playlist.id)
    playlists = Playlist.objects.annotate(item_count=Count("items")).order_by("name")
    return render(request, "core/playlists.html", {"playlists": playlists, "form": form})


@login_required
@require_http_methods(["GET", "POST"])
def playlist_edit(request: HttpRequest, playlist_id: int) -> HttpResponse:
    playlist = get_object_or_404(Playlist, pk=playlist_id)
    item_form = PlaylistItemForm(request.POST or None)
    if request.method == "POST" and item_form.is_valid():
        item = item_form.save(commit=False)
        item.playlist = playlist
        maximum = playlist.items.aggregate(value=Max("position"))["value"] or 0
        item.position = maximum + 10
        item.full_clean()
        item.save()
        _audit(request, "playlist.item_added", item, {"playlistId": playlist.id})
        messages.success(request, "Элемент добавлен.")
        return redirect("playlist_edit", playlist_id=playlist.id)
    items = playlist.items.select_related("asset", "scene").order_by("position", "id")
    return render(
        request,
        "core/playlist_edit.html",
        {"playlist": playlist, "items": items, "item_form": item_form},
    )


@login_required
@require_http_methods(["GET", "POST"])
def playlist_settings(request: HttpRequest, playlist_id: int) -> HttpResponse:
    playlist = get_object_or_404(Playlist, pk=playlist_id)
    form = PlaylistForm(request.POST or None, instance=playlist)
    if request.method == "POST" and form.is_valid():
        playlist = form.save()
        _audit(request, "playlist.updated", playlist)
        messages.success(request, "Плейлист изменён.")
        return redirect("playlist_edit", playlist_id=playlist.id)
    return render(
        request,
        "core/edit_form.html",
        {"form": form, "object": playlist, "kind": "Плейлист"},
    )


@login_required
@require_POST
def playlist_reorder(request: HttpRequest, playlist_id: int) -> JsonResponse:
    playlist = get_object_or_404(Playlist, pk=playlist_id)
    try:
        payload = json.loads(request.body)
        ordered_ids = [int(value) for value in payload["ids"]]
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return JsonResponse({"error": "Некорректный порядок"}, status=400)

    actual_ids = set(playlist.items.values_list("id", flat=True))
    if set(ordered_ids) != actual_ids or len(ordered_ids) != len(actual_ids):
        return JsonResponse({"error": "Список элементов изменился, обновите страницу"}, status=409)

    with transaction.atomic():
        for position, item_id in enumerate(ordered_ids, start=1):
            PlaylistItem.objects.filter(pk=item_id, playlist=playlist).update(
                position=position * 10
            )
    _audit(request, "playlist.reordered", playlist, {"ids": ordered_ids})
    return JsonResponse({"ok": True})


@login_required
@require_POST
def playlist_item_toggle(request: HttpRequest, item_id: int) -> HttpResponse:
    item = get_object_or_404(PlaylistItem, pk=item_id)
    item.enabled = not item.enabled
    item.save(update_fields=["enabled", "updated_at"])
    _audit(request, "playlist.item_toggled", item, {"enabled": item.enabled})
    return redirect("playlist_edit", playlist_id=item.playlist_id)


@login_required
@require_http_methods(["GET", "POST"])
def playlist_item_edit(request: HttpRequest, item_id: int) -> HttpResponse:
    item = get_object_or_404(PlaylistItem, pk=item_id)
    form = PlaylistItemForm(request.POST or None, instance=item)
    if request.method == "POST" and form.is_valid():
        item = form.save()
        _audit(request, "playlist.item_updated", item, {"playlistId": item.playlist_id})
        messages.success(request, "Параметры показа изменены. Опубликуйте канал для применения.")
        return redirect("playlist_edit", playlist_id=item.playlist_id)
    return render(
        request,
        "core/edit_form.html",
        {"form": form, "object": item, "kind": "Элемент плейлиста"},
    )


@login_required
@require_POST
def playlist_item_delete(request: HttpRequest, item_id: int) -> HttpResponse:
    item = get_object_or_404(PlaylistItem, pk=item_id)
    playlist_id = item.playlist_id
    _audit(request, "playlist.item_deleted", item)
    item.delete()
    return redirect("playlist_edit", playlist_id=playlist_id)


@role_required(Role.MODERATOR)
@require_http_methods(["GET", "POST"])
def channel_list(request: HttpRequest) -> HttpResponse:
    form = ChannelForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        channel = form.save()
        _audit(request, "channel.created", channel)
        messages.success(request, "Канал создан.")
        return redirect("channels")
    channels = Channel.objects.select_related("playlist", "published_revision").order_by("name")
    return render(request, "core/channels.html", {"channels": channels, "form": form})


@role_required(Role.MODERATOR)
@require_http_methods(["GET", "POST"])
def channel_edit(request: HttpRequest, channel_id: int) -> HttpResponse:
    channel = get_object_or_404(Channel, pk=channel_id)
    form = ChannelForm(request.POST or None, instance=channel)
    if request.method == "POST" and form.is_valid():
        channel = form.save()
        _audit(request, "channel.updated", channel)
        messages.success(request, "Канал изменён. Для экранов изменения появятся после публикации.")
        return redirect("channels")
    return render(
        request, "core/edit_form.html", {"form": form, "object": channel, "kind": "Канал"}
    )


@role_required(Role.MODERATOR)
@require_POST
def channel_publish(request: HttpRequest, channel_id: int) -> HttpResponse:
    channel = get_object_or_404(Channel, pk=channel_id)
    try:
        revision = publish_channel(channel, request.user)
    except PublicationError as exc:
        messages.error(request, f"Публикация невозможна: {exc}")
    else:
        _audit(request, "channel.published", channel, {"revision": revision.number})
        messages.success(request, f"Опубликована ревизия {revision.number}.")
    return redirect("channels")


@role_required(Role.MODERATOR)
@require_http_methods(["GET", "POST"])
def screen_list(request: HttpRequest) -> HttpResponse:
    form = ScreenForm(request.POST or None)
    issued_url = request.session.pop("issued_screen_url", None)
    if request.method == "POST" and form.is_valid():
        screen = form.save(commit=False)
        token = screen.issue_token()
        screen.save()
        issued_url = request.build_absolute_uri(
            reverse("player", kwargs={"screen_id": screen.id, "token": token})
        )
        request.session["issued_screen_url"] = issued_url
        _audit(request, "screen.created", screen)
        return redirect("screens")
    screens = Screen.objects.select_related("channel", "sync_group").order_by("name")
    return render(
        request,
        "core/screens.html",
        {"screens": screens, "form": form, "issued_url": issued_url},
    )


@role_required(Role.MODERATOR)
@require_http_methods(["GET", "POST"])
def screen_edit(request: HttpRequest, screen_id) -> HttpResponse:
    screen = get_object_or_404(Screen, pk=screen_id)
    form = ScreenForm(request.POST or None, instance=screen)
    if request.method == "POST" and form.is_valid():
        screen = form.save()
        _audit(request, "screen.updated", screen)
        messages.success(request, "Экран изменён. Его постоянная ссылка осталась прежней.")
        return redirect("screens")
    return render(
        request, "core/edit_form.html", {"form": form, "object": screen, "kind": "Экран"}
    )


@role_required(Role.MODERATOR)
@require_POST
def screen_command(request: HttpRequest, screen_id) -> HttpResponse:
    screen = get_object_or_404(Screen, pk=screen_id)
    command = request.POST.get("command", "")
    if command not in PlayerCommand.CommandType.values:
        messages.error(request, "Неизвестная команда.")
        return redirect("screens")
    payload = {}
    if command == PlayerCommand.CommandType.VOLUME:
        try:
            requested_volume = int(request.POST.get("value", "100"))
        except ValueError:
            requested_volume = 100
        payload["value"] = max(0, min(100, requested_volume))
    PlayerCommand.objects.create(
        screen=screen,
        command=command,
        payload=payload,
        expires_at=timezone.now() + timedelta(minutes=5),
    )
    _audit(request, "screen.command", screen, {"command": command, "payload": payload})
    messages.success(request, "Команда отправлена.")
    return redirect("screens")


@role_required(Role.MODERATOR)
@require_http_methods(["GET", "POST"])
def slogans(request: HttpRequest) -> HttpResponse:
    slogan_set = SloganSet.objects.first()
    form = SloganForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        if not slogan_set:
            slogan_set = SloganSet.objects.create(name="Основные фразы")
        slogan = form.save(commit=False)
        slogan.slogan_set = slogan_set
        maximum = slogan_set.slogans.aggregate(value=Max("position"))["value"] or 0
        slogan.position = maximum + 10
        slogan.save()
        _audit(request, "slogan.created", slogan)
        return redirect("slogans")
    items = slogan_set.slogans.all() if slogan_set else Slogan.objects.none()
    return render(
        request, "core/slogans.html", {"slogan_set": slogan_set, "items": items, "form": form}
    )


@role_required(Role.MODERATOR)
@require_http_methods(["GET", "POST"])
def slogan_edit(request: HttpRequest, slogan_id: int) -> HttpResponse:
    slogan = get_object_or_404(Slogan, pk=slogan_id)
    form = SloganForm(request.POST or None, instance=slogan)
    if request.method == "POST" and form.is_valid():
        slogan = form.save()
        _audit(request, "slogan.updated", slogan)
        messages.success(request, "Фраза изменена. Опубликуйте канал для применения.")
        return redirect("slogans")
    return render(
        request, "core/edit_form.html", {"form": form, "object": slogan, "kind": "Фраза"}
    )


@role_required(Role.MODERATOR)
@require_POST
def slogan_toggle(request: HttpRequest, slogan_id: int) -> HttpResponse:
    slogan = get_object_or_404(Slogan, pk=slogan_id)
    slogan.enabled = not slogan.enabled
    slogan.save(update_fields=["enabled", "updated_at"])
    _audit(request, "slogan.toggled", slogan, {"enabled": slogan.enabled})
    return redirect("slogans")


@role_required(Role.MODERATOR)
@require_POST
def slogan_move(request: HttpRequest, slogan_id: int) -> HttpResponse:
    slogan = get_object_or_404(Slogan, pk=slogan_id)
    direction = request.POST.get("direction")
    queryset = slogan.slogan_set.slogans.all()
    neighbour = (
        queryset.filter(position__lt=slogan.position).order_by("-position", "-id").first()
        if direction == "up"
        else queryset.filter(position__gt=slogan.position).order_by("position", "id").first()
    )
    if neighbour:
        with transaction.atomic():
            current_position = slogan.position
            slogan.position = neighbour.position
            neighbour.position = current_position
            slogan.save(update_fields=["position", "updated_at"])
            neighbour.save(update_fields=["position", "updated_at"])
        _audit(request, "slogan.reordered", slogan, {"direction": direction})
    return redirect("slogans")


@role_required(Role.MODERATOR)
@require_POST
def slogan_delete(request: HttpRequest, slogan_id: int) -> HttpResponse:
    slogan = get_object_or_404(Slogan, pk=slogan_id)
    _audit(request, "slogan.deleted", slogan)
    slogan.delete()
    messages.success(request, "Фраза удалена. Опубликуйте канал для применения.")
    return redirect("slogans")


@role_required(Role.MODERATOR)
def widget_list(request: HttpRequest) -> HttpResponse:
    scenes = Scene.objects.select_related("theme", "slogan_set", "weather_source").order_by("name")
    return render(request, "core/scenes.html", {"scenes": scenes})


@role_required(Role.MODERATOR)
@require_http_methods(["GET", "POST"])
def widget_create(request: HttpRequest) -> HttpResponse:
    initial = {}
    requested_type = request.GET.get("type", "")
    supported_types = {
        value
        for value in Scene.SceneType.values
        if value != Scene.SceneType.PHOTO_MESSAGE
    }
    if requested_type in supported_types:
        initial["scene_type"] = requested_type
    form = SceneForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        scene = form.save()
        _audit(request, "scene.created", scene, {"type": scene.scene_type})
        messages.success(
            request,
            "Виджет создан. Добавьте его в плейлист и опубликуйте канал.",
        )
        return redirect("widgets")
    return render(
        request,
        "core/widget_form.html",
        {"form": form, "kind": "Новый виджет", "is_create": True},
    )


@role_required(Role.MODERATOR)
@require_http_methods(["GET", "POST"])
def widget_edit(request: HttpRequest, scene_id: int) -> HttpResponse:
    scene = get_object_or_404(Scene, pk=scene_id)
    form = SceneForm(request.POST or None, instance=scene)
    if request.method == "POST" and form.is_valid():
        scene = form.save()
        _audit(request, "scene.updated", scene)
        messages.success(request, "Виджет изменён. Опубликуйте канал для применения.")
        return redirect("widgets")
    return render(
        request,
        "core/widget_form.html",
        {"form": form, "object": scene, "kind": "Виджет", "is_create": False},
    )


@role_required(Role.MODERATOR)
@require_POST
def widget_delete(request: HttpRequest, scene_id: int) -> HttpResponse:
    scene = get_object_or_404(Scene, pk=scene_id)
    if scene.playlist_items.exists():
        messages.error(request, "Сначала удалите виджет из всех плейлистов.")
    else:
        _audit(request, "scene.deleted", scene, {"name": scene.name})
        scene.delete()
        messages.success(request, "Виджет удалён.")
    return redirect("widgets")


@login_required
def wiki_index(request: HttpRequest) -> HttpResponse:
    query = request.GET.get("q", "").strip().lower()
    articles = WIKI_ARTICLES
    if query:
        articles = [
            article
            for article in WIKI_ARTICLES
            if query in article["title"].lower() or query in article["summary"].lower()
        ]
    return render(request, "core/wiki_index.html", {"articles": articles, "query": query})


@login_required
def wiki_article(request: HttpRequest, slug: str) -> HttpResponse:
    article = WIKI_BY_SLUG.get(slug)
    if not article:
        raise Http404("Статья не найдена")
    return render(
        request,
        "core/wiki_article.html",
        {"article": article, "articles": WIKI_ARTICLES},
    )


@role_required(Role.ADMIN)
@require_http_methods(["GET", "POST"])
def user_list(request: HttpRequest) -> HttpResponse:
    form = UserCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        _audit(
            request,
            "user.created",
            user,
            {"username": user.username, "role": get_user_role(user)},
        )
        messages.success(request, f"Пользователь {user.username} создан.")
        return redirect("users")
    users = User.objects.prefetch_related("groups").order_by("username")
    if not request.user.is_superuser:
        users = users.filter(is_superuser=False)
    rows = [
        {"user": user, "role": get_user_role(user), "role_label": ROLE_LABELS[get_user_role(user)]}
        for user in users
    ]
    return render(request, "core/users.html", {"rows": rows, "form": form})


@role_required(Role.ADMIN)
@require_http_methods(["GET", "POST"])
def user_edit(request: HttpRequest, user_id: int) -> HttpResponse:
    queryset = User.objects.all()
    if not request.user.is_superuser:
        queryset = queryset.filter(is_superuser=False)
    user = get_object_or_404(queryset, pk=user_id)
    old_role = get_user_role(user)
    old_active = user.is_active
    form = UserUpdateForm(request.POST or None, instance=user, acting_user=request.user)
    if request.method == "POST" and form.is_valid():
        password_changed = bool(form.cleaned_data.get("password1"))
        user = form.save()
        _audit(
            request,
            "user.updated",
            user,
            {
                "username": user.username,
                "oldRole": old_role,
                "role": get_user_role(user),
                "oldActive": old_active,
                "active": user.is_active,
                "passwordChanged": password_changed,
            },
        )
        messages.success(request, "Пользователь изменён.")
        return redirect("users")
    return render(
        request,
        "core/user_edit.html",
        {"form": form, "managed_user": user},
    )


@screen_token_required
@require_GET
def player(request: HttpRequest, screen_id, token: str) -> HttpResponse:
    response = render(
        request,
        "player/screen.html",
        {"screen": request.signage_screen, "screen_id": screen_id, "screen_token": token},
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@screen_token_required
@require_GET
def player_service_worker(request: HttpRequest, screen_id, token: str) -> HttpResponse:
    response = render(request, "player/service-worker.js", content_type="text/javascript")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = reverse(
        "player", kwargs={"screen_id": screen_id, "token": token}
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


@screen_token_required
@require_GET
def player_manifest(request: HttpRequest, screen_id, token: str) -> JsonResponse:
    screen = request.signage_screen
    revision = screen.channel.published_revision
    if not revision:
        return JsonResponse(
            {
                "schemaVersion": 1,
                "revision": 0,
                "serverTime": timezone.now().isoformat(),
                "screen": {"id": str(screen.id), "name": screen.name},
                "items": [],
            }
        )
    etag = f'"channel-{screen.channel_id}-revision-{revision.number}"'
    if request.headers.get("If-None-Match") == etag:
        response = HttpResponse(status=304)
        response.headers["ETag"] = etag
        return response
    payload = dict(revision.manifest)
    payload["items"] = [dict(item) for item in revision.manifest.get("items", [])]
    for item in payload["items"]:
        asset = item.get("asset")
        if not asset:
            continue
        asset = dict(asset)
        item["asset"] = asset
        if asset.get("kind") in {Asset.Kind.IMAGE, Asset.Kind.VIDEO}:
            asset["mediaUrl"] = reverse(
                "player_media",
                kwargs={"screen_id": screen.id, "token": token, "asset_id": asset["id"]},
            )
        elif asset.get("websiteMode") == Asset.WebsiteMode.SNAPSHOT:
            asset["mediaUrl"] = reverse(
                "player_media",
                kwargs={"screen_id": screen.id, "token": token, "asset_id": asset["id"]},
            )
    payload["serverTime"] = timezone.now().isoformat()
    payload["screen"] = {
        "id": str(screen.id),
        "name": screen.name,
        "syncGroup": screen.sync_group_id,
        "timelineEpoch": (
            screen.sync_group.timeline_epoch.isoformat()
            if screen.sync_group
            else payload.get("timelineEpoch")
        ),
        "settings": screen.settings,
    }
    response = JsonResponse(payload)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "no-cache"
    return response


@screen_token_required
@require_GET
def player_media(request: HttpRequest, screen_id, token: str, asset_id) -> HttpResponse:
    screen = request.signage_screen
    revision = screen.channel.published_revision
    if not revision:
        raise Http404
    published_asset = next(
        (
            item.get("asset", {})
            for item in revision.manifest.get("items", [])
            if str(item.get("asset", {}).get("id")) == str(asset_id)
        ),
        None,
    )
    if not published_asset:
        raise Http404
    # Опубликованная ревизия неизменяема: отключение элемента применяется только
    # после следующей публикации, а текущая ревизия продолжает работать без обрыва.
    asset = get_object_or_404(Asset, pk=asset_id)
    if asset.kind == Asset.Kind.WEBSITE and asset.website_mode == Asset.WebsiteMode.SNAPSHOT:
        snapshot_name = asset.metadata.get("snapshotPath", "")
        if not snapshot_name:
            raise Http404
        snapshot_path = settings.SIGNAGE_SITE_SNAPSHOT_ROOT / snapshot_name
        if not snapshot_path.is_file():
            raise Http404
        return FileResponse(snapshot_path.open("rb"), content_type="image/png")
    media_name = published_asset.get("mediaPath") or (asset.file.name if asset.file else "")
    relative_path = PurePosixPath(str(media_name))
    if not media_name or relative_path.is_absolute() or ".." in relative_path.parts:
        raise Http404
    selected_path = settings.MEDIA_ROOT.joinpath(*relative_path.parts)
    if not selected_path.is_file():
        raise Http404
    content_type = published_asset.get("mimeType") or asset.mime_type or "application/octet-stream"
    if settings.DEBUG:
        return FileResponse(selected_path.open("rb"), content_type=content_type)
    response = HttpResponse(content_type=content_type)
    response.headers["X-Accel-Redirect"] = f"/_protected_media/{relative_path.as_posix()}"
    response.headers["Content-Disposition"] = "inline"
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    if published_asset.get("size"):
        response.headers["Content-Length"] = str(published_asset["size"])
    return response


@screen_token_required
@csrf_exempt
@require_POST
def player_heartbeat(request: HttpRequest, screen_id, token: str) -> JsonResponse:
    screen = request.signage_screen
    try:
        payload = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return JsonResponse({"error": "invalid_json"}, status=400)
    screen.last_seen_at = timezone.now()
    screen.last_ip = _client_ip(request)
    screen.status = Screen.Status.ERROR if payload.get("error") else Screen.Status.ONLINE
    screen.current_item_key = str(payload.get("currentItem", ""))[:160]
    screen.current_revision = (
        payload.get("revision") if isinstance(payload.get("revision"), int) else None
    )
    screen.player_version = str(payload.get("playerVersion", ""))[:80]
    screen.capabilities = (
        payload.get("capabilities", {}) if isinstance(payload.get("capabilities"), dict) else {}
    )
    screen.last_error = str(payload.get("error", ""))[:2000]
    screen.save(
        update_fields=[
            "last_seen_at",
            "last_ip",
            "status",
            "current_item_key",
            "current_revision",
            "player_version",
            "capabilities",
            "last_error",
            "updated_at",
        ]
    )
    commands = list(
        screen.commands.filter(acknowledged_at__isnull=True, expires_at__gt=timezone.now())
        .order_by("id")
        .values("id", "command", "payload")[:20]
    )
    return JsonResponse({"serverTime": timezone.now().isoformat(), "commands": commands})


@screen_token_required
@csrf_exempt
@require_POST
def player_ack(request: HttpRequest, screen_id, token: str, command_id: int) -> JsonResponse:
    updated = request.signage_screen.commands.filter(
        pk=command_id, acknowledged_at__isnull=True
    ).update(acknowledged_at=timezone.now())
    return JsonResponse({"ok": bool(updated)})
