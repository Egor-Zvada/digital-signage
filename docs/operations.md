# Эксплуатация сервера

## Установка

На чистом Debian 13 LXC:

Сначала установите выданный вашим Microsoft CA сертификат сайта, соответствующий
закрытый ключ и публичную цепочку CA:

- `/etc/signage/tls/server.crt` — сертификат `signage.vve.local` и промежуточные сертификаты;
- `/etc/signage/tls/server.key` — закрытый ключ сайта;
- `/etc/signage/tls/ca.crt` — публичная цепочка доверия Microsoft CA.

Закрытый ключ самого центра сертификации не нужен. Установщик проверит срок,
имя сайта, совпадение ключа с сертификатом и доверие к цепочке перед установкой.

```bash
git clone https://github.com/Egor-Zvada/digital-signage.git /root/digital-signage
bash /root/digital-signage/deploy/lxc/install.sh
```

Установщик создаёт PostgreSQL, системного пользователя, каталоги, версионный релиз,
службы systemd и первого администратора. Существующие TLS-файлы не заменяются.
Если один из трёх файлов отсутствует или проверка не прошла, установка останавливается
с объяснением причины.
Начальный пароль хранится в `/root/signage-initial-admin.txt` и должен быть удалён
после смены пароля.

Публичный сертификат CA доступен до перенаправления по адресу
`http://signage.vve.local/signage-ca.crt`. CA нужен приложению
только для HTTPS; mTLS и клиентские сертификаты не используются. Состав файлов,
проверки и порядок перехода описаны в
[полном русском руководстве](user-guide-ru.md#11-собственный-ca-и-microsoft-ca).

В среде без своей PKI допустимо явно запустить установщик с
`SIGNAGE_GENERATE_LOCAL_CA=true`. Этот вариант создаёт отдельный локальный CA
только если все TLS-файлы ещё отсутствуют. В рабочей корпоративной среде используется
существующий Microsoft CA.

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
`/opt/signage/releases`, проверяет приложение и TLS, сохраняет `pg_dump` в
`/srv/signage/backups/postgres/pre-deploy-<релиз>.dump`, выполняет миграции,
собирает статику и переключает `/opt/signage/current`. Снимок всего LXC перед
обновлением остаётся полезным дополнительным способом восстановления.

Если после переключения не проходит проверка HTTPS или обязательной статики,
установщик возвращает предыдущую ссылку релиза и перезапускает службы.
Миграции базы и общая статика при этом автоматически не откатываются; при
несовместимой миграции база восстанавливается из указанной резервной копии.
