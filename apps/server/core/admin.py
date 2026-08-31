from django.contrib import admin

from .models import (
    Asset,
    AuditEvent,
    BrandTheme,
    Channel,
    PlayerCommand,
    Playlist,
    PlaylistItem,
    PublishedRevision,
    Scene,
    Screen,
    Slogan,
    SloganSet,
    SyncGroup,
    WeatherSource,
    WorkerJob,
)


@admin.register(Asset)
class AssetAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "status", "enabled", "width", "height", "created_at")
    list_filter = ("kind", "status", "enabled")
    search_fields = ("name", "source_url", "sha256")
    readonly_fields = (
        "id",
        "sha256",
        "file_size",
        "duration_ms",
        "metadata",
        "created_at",
        "updated_at",
    )


class PlaylistItemInline(admin.TabularInline):
    model = PlaylistItem
    extra = 0
    ordering = ("position",)


@admin.register(Playlist)
class PlaylistAdmin(admin.ModelAdmin):
    list_display = ("name", "enabled", "updated_at")
    inlines = (PlaylistItemInline,)


@admin.register(Channel)
class ChannelAdmin(admin.ModelAdmin):
    list_display = ("name", "playlist", "enabled", "published_revision", "updated_at")
    list_filter = ("enabled", "timezone_name")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Screen)
class ScreenAdmin(admin.ModelAdmin):
    list_display = ("name", "channel", "status", "last_seen_at", "current_revision")
    list_filter = ("status", "enabled", "channel")
    search_fields = ("name", "id")
    readonly_fields = (
        "id",
        "token_hash",
        "token_prefix",
        "last_seen_at",
        "last_ip",
        "capabilities",
    )


@admin.register(SloganSet)
class SloganSetAdmin(admin.ModelAdmin):
    list_display = ("name", "enabled", "playback_mode", "default_duration_seconds")


@admin.register(Slogan)
class SloganAdmin(admin.ModelAdmin):
    list_display = ("text", "slogan_set", "enabled", "position", "duration_seconds")
    list_filter = ("slogan_set", "enabled")
    ordering = ("slogan_set", "position")


@admin.register(WeatherSource)
class WeatherSourceAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "enabled", "last_success_at", "last_error")
    readonly_fields = ("last_attempt_at", "last_success_at", "last_error", "current_data")


@admin.register(WorkerJob)
class WorkerJobAdmin(admin.ModelAdmin):
    list_display = ("id", "job_type", "status", "attempts", "run_after", "locked_by")
    list_filter = ("job_type", "status")
    readonly_fields = ("created_at", "finished_at")


admin.site.register(BrandTheme)
admin.site.register(Scene)
admin.site.register(SyncGroup)
admin.site.register(PublishedRevision)
admin.site.register(PlayerCommand)
admin.site.register(AuditEvent)

admin.site.site_header = "ОГАУ ДО «СШ ВВЕ» — цифровые экраны"
admin.site.site_title = "Digital Signage"


def _superuser_only(request):
    """Keep the low-level Django admin outside the application role model."""
    return request.user.is_active and request.user.is_superuser


admin.site.has_permission = _superuser_only
