#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "Запустите install.sh от root" >&2
    exit 1
fi

repo_url=${SIGNAGE_REPOSITORY_URL:-https://github.com/Egor-Zvada/digital-signage.git}
repo_dir=/opt/signage/repository.git
release_root=/opt/signage/releases
environment_file=/etc/signage/signage.env

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
    ca-certificates chromium ffmpeg git nginx openssl postgresql postgresql-client \
    python3 python3-venv python3-pip python3-dev build-essential libpq-dev curl

if ! id signage >/dev/null 2>&1; then
    useradd --system --home-dir /srv/signage --create-home --shell /usr/sbin/nologin signage
fi

install -d -m 0755 /opt/signage "$release_root"
install -d -o signage -g signage -m 0750 \
    /srv/signage/uploads \
    /srv/signage/media/originals \
    /srv/signage/media/renditions \
    /srv/signage/media/thumbnails \
    /srv/signage/site-snapshots \
    /srv/signage/backups/postgres
install -d -o signage -g www-data -m 0750 /srv/signage/static
install -d -o root -g signage -m 0750 /etc/signage

if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='signage'" | grep -q 1; then
    runuser -u postgres -- createuser signage
fi
if ! runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='signage'" | grep -q 1; then
    runuser -u postgres -- createdb --owner=signage signage
fi

if [[ ! -f "$environment_file" ]]; then
    secret_key=$(openssl rand -hex 48)
    cat >"$environment_file" <<EOF
SIGNAGE_DEBUG=false
SIGNAGE_SECRET_KEY=$secret_key
SIGNAGE_ALLOWED_HOSTS=signage.vve.local,192.168.110.222
SIGNAGE_CSRF_TRUSTED_ORIGINS=https://signage.vve.local,https://192.168.110.222
SIGNAGE_DATABASE_URL=postgresql:///signage?host=/var/run/postgresql
SIGNAGE_TIME_ZONE=Asia/Sakhalin
SIGNAGE_STATIC_ROOT=/srv/signage/static
SIGNAGE_MEDIA_ROOT=/srv/signage/media
SIGNAGE_UPLOAD_ROOT=/srv/signage/uploads
SIGNAGE_SITE_SNAPSHOT_ROOT=/srv/signage/site-snapshots
EOF
    chown root:signage "$environment_file"
    chmod 0640 "$environment_file"
fi

tls_dir=/etc/signage/tls
install -d -o root -g www-data -m 0750 "$tls_dir"
if [[ ! -f "$tls_dir/ca.crt" || ! -f "$tls_dir/server.crt" ]]; then
    openssl genpkey -algorithm ED25519 -out "$tls_dir/ca.key"
    openssl req -x509 -new -key "$tls_dir/ca.key" -sha256 -days 3650 \
        -subj "/CN=VVE Signage Local CA" -out "$tls_dir/ca.crt"
    openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$tls_dir/server.key"
    openssl req -new -key "$tls_dir/server.key" -subj "/CN=signage.vve.local" \
        -out "$tls_dir/server.csr"
    openssl x509 -req -in "$tls_dir/server.csr" -CA "$tls_dir/ca.crt" \
        -CAkey "$tls_dir/ca.key" -CAcreateserial -days 825 -sha256 \
        -extfile "$(dirname "$0")/tls-san.cnf" -out "$tls_dir/server.crt"
    rm -f "$tls_dir/server.csr" "$tls_dir/ca.srl"
    chmod 0600 "$tls_dir/ca.key" "$tls_dir/server.key"
    chmod 0644 "$tls_dir/ca.crt" "$tls_dir/server.crt"
    chown root:www-data "$tls_dir/server.key"
fi

if [[ ! -d "$repo_dir" ]]; then
    git clone --mirror "$repo_url" "$repo_dir"
else
    git --git-dir="$repo_dir" remote update --prune
fi

commit=$(git --git-dir="$repo_dir" rev-parse refs/heads/main)
release_id="$(date +%Y%m%d%H%M%S)-${commit:0:10}"
release_dir="$release_root/$release_id"
install -d -o signage -g signage -m 0755 "$release_dir"
git --git-dir="$repo_dir" archive "$commit" | tar -x -C "$release_dir"
chown -R signage:signage "$release_dir"

runuser -u signage -- python3 -m venv "$release_dir/.venv"
runuser -u signage -- "$release_dir/.venv/bin/python" -m pip install --upgrade pip
runuser -u signage -- "$release_dir/.venv/bin/python" -m pip install "$release_dir"

set -a
source "$environment_file"
set +a
runuser -u signage --preserve-environment -- "$release_dir/.venv/bin/python" \
    "$release_dir/apps/server/manage.py" migrate --noinput
runuser -u signage --preserve-environment -- "$release_dir/.venv/bin/python" \
    "$release_dir/apps/server/manage.py" collectstatic --noinput
runuser -u signage --preserve-environment -- "$release_dir/.venv/bin/python" \
    "$release_dir/apps/server/manage.py" seed_signage

ln -sfn "$release_dir" /opt/signage/current.new
mv -Tf /opt/signage/current.new /opt/signage/current

install -o root -g root -m 0644 "$release_dir/deploy/lxc/signage-web.service" /etc/systemd/system/signage-web.service
install -o root -g root -m 0644 "$release_dir/deploy/lxc/signage-worker.service" /etc/systemd/system/signage-worker.service
install -o root -g root -m 0644 "$release_dir/deploy/lxc/signage-backup.service" /etc/systemd/system/signage-backup.service
install -o root -g root -m 0644 "$release_dir/deploy/lxc/signage-backup.timer" /etc/systemd/system/signage-backup.timer
install -o root -g root -m 0644 "$release_dir/deploy/lxc/signage-maintenance.service" /etc/systemd/system/signage-maintenance.service
install -o root -g root -m 0644 "$release_dir/deploy/lxc/signage-maintenance.timer" /etc/systemd/system/signage-maintenance.timer
install -o root -g root -m 0755 "$release_dir/deploy/lxc/signage-backup" /usr/local/sbin/signage-backup
install -o root -g root -m 0644 "$release_dir/deploy/lxc/nginx-signage.conf" /etc/nginx/sites-available/signage
ln -sfn /etc/nginx/sites-available/signage /etc/nginx/sites-enabled/signage
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable --now signage-web.service signage-worker.service signage-backup.timer signage-maintenance.timer nginx
systemctl restart signage-web.service signage-worker.service nginx

if ! runuser -u signage --preserve-environment -- "$release_dir/.venv/bin/python" \
    "$release_dir/apps/server/manage.py" shell -c \
    "from django.contrib.auth import get_user_model; raise SystemExit(0 if get_user_model().objects.filter(is_superuser=True).exists() else 1)"; then
    admin_password=$(openssl rand -base64 24 | tr -d '/+=' | head -c 20)
    SIGNAGE_INITIAL_ADMIN_PASSWORD="$admin_password" \
    runuser -u signage --preserve-environment -- "$release_dir/.venv/bin/python" \
        "$release_dir/apps/server/manage.py" shell -c \
        "import os; from django.contrib.auth import get_user_model; get_user_model().objects.create_superuser('admin', '', os.environ['SIGNAGE_INITIAL_ADMIN_PASSWORD'])"
    printf 'Пользователь: admin\nПароль: %s\n' "$admin_password" >/root/signage-initial-admin.txt
    chmod 0600 /root/signage-initial-admin.txt
fi

health_ok=0
for _ in $(seq 1 30); do
    if curl --fail --silent --show-error --cacert "$tls_dir/ca.crt" \
        --resolve signage.vve.local:443:127.0.0.1 https://signage.vve.local/healthz; then
        health_ok=1
        break
    fi
    sleep 1
done
if [[ $health_ok -ne 1 ]]; then
    echo "Веб-сервис не ответил за 30 секунд." >&2
    systemctl --no-pager --full status signage-web nginx >&2 || true
    exit 1
fi
echo
echo "Digital Signage установлен: https://signage.vve.local/"
echo "Корневой сертификат: http://signage.vve.local/signage-ca.crt"
echo "Начальные данные администратора: /root/signage-initial-admin.txt"
