from __future__ import annotations

from functools import wraps

from django.http import Http404, HttpRequest

from .models import Screen


def get_screen_or_404(screen_id, token: str) -> Screen:
    try:
        screen = Screen.objects.select_related(
            "channel", "channel__published_revision", "sync_group"
        ).get(pk=screen_id, enabled=True)
    except Screen.DoesNotExist as exc:
        raise Http404 from exc
    if not screen.verify_token(token):
        raise Http404
    return screen


def screen_token_required(view):
    @wraps(view)
    def wrapper(request: HttpRequest, screen_id, token: str, *args, **kwargs):
        request.signage_screen = get_screen_or_404(screen_id, token)
        return view(request, screen_id, token, *args, **kwargs)

    return wrapper
