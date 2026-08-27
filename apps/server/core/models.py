from __future__ import annotations

import hashlib
import secrets
import uuid
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone
from django.utils.text import slugify

User = get_user_model()


def asset_upload_path(instance: Asset, filename: str) -> str:
    suffix = Path(filename).suffix.lower()[:12]
    return f"originals/{instance.id.hex[:2]}/{instance.id}{suffix}"


class TimestampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ScheduleMixin(models.Model):
    active_from = models.DateTimeField("Показывать с", null=True, blank=True)
    active_until = models.DateTimeField("Показывать до", null=True, blank=True)
    weekdays = models.JSONField(
        "Дни недели",
        default=list,
        blank=True,
        help_text="Пусто — каждый день; 0 — понедельник, 6 — воскресенье",
    )
    daily_start = models.TimeField("Ежедневно с", null=True, blank=True)
    daily_end = models.TimeField("Ежедневно до", null=True, blank=True)

    class Meta:
        abstract = True


class BrandTheme(TimestampedModel):
    name = models.CharField("Название", max_length=120, unique=True)
    short_name = models.CharField("Сокращённое название", max_length=160)
    full_name = models.TextField("Полное название")
    is_default = models.BooleanField("Тема по умолчанию", default=False)
    logo_path = models.CharField("Путь к логотипу", max_length=255, default="brand/school-logo.png")
    colors = models.JSONField("Цвета", default=dict, blank=True)
    settings = models.JSONField("Настройки", default=dict, blank=True)

    class Meta:
        verbose_name = "фирменная тема"
        verbose_name_plural = "фирменные темы"

    def __str__(self) -> str:
        return self.name


class SloganSet(TimestampedModel):
    class PlaybackMode(models.TextChoices):
        SEQUENTIAL = "sequential", "По порядку"
        SHUFFLED = "shuffled", "Синхронно перемешивать"

    name = models.CharField("Название", max_length=120, unique=True)
    enabled = models.BooleanField("Включён", default=True)
    playback_mode = models.CharField(
        "Порядок", max_length=20, choices=PlaybackMode.choices, default=PlaybackMode.SEQUENTIAL
    )
    default_duration_seconds = models.PositiveSmallIntegerField(
        "Длительность по умолчанию",
        default=10,
        validators=[MinValueValidator(2), MaxValueValidator(3600)],
    )

    class Meta:
        verbose_name = "набор фраз"
        verbose_name_plural = "наборы фраз"

    def __str__(self) -> str:
        return self.name


class Slogan(ScheduleMixin, TimestampedModel):
    slogan_set = models.ForeignKey(SloganSet, on_delete=models.CASCADE, related_name="slogans")
    text = models.TextField("Фраза", max_length=500)
    subtitle = models.CharField("Подпись", max_length=240, blank=True)
    enabled = models.BooleanField("Включена", default=True)
    position = models.PositiveIntegerField("Порядок", default=0)
    duration_seconds = models.PositiveSmallIntegerField(
        "Длительность",
        null=True,
        blank=True,
        validators=[MinValueValidator(2), MaxValueValidator(3600)],
    )

    class Meta:
        ordering = ["position", "id"]
        verbose_name = "фраза"
        verbose_name_plural = "фразы"
        indexes = [models.Index(fields=["slogan_set", "enabled", "position"])]

    def __str__(self) -> str:
        return self.text[:80]


class WeatherSource(TimestampedModel):
    name = models.CharField("Название", max_length=120, unique=True)
    provider = models.CharField("Провайдер", max_length=40, default="open_meteo")
    enabled = models.BooleanField("Включён", default=True)
    latitude = models.DecimalField("Широта", max_digits=8, decimal_places=5)
    longitude = models.DecimalField("Долгота", max_digits=8, decimal_places=5)
    timezone_name = models.CharField("Часовой пояс", max_length=80, default="Asia/Sakhalin")
    update_interval_minutes = models.PositiveSmallIntegerField(
        "Интервал обновления", default=15, validators=[MinValueValidator(5)]
    )
    stale_after_minutes = models.PositiveIntegerField("Считать устаревшим через", default=180)
    last_success_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    current_data = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "источник погоды"
        verbose_name_plural = "источники погоды"

    def __str__(self) -> str:
        return self.name


