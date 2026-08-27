from __future__ import annotations

from typing import Any

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from core.models import Asset, Channel, PlaylistItem, PublishedRevision, Scene


class PublicationError(Exception):
    pass


def serialize_schedule(item: PlaylistItem) -> dict[str, Any]:
    return {
        "activeFrom": item.active_from.isoformat() if item.active_from else None,
        "activeUntil": item.active_until.isoformat() if item.active_until else None,
        "weekdays": item.weekdays,
        "dailyStart": item.daily_start.isoformat() if item.daily_start else None,
        "dailyEnd": item.daily_end.isoformat() if item.daily_end else None,
    }


def serialize_asset(asset: Asset) -> dict[str, Any]:
    return {
        "id": str(asset.id),
        "kind": asset.kind,
        "name": asset.name,
        "mimeType": asset.mime_type,
        "sha256": asset.sha256,
        "size": asset.file_size,
        "durationMs": asset.duration_ms,
        "width": asset.width,
        "height": asset.height,
        "url": asset.source_url if asset.kind == Asset.Kind.WEBSITE else None,
        "websiteMode": asset.website_mode,
        "mediaPath": asset.file.name if asset.file else None,
        "metadata": asset.metadata,
    }


def serialize_scene(scene: Scene, channel: Channel) -> dict[str, Any]:
    theme = scene.theme or channel.theme
    slogan_set = scene.slogan_set or channel.slogan_set
    weather_source = scene.weather_source or channel.weather_source
    slogans = []
    if slogan_set and slogan_set.enabled:
        slogans = [
            {
                "id": slogan.id,
                "text": slogan.text,
                "subtitle": slogan.subtitle,
                "position": slogan.position,
                "durationSeconds": slogan.duration_seconds or slogan_set.default_duration_seconds,
                "schedule": {
                    "activeFrom": slogan.active_from.isoformat() if slogan.active_from else None,
                    "activeUntil": slogan.active_until.isoformat() if slogan.active_until else None,
                    "weekdays": slogan.weekdays,
                    "dailyStart": slogan.daily_start.isoformat() if slogan.daily_start else None,
                    "dailyEnd": slogan.daily_end.isoformat() if slogan.daily_end else None,
                },
            }
            for slogan in slogan_set.slogans.filter(enabled=True).order_by("position", "id")
        ]

    return {
        "id": scene.id,
        "name": scene.name,
        "type": scene.scene_type,
        "config": scene.config,
        "theme": {
            "shortName": theme.short_name if theme else "ОГАУ ДО «СШ ВВЕ»",
            "fullName": theme.full_name if theme else "",
            "logoPath": theme.logo_path if theme else "brand/school-logo.png",
            "colors": theme.colors if theme else {},
            "settings": theme.settings if theme else {},
        },
        "slogans": {
            "mode": slogan_set.playback_mode if slogan_set else "sequential",
            "items": slogans,
        },
        "weather": {
            "sourceId": weather_source.id if weather_source else None,
            "data": weather_source.current_data if weather_source else {},
            "updatedAt": weather_source.last_success_at.isoformat()
            if weather_source and weather_source.last_success_at
            else None,
            "staleAfterMinutes": weather_source.stale_after_minutes if weather_source else 180,
        },
    }


def build_manifest(channel: Channel, revision_number: int) -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    problems: list[str] = []

    queryset = (
        channel.playlist.items.filter(enabled=True)
        .select_related(
            "asset", "scene", "scene__theme", "scene__slogan_set", "scene__weather_source"
        )
        .order_by("position", "id")
    )
    for item in queryset:
        entry: dict[str, Any] = {
            "key": f"playlist-item-{item.id}",
            "type": item.item_type,
            "title": item.title or str(item.asset or item.scene),
            "position": item.position,
            "durationMs": item.duration_seconds * 1000,
            "fit": item.fit_mode,
            "volume": item.volume,
            "muted": item.muted,
            "overlay": item.overlay_mode,
            "schedule": serialize_schedule(item),
            "settings": item.settings,
        }

        if item.item_type == PlaylistItem.ItemType.ASSET:
            asset = item.asset
            if not asset or not asset.enabled or asset.deleted_at:
                problems.append(f"Элемент {item.id}: контент отключён или удалён")
                continue
            if asset.status != Asset.Status.READY:
                problems.append(f"{asset.name}: контент ещё не готов")
                continue
            if asset.kind == Asset.Kind.VIDEO:
                if not asset.duration_ms:
                    problems.append(f"{asset.name}: не определена длительность видео")
                    continue
                entry["durationMs"] = asset.duration_ms
            entry["asset"] = serialize_asset(asset)
        else:
            scene = item.scene
            if not scene or not scene.enabled:
                problems.append(f"Элемент {item.id}: сцена отключена")
                continue
            entry["scene"] = serialize_scene(scene, channel)
        items.append(entry)

    if problems:
        raise PublicationError("; ".join(problems))
    if not items:
        raise PublicationError("В плейлисте нет активных готовых элементов")

    total_duration_ms = sum(int(item["durationMs"]) for item in items)
    theme = channel.theme
    weather = channel.weather_source
    return {
        "schemaVersion": 1,
        "channel": {
            "id": channel.id,
            "name": channel.name,
            "slug": channel.slug,
            "timezone": channel.timezone_name,
            "defaultVolume": channel.default_volume,
            "muted": channel.muted,
            "overlay": channel.overlay_config,
        },
        "revision": revision_number,
        "generatedAt": timezone.now().isoformat(),
        "timelineEpoch": timezone.now().replace(microsecond=0).isoformat(),
        "totalDurationMs": total_duration_ms,
        "theme": {
            "shortName": theme.short_name if theme else "ОГАУ ДО «СШ ВВЕ»",
            "fullName": theme.full_name if theme else "",
            "logoPath": theme.logo_path if theme else "brand/school-logo.png",
            "colors": theme.colors if theme else {},
        },
        "weather": {
            "data": weather.current_data if weather else {},
            "updatedAt": weather.last_success_at.isoformat()
            if weather and weather.last_success_at
            else None,
            "staleAfterMinutes": weather.stale_after_minutes if weather else 180,
        },
        "items": items,
    }


@transaction.atomic
def publish_channel(channel: Channel, user=None) -> PublishedRevision:
    channel = (
        Channel.objects.select_for_update()
        .select_related("playlist", "theme", "slogan_set", "weather_source")
        .get(pk=channel.pk)
    )
    last_number = (
        PublishedRevision.objects.filter(channel=channel).aggregate(max_number=Max("number"))[
            "max_number"
        ]
        or 0
    )
    number = last_number + 1
    manifest = build_manifest(channel, number)
    revision = PublishedRevision.objects.create(
        channel=channel,
        number=number,
        manifest=manifest,
        published_by=user if getattr(user, "is_authenticated", False) else None,
    )
    channel.published_revision = revision
    channel.save(update_fields=["published_revision", "updated_at"])
    return revision
