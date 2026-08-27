from .models import BrandTheme


def brand(_request):
    theme = BrandTheme.objects.filter(is_default=True).first()
    return {
        "brand_theme": theme,
        "brand_short_name": theme.short_name if theme else "ОГАУ ДО «СШ ВВЕ»",
    }