class Scene(TimestampedModel):
    class SceneType(models.TextChoices):
        IDENTITY = "identity", "Официальная заставка"
        CLOCK = "clock", "Часы и дата"
        WEATHER = "weather", "Погода"
        CLOCK_WEATHER = "clock_weather", "Часы и погода"
        SLOGAN = "slogan", "Фраза"
        ANNOUNCEMENT = "announcement", "Объявление"
        PHOTO_MESSAGE = "photo_message", "Фото с подложкой"

    name = models.CharField("Название", max_length=160)
    scene_type = models.CharField("Тип", max_length=40, choices=SceneType.choices)
    enabled = models.BooleanField("Включена", default=True)
    theme = models.ForeignKey(
        BrandTheme, on_delete=models.PROTECT, related_name="scenes", null=True, blank=True
    )
    slogan_set = models.ForeignKey(
        SloganSet, on_delete=models.SET_NULL, null=True, blank=True, related_name="scenes"
    )
    weather_source = models.ForeignKey(
        WeatherSource, on_delete=models.SET_NULL, null=True, blank=True, related_name="scenes"
    )
    config = models.JSONField("Настройки сцены", default=dict, blank=True)

    class Meta:
        verbose_name = "сцена"
        verbose_name_plural = "сцены"

    def __str__(self) -> str:
        return self.name


