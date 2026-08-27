# Эксплуатация сервера

## Установка

На чистом Debian 13 LXC:

```bash
git clone https://github.com/Egor-Zvada/digital-signage.git /root/digital-signage
bash /root/digital-signage/deploy/lxc/install.sh
```

Установщик создаёт PostgreSQL, системного пользователя, каталоги, начальный
локальный TLS-сертификат, версионный релиз, службы systemd и первого администратора.
Начальный пароль хранится в `/root/signage-initial-admin.txt` и должен быть удалён
после смены пароля.

Начальный публичный сертификат Signage CA доступен до перенаправления по адресу
`http://signage.vve.local/signage-ca.crt`. Для production рекомендуется заменить
его серверным сертификатом существующего Microsoft CA. Этот CA нужен приложению
только для HTTPS; mTLS и клиентские сертификаты не используются. Состав файлов,
проверки и порядок перехода описаны в
[полном русском руководстве](user-guide-ru.md#11-собственный-ca-и-microsoft-ca).

## Проверка

```bash
systemctl status signage-web signage-worker nginx postgresql
curl --cacert /etc/signage/tls/ca.crt \
  --resolve signage.vve.local:443:127.0.0.1 \
  https://signage.vve.local/healthz
```

## Журналы

```bash
journalctl -u signage-web -f
journalctl -u signage-worker -f
journalctl -u nginx -f
```

## Резервное копирование

Ежедневный логический dump находится в `/srv/signage/backups/postgres`. Дополнительно
необходим nightly backup всего LXC через Proxmox Backup Server или `vzdump`.

Проверка таймера:

```bash
systemctl list-timers signage-backup.timer
systemctl start signage-backup.service
```

## Обновление

Повторный запуск актуального `deploy/lxc/install.sh` создаёт новый каталог в
`/opt/signage/releases`, выполняет миграции и атомарно меняет `/opt/signage/current`.
Перед обновлением рекомендуется snapshot LXC и отдельный `pg_dump`.
