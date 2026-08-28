from __future__ import annotations

import math
import mimetypes
from pathlib import Path
from urllib.parse import urlparse

from django import forms
from django.db.models import Q
from django.utils import timezone
from PIL import Image, UnidentifiedImageError

from .models import Asset, Channel, Playlist, PlaylistItem, Scene, Screen, Slogan, SloganSet

WEEKDAY_CHOICES = [
    (0, "Пн"),
    (1, "Вт"),
    (2, "Ср"),
    (3, "Чт"),
    (4, "Пт"),
    (5, "Сб"),
    (6, "Вс"),
]


class StyledModelForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            current = field.widget.attrs.get("class", "")
            field.widget.attrs["class"] = f"field-control {current}".strip()


class AssetForm(StyledModelForm):
    source_url = forms.URLField(
        label="Адрес сайта",
        required=False,
        assume_scheme="https",
        widget=forms.URLInput(attrs={"placeholder": "https://example.org"}),
    )

    class Meta:
        model = Asset
        fields = ["name", "file", "source_url", "website_mode", "enabled"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["file"].help_text = "Тип фото или видео определится автоматически."
        self.fields["source_url"].help_text = "Для сайта укажите ссылку вместо файла."
        self.fields["website_mode"].initial = Asset.WebsiteMode.LIVE
        self.fields["file"].widget.attrs["data-asset-file"] = ""
        self.fields["source_url"].widget.attrs["data-asset-url"] = ""
        self.fields["website_mode"].widget.attrs["data-website-mode"] = ""

    @staticmethod
    def _infer_file_kind(uploaded_file) -> str:
        content_type = (getattr(uploaded_file, "content_type", "") or "").split(";", 1)[
            0
        ].lower()
        guessed_type = (mimetypes.guess_type(uploaded_file.name)[0] or "").lower()
        for candidate in (content_type, guessed_type):
            if candidate.startswith("image/"):
                return Asset.Kind.IMAGE
            if candidate.startswith("video/"):
                return Asset.Kind.VIDEO

        suffix = Path(uploaded_file.name).suffix.lower()
        if suffix in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tif", ".tiff"}:
            return Asset.Kind.IMAGE
        if suffix in {
            ".mp4",
            ".m4v",
            ".mov",
            ".webm",
            ".mkv",
            ".avi",
            ".mpeg",
            ".mpg",
            ".mts",
            ".m2ts",
            ".wmv",
        }:
            return Asset.Kind.VIDEO

        # Some browsers send application/octet-stream. Pillow can still identify
        # an image by its contents; the worker performs the final authoritative probe.
        try:
            uploaded_file.seek(0)
            with Image.open(uploaded_file) as image:
                image.verify()
        except (OSError, UnidentifiedImageError):
            pass
        else:
            return Asset.Kind.IMAGE
        finally:
            uploaded_file.seek(0)

        raise forms.ValidationError(
            "Не удалось определить тип файла. Поддерживаются изображения и видео."
        )

    def clean(self):
        cleaned = super().clean()
        uploaded_file = cleaned.get("file")
        source_url = cleaned.get("source_url")
        if uploaded_file and source_url:
            raise forms.ValidationError("Загрузите файл или укажите сайт, но не оба сразу.")
        if not uploaded_file and not source_url:
            raise forms.ValidationError("Загрузите фото или видео либо укажите адрес сайта.")

        if uploaded_file:
            try:
                self._inferred_kind = self._infer_file_kind(uploaded_file)
            except forms.ValidationError as exc:
                self.add_error("file", exc)
            cleaned["source_url"] = ""
            cleaned["website_mode"] = ""
        else:
            parsed = urlparse(source_url)
            if parsed.scheme not in {"http", "https"}:
                self.add_error("source_url", "Разрешены только HTTP и HTTPS адреса.")
            self._inferred_kind = Asset.Kind.WEBSITE
            cleaned["website_mode"] = cleaned.get("website_mode") or Asset.WebsiteMode.LIVE
        return cleaned

    def save(self, commit=True):
        asset = super().save(commit=False)
        asset.kind = self._inferred_kind
        if asset.kind != Asset.Kind.WEBSITE:
            asset.source_url = ""
            asset.website_mode = ""
        if commit:
            asset.save()
            self.save_m2m()
        return asset


class AssetEditForm(StyledModelForm):
    source_url = forms.URLField(
        label="Адрес сайта",
        required=False,
        assume_scheme="https",
        widget=forms.URLInput(attrs={"placeholder": "https://example.org"}),
    )

    class Meta:
        model = Asset
        fields = ["name", "enabled", "source_url", "website_mode"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.kind != Asset.Kind.WEBSITE:
            self.fields.pop("source_url")
            self.fields.pop("website_mode")

    def clean_source_url(self):
        value = self.cleaned_data.get("source_url", "")
        if self.instance.kind == Asset.Kind.WEBSITE and not value:
            raise forms.ValidationError("Укажите адрес сайта.")
        return value


class PlaylistForm(StyledModelForm):
    class Meta:
        model = Playlist
        fields = ["name", "description", "enabled"]


class AssetMetadataSelect(forms.Select):
    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(
            name, value, label, selected, index, subindex=subindex, attrs=attrs
        )
        asset = getattr(value, "instance", None)
        if asset is not None:
            option["attrs"]["data-kind"] = asset.kind
            if asset.duration_ms:
                option["attrs"]["data-duration-ms"] = str(asset.duration_ms)
        return option


class PlaylistItemForm(StyledModelForm):
    weekdays = forms.MultipleChoiceField(
        label="Дни недели",
        required=False,
        choices=WEEKDAY_CHOICES,
        widget=forms.CheckboxSelectMultiple,
        help_text="Ничего не выбрано — показывать каждый день.",
    )

    class Meta:
        model = PlaylistItem
        fields = [
            "item_type",
            "asset",
            "scene",
            "title",
            "enabled",
            "duration_seconds",
            "fit_mode",
            "volume",
            "muted",
            "overlay_mode",
            "active_from",
            "active_until",
            "weekdays",
            "daily_start",
            "daily_end",
        ]
        widgets = {
            "item_type": forms.HiddenInput(),
            "asset": AssetMetadataSelect,
            "active_from": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}
            ),
            "active_until": forms.DateTimeInput(
                format="%Y-%m-%dT%H:%M", attrs={"type": "datetime-local"}
            ),
            "daily_start": forms.TimeInput(attrs={"type": "time"}),
            "daily_end": forms.TimeInput(attrs={"type": "time"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["item_type"].required = False
        self.fields["duration_seconds"].required = False
        self.fields["duration_seconds"].help_text = (
            "Фото и сайты — 10 секунд; для видео используется его полная длительность."
        )
        if not self.is_bound and not self.instance.pk:
            local_now = timezone.localtime().replace(second=0, microsecond=0)
            self.initial.setdefault("duration_seconds", 10)
            self.initial.setdefault("active_from", local_now)
            self.initial.setdefault("active_until", None)
            self.initial.setdefault("weekdays", [])
            self.initial.setdefault("daily_start", None)
            self.initial.setdefault("daily_end", None)

        available_assets = Asset.objects.filter(
            deleted_at__isnull=True,
            enabled=True,
            status=Asset.Status.READY,
        )
        if self.instance.pk and self.instance.asset_id:
            available_assets = Asset.objects.filter(
                Q(
                    deleted_at__isnull=True,
                    enabled=True,
                    status=Asset.Status.READY,
                )
                | Q(pk=self.instance.asset_id)
            )
        self.fields["asset"].queryset = available_assets.order_by("name")

    def clean_weekdays(self):
        return [int(value) for value in self.cleaned_data.get("weekdays", [])]

    def clean(self):
        cleaned = super().clean()
        asset = cleaned.get("asset")
        scene = cleaned.get("scene")
        duration_seconds = cleaned.get("duration_seconds")

        if bool(asset) == bool(scene):
            raise forms.ValidationError("Выберите либо контент, либо сцену.")

        inferred_type = (
            PlaylistItem.ItemType.ASSET if asset else PlaylistItem.ItemType.SCENE
        )
        cleaned["item_type"] = inferred_type
        self.instance.item_type = inferred_type

        if asset and asset.kind == Asset.Kind.VIDEO and asset.duration_ms:
            cleaned["duration_seconds"] = max(1, math.ceil(asset.duration_ms / 1000))
        elif not duration_seconds:
            cleaned["duration_seconds"] = 10

        if not self.instance.pk and not cleaned.get("active_from"):
            cleaned["active_from"] = timezone.now()
        if not cleaned.get("weekdays"):
            cleaned["weekdays"] = []
        return cleaned


class ChannelForm(StyledModelForm):
    class Meta:
        model = Channel
        fields = [
            "name",
            "slug",
            "enabled",
            "timezone_name",
            "playlist",
            "theme",
            "slogan_set",
            "weather_source",
            "default_volume",
            "muted",
        ]


class ScreenForm(StyledModelForm):
    class Meta:
        model = Screen
        fields = ["name", "enabled", "channel", "sync_group"]


class SceneForm(StyledModelForm):
    header_subtitle = forms.CharField(label="Подпись в шапке", required=False)
    header_badge = forms.CharField(label="Метка справа", required=False)
    kicker = forms.CharField(label="Надзаголовок", required=False)
    title = forms.CharField(label="Заголовок", required=False)
    text = forms.CharField(
        label="Основной текст", required=False, widget=forms.Textarea(attrs={"rows": 4})
    )
    subtitle = forms.CharField(label="Подзаголовок", required=False)

    class Meta:
        model = Scene
        fields = ["name", "enabled", "theme", "slogan_set", "weather_source"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        config = self.instance.config or {}
        for field_name, key in {
            "header_subtitle": "headerSubtitle",
            "header_badge": "headerBadge",
            "kicker": "kicker",
            "title": "title",
            "text": "text",
            "subtitle": "subtitle",
        }.items():
            self.fields[field_name].initial = config.get(key, "")

    def save(self, commit=True):
        scene = super().save(commit=False)
        config = dict(scene.config or {})
        for field_name, key in {
            "header_subtitle": "headerSubtitle",
            "header_badge": "headerBadge",
            "kicker": "kicker",
            "title": "title",
            "text": "text",
            "subtitle": "subtitle",
        }.items():
            value = self.cleaned_data.get(field_name, "").strip()
            if value:
                config[key] = value
            else:
                config.pop(key, None)
        scene.config = config
        if commit:
            scene.save()
            self.save_m2m()
        return scene


class SloganSetForm(StyledModelForm):
    class Meta:
        model = SloganSet
        fields = ["name", "enabled", "playback_mode", "default_duration_seconds"]


class SloganForm(StyledModelForm):
    class Meta:
        model = Slogan
        fields = [
            "text",
            "subtitle",
            "enabled",
            "duration_seconds",
            "active_from",
            "active_until",
        ]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 3}),
            "active_from": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "active_until": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }
