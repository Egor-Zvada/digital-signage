from __future__ import annotations

from collections.abc import Callable
from functools import wraps

from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest, HttpResponse


class Role:
    USER = "user"
    MODERATOR = "moderator"
    ADMIN = "admin"


ROLE_CHOICES = [
    (Role.USER, "Пользователь"),
    (Role.MODERATOR, "Модератор"),
    (Role.ADMIN, "Администратор"),
]

ROLE_LABELS = dict(ROLE_CHOICES)
ROLE_GROUPS = {
    Role.USER: "signage_user",
    Role.MODERATOR: "signage_moderator",
    Role.ADMIN: "signage_admin",
}
ROLE_RANK = {Role.USER: 10, Role.MODERATOR: 20, Role.ADMIN: 30}


def ensure_role_groups() -> dict[str, Group]:
    return {
        role: Group.objects.get_or_create(name=group_name)[0]
        for role, group_name in ROLE_GROUPS.items()
    }


def get_user_role(user) -> str:
    if not getattr(user, "is_authenticated", False):
        return ""
    if user.is_superuser:
        return Role.ADMIN
    names = set(user.groups.values_list("name", flat=True))
    for role in (Role.ADMIN, Role.MODERATOR, Role.USER):
        if ROLE_GROUPS[role] in names:
            return role
    # Accounts created before roles were introduced receive least privilege.
    return Role.USER


def set_user_role(user, role: str) -> None:
    if role not in ROLE_GROUPS:
        raise ValueError("Неизвестная роль")
    groups = ensure_role_groups()
    user.groups.remove(*groups.values())
    user.groups.add(groups[role])
    # Custom Signage roles do not grant access to Django's technical admin.
    # Only a real superuser may retain that separate maintenance privilege.
    if not user.is_superuser and user.is_staff:
        user.is_staff = False
        user.save(update_fields=["is_staff"])


def role_at_least(user, minimum: str) -> bool:
    return ROLE_RANK.get(get_user_role(user), 0) >= ROLE_RANK[minimum]


def role_required(minimum: str):
    def decorator(view: Callable[..., HttpResponse]):
        @login_required
        @wraps(view)
        def wrapped(request: HttpRequest, *args, **kwargs):
            if not role_at_least(request.user, minimum):
                raise PermissionDenied("Недостаточно прав для этого раздела.")
            return view(request, *args, **kwargs)

        return wrapped

    return decorator


def permission_context(user) -> dict[str, object]:
    role = get_user_role(user)
    return {
        "current_role": role,
        "current_role_label": ROLE_LABELS.get(role, ""),
        "can_manage_operations": role_at_least(user, Role.MODERATOR),
        "can_manage_users": role_at_least(user, Role.ADMIN),
    }
