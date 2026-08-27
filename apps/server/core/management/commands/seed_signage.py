from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import (
    BrandTheme,
    Channel,
    Playlist,
    PlaylistItem,
    Scene,
    Slogan,
    SloganSet,
    SyncGroup,
    WeatherSource,
    WorkerJob,
)

FULL_NAME = (
    "Областное государственное автономное учреждение дополнительного образования "
    "«Спортивная школа восточных видов единоборств Сахалинской области»"
)

SLOGANS = [
    "Дисциплина превращает движение в мастерство.",
    "Сильнее становится тот, кто умеет владеть собой.",
    "Уважение открывает тренировку. Ответственность ведёт к результату.",
    "Спокойный ум. Точная техника. Честный результат.",
    "Мастерство видно в точности, характер — в отношении.",
    "Точность приходит с повторением. Уверенность — с опытом.",
    "Сахалинский характер: выдержка, уважение, движение вперёд.",
    "Прогресс складывается из точных повторений.",
    "ОГАУ ДО «СШ ВВЕ»: растём в мастерстве, сохраняем уважение.",
]

SCENES = [
    (
        "Официальная заставка",
        Scene.SceneType.IDENTITY,
        12,
        {
            "kicker": "Сахалинская область",
            "subtitle": "Каратэ · Фехтование · Дисциплина · Уважение",
        },
    ),
    (
        "Сахалинское время",
        Scene.SceneType.CLOCK,
        12,
        {"headerSubtitle": "Каратэ · Фехтование · Сахалинская область"},
    ),
    (
        "Погода в Южно-Сахалинске",
        Scene.SceneType.WEATHER,
        12,
        {"headerSubtitle": "Погода · Южно-Сахалинск"},
    ),
    (
        "Время и погода",
        Scene.SceneType.CLOCK_WEATHER,
        15,
        {"headerSubtitle": "Время и погода · Южно-Сахалинск"},
    ),
    ("Фразы школы", Scene.SceneType.SLOGAN, 12, {"subtitle": "Тренируйся. Уважай. Расти."}),
    (
        "Объявление",
        Scene.SceneType.ANNOUNCEMENT,
        15,
        {
            "kicker": "Информация",
            "title": "Добро пожаловать!",
            "text": "Текст объявления можно изменить в настройках сцены.",
        },
    ),
]


class Command(BaseCommand):
    help = "Создаёт начальную тему, сцены, фразы, погоду, плейлист и канал"

    @transaction.atomic
    def handle(self, *args, **options):
        theme, _ = BrandTheme.objects.get_or_create(
            name="Круг движения",
            defaults={
                "short_name": "ОГАУ ДО «СШ ВВЕ»",
                "full_name": FULL_NAME,
                "is_default": True,
                "logo_path": "brand/school-logo.png",
                "colors": {
                    "blue": "#1E4B98",
                    "dark": "#102F65",
                    "cyan": "#50B8E8",
                    "red": "#D93B43",
                    "white": "#FFFFFF",
                    "mist": "#EFF6FC",
                },
                "settings": {"name": "Круг движения", "canvas": "1920x1080"},
            },
        )
        BrandTheme.objects.exclude(pk=theme.pk).update(is_default=False)

        slogan_set, _ = SloganSet.objects.get_or_create(
            name="Основные фразы",
            defaults={
                "playback_mode": SloganSet.PlaybackMode.SEQUENTIAL,
                "default_duration_seconds": 10,
            },
        )
        for index, text in enumerate(SLOGANS, start=1):
            Slogan.objects.get_or_create(
                slogan_set=slogan_set,
                text=text,
                defaults={"enabled": True, "position": index * 10},
            )

        weather, _ = WeatherSource.objects.get_or_create(
            name="Южно-Сахалинск",
            defaults={
                "provider": "open_meteo",
                "enabled": True,
                "latitude": Decimal("46.95910"),
                "longitude": Decimal("142.73800"),
                "timezone_name": "Asia/Sakhalin",
                "update_interval_minutes": 15,
                "stale_after_minutes": 180,
            },
        )
        if not WorkerJob.objects.filter(
            job_type=WorkerJob.JobType.WEATHER,
            status__in=[WorkerJob.Status.QUEUED, WorkerJob.Status.RUNNING],
            payload__sourceId=weather.id,
        ).exists():
            WorkerJob.objects.create(
                job_type=WorkerJob.JobType.WEATHER,
                payload={"sourceId": weather.id},
            )

        playlist, _ = Playlist.objects.get_or_create(
            name="Основной экран",
            defaults={"description": "Фирменные сцены, объявления и медиаконтент"},
        )
        for index, (name, scene_type, duration, config) in enumerate(SCENES, start=1):
            scene, _ = Scene.objects.get_or_create(
                name=name,
                defaults={
                    "scene_type": scene_type,
                    "enabled": True,
                    "theme": theme,
                    "slogan_set": slogan_set,
                    "weather_source": weather,
                    "config": config,
                },
            )
            PlaylistItem.objects.get_or_create(
                playlist=playlist,
                scene=scene,
                defaults={
                    "item_type": PlaylistItem.ItemType.SCENE,
                    "asset": None,
                    "title": name,
                    "enabled": True,
                    "position": index * 10,
                    "duration_seconds": duration,
                },
            )

        sync_group, _ = SyncGroup.objects.get_or_create(name="Основная группа")
        channel, _ = Channel.objects.get_or_create(
            slug="main",
            defaults={
                "name": "Основной канал",
                "enabled": True,
                "timezone_name": "Asia/Sakhalin",
                "playlist": playlist,
                "theme": theme,
                "slogan_set": slogan_set,
                "weather_source": weather,
                "overlay_config": {"preset": "minimal"},
            },
        )
        self.stdout.write(self.style.SUCCESS(f"Начальные данные готовы: {channel}, {sync_group}"))
