from pathlib import Path

from django.conf import settings


def player_source(name: str) -> str:
    path = Path(settings.REPOSITORY_DIR, "apps", "web", "static", name)
    return path.read_text(encoding="utf-8")


def test_player_has_no_interactive_audio_prompt():
    script = player_source("js/player.js")
    stylesheet = player_source("css/player.css")

    assert "Нажмите, чтобы включить звук" not in script
    assert "audioPrompt" not in script
    assert "audio-unlock" not in stylesheet
    assert "video.muted = videoShouldBeMuted(item)" in script
    assert "video.muted = true" in script


def test_widget_font_scaling_preserves_default_sizes_and_renders_announcement_subtitle():
    script = player_source("js/player.js")
    stylesheet = player_source("css/player.css")

    assert "Math.min(1.6, Math.max(.5" in script
    assert "announcement-subtitle" in script
    assert "Текст объявления настраивается в панели управления" not in script
    assert "Информационное сообщение" not in script
    assert "font-size:calc(10vw*var(--title-scale,1))" in stylesheet
    assert "font-size:calc(8.2vw*var(--title-scale,1))" in stylesheet
    assert "font-size:calc(2.6vw*var(--text-scale,1))" in stylesheet
    assert ".timezone-pill{font-size:calc(1vw*var(--subtitle-scale,1))}" in stylesheet
