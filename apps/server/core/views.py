from __future__ import annotations

import json
from datetime import timedelta

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Max, Q
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
from .player_auth import screen_token_required
from .services.manifest import PublicationError, publish_channel


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
    context = {
        "assets_count": Asset.objects.filter(deleted_at__isnull=True).count(),
        "playlists_count": Playlist.objects.count(),
        "channels_count": Channel.objects.count(),
        "screens_count": Screen.objects.count(),
        "screens_online": Screen.objects.filter(last_seen_at__gte=online_after).count(),
        "screens": Screen.objects.select_related("channel").order_by("name")[:10],
        "recent_events": AuditEvent.objects.select_related("actor")[:10],
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


@login_required
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


@login_required
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


@login_required
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


@login_required
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


@login_required
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


@login_required
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


@login_required
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


@login_required
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


@login_required
@require_POST
def slogan_toggle(request: HttpRequest, slogan_id: int) -> HttpResponse:
    slogan = get_object_or_404(Slogan, pk=slogan_id)
    slogan.enabled = not slogan.enabled
    slogan.save(update_fields=["enabled", "updated_at"])
    _audit(request, "slogan.toggled", slogan, {"enabled": slogan.enabled})
    return redirect("slogans")


@login_required
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


@login_required
@require_POST
def slogan_delete(request: HttpRequest, slogan_id: int) -> HttpResponse:
    slogan = get_object_or_404(Slogan, pk=slogan_id)
    _audit(request, "slogan.deleted", slogan)
    slogan.delete()
    messages.success(request, "Фраза удалена. Опубликуйте канал для применения.")
    return redirect("slogans")


@login_required
def scene_list(request: HttpRequest) -> HttpResponse:
    scenes = Scene.objects.select_related("theme", "slogan_set", "weather_source").order_by("name")
    return render(request, "core/scenes.html", {"scenes": scenes})


@login_required
@require_http_methods(["GET", "POST"])
def scene_edit(request: HttpRequest, scene_id: int) -> HttpResponse:
    scene = get_object_or_404(Scene, pk=scene_id)
    form = SceneForm(request.POST or None, instance=scene)
    if request.method == "POST" and form.is_valid():
        scene = form.save()
        _audit(request, "scene.updated", scene)
        messages.success(request, "Сцена изменена. Опубликуйте канал для применения.")
        return redirect("scenes")
    return render(
        request, "core/edit_form.html", {"form": form, "object": scene, "kind": "Сцена"}
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
    allowed = any(
        str(item.get("asset", {}).get("id")) == str(asset_id)
        for item in revision.manifest.get("items", [])
    )
    if not allowed:
        raise Http404
    # Опубликованная ревизия неизменяема: отключение элемента применяется только
    # после следующей публикации, а текущая ревизия продолжает работать без обрыва.
    asset = get_object_or_404(Asset, pk=asset_id)
    selected_file = asset.file
    if asset.kind == Asset.Kind.WEBSITE and asset.website_mode == Asset.WebsiteMode.SNAPSHOT:
        snapshot_name = asset.metadata.get("snapshotPath", "")
        if not snapshot_name:
            raise Http404
        snapshot_path = settings.SIGNAGE_SITE_SNAPSHOT_ROOT / snapshot_name
        if not snapshot_path.is_file():
            raise Http404
        return FileResponse(snapshot_path.open("rb"), content_type="image/png")
    if not selected_file:
        raise Http404
    if settings.DEBUG:
        return FileResponse(selected_file.open("rb"), content_type=asset.mime_type or None)
    response = HttpResponse(content_type=asset.mime_type or "application/octet-stream")
    response.headers["X-Accel-Redirect"] = f"/_protected_media/{selected_file.name}"
    response.headers["Content-Disposition"] = "inline"
    response.headers["Cache-Control"] = "private, max-age=31536000, immutable"
    if asset.file_size:
        response.headers["Content-Length"] = str(asset.file_size)
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
