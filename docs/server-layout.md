# Схема одного LXC

```text
nginx :443
  ├─ /admin, /api, /display -> signage-web (Unix socket)
  └─ /media                 -> X-Accel-Redirect

signage-web.service
  ├─ Django ASGI
  ├─ административная панель
  ├─ player API
  └─ manifest + heartbeat polling

signage-worker.service
  ├─ ffprobe (транскодирование пока не реализовано)
  ├─ проверка изображений
  ├─ Chromium screenshots
  └─ очистка завершённых фоновых заданий

PostgreSQL 17
  ├─ данные приложения
  ├─ задания worker
  └─ события публикации
```

## Каталоги production-системы

```text
/opt/signage/releases/<version>/
/opt/signage/current
/etc/signage/signage.env
/srv/signage/uploads/
/srv/signage/media/originals/
/srv/signage/media/renditions/
/srv/signage/media/thumbnails/
/srv/signage/site-snapshots/
/srv/signage/backups/postgres/
/var/lib/postgresql/17/main/
```

## Порты

- `443/tcp`: Pi и администраторы;
- `80/tcp`: только перенаправление на HTTPS;
- `22/tcp`: только административная сеть;
- PostgreSQL не слушает внешний TCP-интерфейс.

## Ресурсы

Для небольшого теста достаточно 4 vCPU и 4 ГБ RAM. Для рабочей библиотеки и
серверных Chromium-снимков рекомендуются 8 vCPU и 8–12 ГБ RAM. Медиадиск
рекомендуется увеличить минимум до 250 ГБ. Транскодирование 4K в текущей версии
не реализовано.
