from .models import BrandTheme
from .permissions import permission_context


def brand(request):
    theme = BrandTheme.objects.filter(is_default=True).first()
    context = {
        "brand_theme": theme,
        "brand_short_name": theme.short_name if theme else "ОГАУ ДО «СШ ВВЕ»",
    }
    if request.user.is_authenticated:
        context.update(permission_context(request.user))
    return context
