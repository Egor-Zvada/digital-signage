from django.db import migrations


ROLE_GROUPS = ("signage_user", "signage_moderator", "signage_admin")


def create_roles(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    User = apps.get_model("auth", "User")
    groups = {name: Group.objects.get_or_create(name=name)[0] for name in ROLE_GROUPS}
    for user in User.objects.all().iterator():
        if user.is_superuser or user.is_staff:
            user.groups.add(groups["signage_admin"])
        elif not user.groups.filter(name__in=ROLE_GROUPS).exists():
            user.groups.add(groups["signage_user"])


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]
    operations = [migrations.RunPython(create_roles, migrations.RunPython.noop)]
