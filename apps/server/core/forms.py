from __future__ import annotations

from urllib.parse import urlparse

from django import forms

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
            "active_from": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "active_until": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "daily_start": forms.TimeInput(attrs={"type": "time"}),
            "daily_end": forms.TimeInput(attrs={"type": "time"}),
        }

    def clean_weekdays(self):
        return [int(value) for value in self.cleaned_data.get("weekdays", [])]


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
