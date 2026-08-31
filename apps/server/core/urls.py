from django.urls import path

from . import views

urlpatterns = [
    path("healthz", views.healthz, name="healthz"),
    path("", views.dashboard, name="dashboard"),
    path("assets/", views.asset_list, name="assets"),
    path("assets/<uuid:asset_id>/edit/", views.asset_edit, name="asset_edit"),
    path("assets/<uuid:asset_id>/toggle/", views.asset_toggle, name="asset_toggle"),
    path("assets/<uuid:asset_id>/delete/", views.asset_delete, name="asset_delete"),
    path("playlists/", views.playlist_list, name="playlists"),
    path("playlists/<int:playlist_id>/", views.playlist_edit, name="playlist_edit"),
    path(
        "playlists/<int:playlist_id>/settings/",
        views.playlist_settings,
        name="playlist_settings",
    ),
    path("playlists/<int:playlist_id>/reorder/", views.playlist_reorder, name="playlist_reorder"),
    path(
        "playlist-items/<int:item_id>/toggle/",
        views.playlist_item_toggle,
        name="playlist_item_toggle",
    ),
    path(
        "playlist-items/<int:item_id>/edit/",
        views.playlist_item_edit,
        name="playlist_item_edit",
    ),
    path(
        "playlist-items/<int:item_id>/delete/",
        views.playlist_item_delete,
        name="playlist_item_delete",
    ),
    path("channels/", views.channel_list, name="channels"),
    path("channels/<int:channel_id>/edit/", views.channel_edit, name="channel_edit"),
    path("channels/<int:channel_id>/publish/", views.channel_publish, name="channel_publish"),
    path("screens/", views.screen_list, name="screens"),
    path("screens/<uuid:screen_id>/edit/", views.screen_edit, name="screen_edit"),
    path("screens/<uuid:screen_id>/command/", views.screen_command, name="screen_command"),
    path("slogans/", views.slogans, name="slogans"),
    path("slogans/<int:slogan_id>/edit/", views.slogan_edit, name="slogan_edit"),
    path("slogans/<int:slogan_id>/toggle/", views.slogan_toggle, name="slogan_toggle"),
    path("slogans/<int:slogan_id>/move/", views.slogan_move, name="slogan_move"),
    path("slogans/<int:slogan_id>/delete/", views.slogan_delete, name="slogan_delete"),
    path("widgets/", views.widget_list, name="widgets"),
    path("widgets/create/", views.widget_create, name="widget_create"),
    path("widgets/<int:scene_id>/edit/", views.widget_edit, name="widget_edit"),
    path("widgets/<int:scene_id>/delete/", views.widget_delete, name="widget_delete"),
    # Старые адреса сохранены для закладок и совместимости.
    path("scenes/", views.widget_list, name="scenes"),
    path("scenes/<int:scene_id>/edit/", views.widget_edit, name="scene_edit"),
    path("wiki/", views.wiki_index, name="wiki"),
    path("wiki/<slug:slug>/", views.wiki_article, name="wiki_article"),
    path("administration/users/", views.user_list, name="users"),
    path("administration/users/<int:user_id>/", views.user_edit, name="user_edit"),
    path("display/<uuid:screen_id>/<str:token>/", views.player, name="player"),
    path(
        "display/<uuid:screen_id>/<str:token>/player-sw.js",
        views.player_service_worker,
        name="player_service_worker",
    ),
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