class Asset(ScheduleMixin, TimestampedModel):
    class Kind(models.TextChoices):
        IMAGE = "image", "Фото"
        VIDEO = "video", "Видео"
        WEBSITE = "website", "Сайт"

    class WebsiteMode(models.TextChoices):
        LIVE = "live", "Живой сайт"
        SNAPSHOT = "snapshot", "Снимок сайта"

    class Status(models.TextChoices):
        UPLOADING = "uploading", "Загружается"
        PROCESSING = "processing", "Обрабатывается"
        READY = "ready", "Готов"
        FAILED = "failed", "Ошибка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("Название", max_length=240)
    kind = models.CharField("Тип", max_length=20, choices=Kind.choices)
    enabled = models.BooleanField("Включён", default=True)
    file = models.FileField("Оригинал", upload_to=asset_upload_path, null=True, blank=True)
    source_url = models.URLField("Адрес сайта", max_length=2048, blank=True)
    website_mode = models.CharField(
        "Режим сайта", max_length=20, choices=WebsiteMode.choices, blank=True
    )
    status = models.CharField(
        "Состояние", max_length=20, choices=Status.choices, default=Status.PROCESSING
    )
    mime_type = models.CharField(max_length=160, blank=True)
    sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    file_size = models.PositiveBigIntegerField(default=0)
    duration_ms = models.PositiveBigIntegerField(null=True, blank=True)
    width = models.PositiveIntegerField(null=True, blank=True)
    height = models.PositiveIntegerField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    error_message = models.TextField(blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "контент"
        verbose_name_plural = "контент"
        indexes = [models.Index(fields=["kind", "enabled", "status"])]

    def __str__(self) -> str:
        return self.name

    def soft_delete(self) -> None:
        self.deleted_at = timezone.now()
        self.enabled = False
        self.save(update_fields=["deleted_at", "enabled", "updated_at"])


class Playlist(TimestampedModel):
    name = models.CharField("Название", max_length=160, unique=True)
    description = models.TextField("Описание", blank=True)
    enabled = models.BooleanField("Включён", default=True)

    class Meta:
        verbose_name = "плейлист"
        verbose_name_plural = "плейлисты"

    def __str__(self) -> str:
        return self.name


class PlaylistItem(ScheduleMixin, TimestampedModel):
    class ItemType(models.TextChoices):
        ASSET = "asset", "Контент"
        SCENE = "scene", "Сцена"

    class FitMode(models.TextChoices):
        COVER = "cover", "Заполнить без искажения"
        CONTAIN = "contain", "Показать целиком"
        STRETCH = "stretch", "Растянуть"

    class OverlayMode(models.TextChoices):
        CHANNEL = "channel", "Оверлей канала"
        HIDDEN = "hidden", "Без оверлея"

    playlist = models.ForeignKey(Playlist, on_delete=models.CASCADE, related_name="items")
    item_type = models.CharField(
        "Тип элемента", max_length=20, choices=ItemType.choices, default=ItemType.ASSET
    )
    asset = models.ForeignKey(
        Asset, on_delete=models.PROTECT, related_name="playlist_items", null=True, blank=True
    )
    scene = models.ForeignKey(
        Scene, on_delete=models.PROTECT, related_name="playlist_items", null=True, blank=True
    )
    title = models.CharField("Название в плейлисте", max_length=240, blank=True)
    enabled = models.BooleanField("Включён", default=True)
    position = models.PositiveIntegerField("Порядок", default=0)
    duration_seconds = models.PositiveIntegerField(
        "Длительность",
        default=10,
        validators=[MinValueValidator(1), MaxValueValidator(86400)],
    )
    fit_mode = models.CharField(
        "Масштабирование", max_length=20, choices=FitMode.choices, default=FitMode.COVER
    )
    volume = models.PositiveSmallIntegerField(
        "Громкость", default=100, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    muted = models.BooleanField("Без звука", default=False)
    overlay_mode = models.CharField(
        "Оверлей",
        max_length=20,
        choices=OverlayMode.choices,
        default=OverlayMode.CHANNEL,
    )
    settings = models.JSONField("Дополнительные настройки", default=dict, blank=True)

    class Meta:
        ordering = ["position", "id"]
        verbose_name = "элемент плейлиста"
        verbose_name_plural = "элементы плейлиста"
        indexes = [models.Index(fields=["playlist", "enabled", "position"])]

    def __str__(self) -> str:
        return self.title or str(self.asset or self.scene or "Элемент")

    def clean(self) -> None:
        from django.core.exceptions import ValidationError

        if self.item_type == self.ItemType.ASSET and not self.asset:
            raise ValidationError({"asset": "Выберите контент."})
        if self.item_type == self.ItemType.SCENE and not self.scene:
            raise ValidationError({"scene": "Выберите сцену."})
        if self.asset and self.scene:
            raise ValidationError("Элемент не может одновременно ссылаться на контент и сцену.")


class SyncGroup(TimestampedModel):
    name = models.CharField("Название", max_length=160, unique=True)
    timeline_epoch = models.DateTimeField("Начало общей шкалы", default=timezone.now)

    class Meta:
        verbose_name = "группа синхронизации"
        verbose_name_plural = "группы синхронизации"

    def __str__(self) -> str:
        return self.name


class Channel(TimestampedModel):
    name = models.CharField("Название", max_length=160, unique=True)
    slug = models.SlugField("Код", max_length=100, unique=True)
    enabled = models.BooleanField("Включён", default=True)
    timezone_name = models.CharField("Часовой пояс", max_length=80, default="Asia/Sakhalin")
    playlist = models.ForeignKey(Playlist, on_delete=models.PROTECT, related_name="channels")
    theme = models.ForeignKey(
        BrandTheme, on_delete=models.PROTECT, related_name="channels", null=True, blank=True
    )
    slogan_set = models.ForeignKey(
        SloganSet, on_delete=models.SET_NULL, related_name="channels", null=True, blank=True
    )
    weather_source = models.ForeignKey(
        WeatherSource, on_delete=models.SET_NULL, related_name="channels", null=True, blank=True
    )
    default_volume = models.PositiveSmallIntegerField(
        "Громкость", default=100, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    muted = models.BooleanField("Без звука", default=False)
    overlay_config = models.JSONField("Оверлей", default=dict, blank=True)
    published_revision = models.ForeignKey(
        "PublishedRevision",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="active_for_channels",
    )

    class Meta:
        verbose_name = "канал"
        verbose_name_plural = "каналы"

    def __str__(self) -> str:
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name, allow_unicode=False) or uuid.uuid4().hex[:10]
        return super().save(*args, **kwargs)


class PublishedRevision(models.Model):
    channel = models.ForeignKey(Channel, on_delete=models.CASCADE, related_name="revisions")
    number = models.PositiveIntegerField()
    manifest = models.JSONField()
    published_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name="signage_publications"
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-number"]
        constraints = [
            models.UniqueConstraint(fields=["channel", "number"], name="unique_channel_revision")
        ]
        verbose_name = "опубликованная ревизия"
        verbose_name_plural = "опубликованные ревизии"

    def __str__(self) -> str:
        return f"{self.channel} · r{self.number}"


class Screen(TimestampedModel):
    class Status(models.TextChoices):
        UNKNOWN = "unknown", "Неизвестно"
        ONLINE = "online", "В сети"
        OFFLINE = "offline", "Не в сети"
        ERROR = "error", "Ошибка"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField("Название", max_length=160)
    enabled = models.BooleanField("Включён", default=True)
    channel = models.ForeignKey(Channel, on_delete=models.PROTECT, related_name="screens")
    sync_group = models.ForeignKey(
        SyncGroup, on_delete=models.SET_NULL, null=True, blank=True, related_name="screens"
    )
    token_prefix = models.CharField(max_length=12, blank=True)
    token_hash = models.CharField(max_length=64, blank=True)
    status = models.CharField(
        "Состояние", max_length=20, choices=Status.choices, default=Status.UNKNOWN
    )
    last_seen_at = models.DateTimeField(null=True, blank=True)
    last_ip = models.GenericIPAddressField(null=True, blank=True)
    current_item_key = models.CharField(max_length=160, blank=True)
    current_revision = models.PositiveIntegerField(null=True, blank=True)
    player_version = models.CharField(max_length=80, blank=True)
    capabilities = models.JSONField(default=dict, blank=True)
    last_error = models.TextField(blank=True)
    settings = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "экран"
        verbose_name_plural = "экраны"

    def __str__(self) -> str:
        return self.name

    def issue_token(self) -> str:
        token = secrets.token_urlsafe(32)
        self.token_prefix = token[:12]
        self.token_hash = hashlib.sha256(token.encode()).hexdigest()
        return token

    def verify_token(self, token: str) -> bool:
        if not token or not self.token_hash:
            return False
        candidate = hashlib.sha256(token.encode()).hexdigest()
        return secrets.compare_digest(candidate, self.token_hash)


class PlayerCommand(models.Model):
    class CommandType(models.TextChoices):
        NEXT = "next", "Следующий"
        PREVIOUS = "previous", "Предыдущий"
        RELOAD = "reload", "Перезагрузить"
        MUTE = "mute", "Выключить звук"
        UNMUTE = "unmute", "Включить звук"
        VOLUME = "volume", "Изменить громкость"

    screen = models.ForeignKey(Screen, on_delete=models.CASCADE, related_name="commands")
    command = models.CharField(max_length=20, choices=CommandType.choices)
    payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    acknowledged_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "команда плеера"
        verbose_name_plural = "команды плеера"

    def __str__(self) -> str:
        return f"{self.screen}: {self.command}"


class WorkerJob(models.Model):
    class JobType(models.TextChoices):
        PROBE_ASSET = "probe_asset", "Проверить файл"
        TRANSCODE = "transcode", "Перекодировать"
        SNAPSHOT = "snapshot", "Снимок сайта"
        WEATHER = "weather", "Обновить погоду"
        CLEANUP = "cleanup", "Очистка"

    class Status(models.TextChoices):
        QUEUED = "queued", "В очереди"
        RUNNING = "running", "Выполняется"
        DONE = "done", "Готово"
        FAILED = "failed", "Ошибка"

    job_type = models.CharField(max_length=30, choices=JobType.choices)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.QUEUED)
    attempts = models.PositiveSmallIntegerField(default=0)
    run_after = models.DateTimeField(default=timezone.now, db_index=True)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.CharField(max_length=120, blank=True)
    error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["run_after", "id"]
        indexes = [models.Index(fields=["status", "run_after"])]
        verbose_name = "фоновое задание"
        verbose_name_plural = "фоновые задания"

    def __str__(self) -> str:
        return f"#{self.pk} {self.job_type}: {self.status}"


class AuditEvent(models.Model):
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    action = models.CharField(max_length=120)
    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=120, blank=True)
    details = models.JSONField(default=dict, blank=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "событие аудита"
        verbose_name_plural = "журнал действий"

    def __str__(self) -> str:
        return f"{self.action} · {self.created_at:%Y-%m-%d %H:%M:%S}"
