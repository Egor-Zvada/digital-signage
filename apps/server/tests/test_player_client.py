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
