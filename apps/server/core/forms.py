from __future__ import annotations

from urllib.parse import urlparse

from django import forms

from .models import Asset, Channel, Playlist, PlaylistItem, Screen, Slogan, SloganSet


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
        fields = ["name", "kind", "file", "source_url", "website_mode", "enabled"]

    def clean(self):
        cleaned = super().clean()
        kind = cleaned.get("kind")
        uploaded_file = cleaned.get("file")
        source_url = cleaned.get("source_url")
        if kind in {Asset.Kind.IMAGE, Asset.Kind.VIDEO} and not uploaded_file:
            self.add_error("file", "Выберите файл.")
        if kind == Asset.Kind.WEBSITE:
            if not source_url:
                self.add_error("source_url", "Укажите адрес сайта.")
            else:
                parsed = urlparse(source_url)
                if parsed.scheme not in {"http", "https"}:
                    self.add_error("source_url", "Разрешены только HTTP и HTTPS адреса.")
        return cleaned


class PlaylistForm(StyledModelForm):
    class Meta:
        model = Playlist
        fields = ["name", "description", "enabled"]


class PlaylistItemForm(StyledModelForm):
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
            "active_from": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "active_until": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "daily_start": forms.TimeInput(attrs={"type": "time"}),
            "daily_end": forms.TimeInput(attrs={"type": "time"}),
            "weekdays": forms.HiddenInput(),
        }


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
