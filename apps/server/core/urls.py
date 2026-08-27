from django.urls import path

from . import views

urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
    path("", views.dashboard, name="dashboard"),
    path("assets/", views.asset_list, name="assets"),
    path("assets/<uuid:asset_id>/toggle/", views.asset_toggle, name="asset_toggle"),
    path("assets/<uuid:asset_id>/delete/", views.asset_delete, name="asset_delete"),
    path("playlists/", views.playlist_list, name="playlists"),
    path("playlists/<int:playlist_id>/", views.playlist_edit, name="playlist_edit"),
    path("playlists/<int:playlist_id>/reorder/", views.playlist_reorder, name="playlist_reorder"),
    path(
        "playlist-items/<int:item_id>/toggle/",
        views.playlist_item_toggle,
        name="playlist_item_toggle",
    ),
    path(
        "playlist-items/<int:item_id>/delete/",
        views.playlist_item_delete,
        name="playlist_item_delete",
    ),
    path("channels/", views.channel_list, name="channels"),
    path("channels/<int:channel_id>/publish/", views.channel_publish, name="channel_publish"),
    path("screens/", views.screen_list, name="screens"),
    path("screens/<uuid:screen_id>/command/", views.screen_command, name="screen_command"),
    path("slogans/", views.slogans, name="slogans"),
    path("display/<uuid:screen_id>/<str:token>/", views.player, name="player"),
    path(
        "display/<uuid:screen_id>/<str:token>/manifest.json",
        views.player_manifest,
        name="player_manifest",
    ),
    path(
        "display/<uuid:screen_id>/<str:token>/heartbeat",
        views.player_heartbeat,
        name="player_heartbeat",
    ),
    path(
        "display/<uuid:screen_id>/<str:token>/media/<uuid:asset_id>",
        views.player_media,
        name="player_media",
    ),
    path(
        "display/<uuid:screen_id>/<str:token>/commands/<int:command_id>/ack",
        views.player_ack,
        name="player_ack",
    ),
]
