# ManzilCart — production deployment, backups and costs

This document is the operational companion to `progress.md`. It targets a small,
single-region deployment (UAE) that is cheap to run but keeps the parts that are hard
to retrofit — HTTPS, PostgreSQL, Redis, ASGI/WebSockets, durable notifications and
backups — in place from day one.

> **Status:** the application and its tests run locally against PostgreSQL 17.5. The
> steps below have not been executed against a real production host. Provider
> credentials (WhatsApp/SMS/email) are still required; until they exist, keep
> `NOTIFICATIONS_MODE=mock` and accept that OTPs are visible only through the
> development console / Admin.

---

## 1. Target topology

```
Internet
  │  HTTPS (443)
  ▼
Nginx  ── static files (Whitenoise already hashed, or Nginx alias)
  │  HTTP/1.1 + WebSocket upgrade (127.0.0.1:8000)
  ▼
Daphne (ASGI, systemd)
  ├── PostgreSQL 17
  ├── Redis (cache + Channels layer)
  └── cron/systemd timers
        ├── process_notifications      (every minute)
        └── process_auction_deadlines  (every minute)
```

A single 2 vCPU / 4 GB VM is enough for launch. If the budget allows, move PostgreSQL
to a managed service so point-in-time recovery is someone else's job.

Python 3.12, Django 6.1, Daphne, Channels, psycopg 3. Nginx terminates TLS; Django
trusts `X-Forwarded-Proto` (`SECURE_PROXY_SSL_HEADER` is configured in production).

---

## 2. Production environment

Start from `.env.example`. The minimum for `DJANGO_ENV=production`:

```dotenv
DJANGO_ENV=production
DJANGO_DEBUG=false
DJANGO_SECRET_KEY=<64-byte random value>
DJANGO_ALLOWED_HOSTS=manzilcart.ae,www.manzilcart.ae
DJANGO_CSRF_TRUSTED_ORIGINS=https://manzilcart.ae,https://www.manzilcart.ae

DB_ENGINE=postgresql
DB_NAME=manzilcart
DB_USER=manzilcart_app          # NOT the postgres superuser
DB_PASSWORD=<strong password>
DB_HOST=127.0.0.1
DB_PORT=5432

REDIS_URL=redis://127.0.0.1:6379/0
NOTIFICATIONS_MODE=mock         # switch to live only after provider credentials exist

EMAIL_HOST=smtp.eu-west-1.amazonaws.com   # or another SMTP provider
EMAIL_PORT=587
EMAIL_HOST_USER=<smtp user>
EMAIL_HOST_PASSWORD=<smtp password>
EMAIL_USE_TLS=true
DEFAULT_FROM_EMAIL=ManzilCart <no-reply@manzilcart.ae>
ADMINS_EMAILS=ops@manzilcart.ae

DJANGO_SECURE_SSL_REDIRECT=true
DJANGO_SECURE_HSTS_SECONDS=31536000
```

Notes:

- `DJANGO_SECRET_KEY` must be long, random and backed up with the rest of the secrets.
  Rotating it invalidates OTP hashes and sessions.
- Do not use `DB_USER=postgres` in production. Create a dedicated role that owns the
  database only:
  ```sql
  CREATE ROLE manzilcart_app LOGIN PASSWORD '...';
  CREATE DATABASE manzilcart OWNER manzilcart_app;
  ```
- `EMAIL_HOST` is required for `manage.py check --deploy` to pass. Without it the
  development console mailer is selected and Django flags `mail.E001`.
- `REDIS_URL` is required in production: LocMem throttles and the in-memory channel
  layer are per-process and would break OTP limits and chat broadcasts across workers.
- Keep `NOTIFICATIONS_MODE=mock` until WhatsApp/SMS credentials and approved templates
  exist. The live backends fail loudly rather than pretending a message was sent.

`check --deploy` should be run **with the production environment loaded**:

```bash
DJANGO_ENV=production ... .venv/bin/python manage.py check --deploy
```

The development `.env` intentionally still reports warnings (DEBUG, HTTP cookies,
console email); that is expected and is not a production failure. While
`NOTIFICATIONS_MODE=mock`, production also reports `manzilcart.W002`: the provider
integration is deliberately the last launch gate. The check is clean once a real
mailer and `NOTIFICATIONS_MODE=live` are configured.

---

## 3. First deploy

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin manzilcart
sudo mkdir -p /srv/manzilcart /var/log/manzilcart
sudo chown manzilcart:manzilcart /srv/manzilcart /var/log/manzilcart

sudo -u manzilcart python3.12 -m venv /srv/manzilcart/.venv
sudo -u manzilcart /srv/manzilcart/.venv/bin/pip install -r requirements.txt

# copy the code to /srv/manzilcart/app, then:
cd /srv/manzilcart/app
sudo -u manzilcart /srv/manzilcart/.venv/bin/python manage.py migrate
sudo -u manzilcart /srv/manzilcart/.venv/bin/python manage.py collectstatic --noinput
sudo -u manzilcart /srv/manzilcart/.venv/bin/python manage.py createsuperuser
sudo -u manzilcart /srv/manzilcart/.venv/bin/python manage.py check --deploy
```

Media uploads are written to `MEDIA_ROOT` (default `app/media`). Either back that
directory up (section 5) or mount object storage later; the storage backend is already
isolated behind `STORAGES`.

---

## 4. systemd units

### Daphne

`/etc/systemd/system/manzilcart.service`

```ini
[Unit]
Description=ManzilCart ASGI (Daphne)
After=network.target postgresql.service redis-server.service
Requires=postgresql.service redis-server.service

[Service]
Type=simple
User=manzilcart
Group=manzilcart
WorkingDirectory=/srv/manzilcart/app
EnvironmentFile=/srv/manzilcart/app/.env
ExecStart=/srv/manzilcart/.venv/bin/daphne -b 127.0.0.1 -p 8000 Manzilcart.asgi:application
Restart=always
RestartSec=5
# Do not expose the app directly; only Nginx talks to 127.0.0.1:8000.

[Install]
WantedBy=multi-user.target
```

### Notification worker

`/etc/systemd/system/manzilcart-notifications.service`

```ini
[Unit]
Description=ManzilCart notification retry worker
After=network.target postgresql.service

[Service]
Type=oneshot
User=manzilcart
WorkingDirectory=/srv/manzilcart/app
EnvironmentFile=/srv/manzilcart/app/.env
ExecStart=/srv/manzilcart/.venv/bin/python manage.py process_notifications
```

`/etc/systemd/system/manzilcart-notifications.timer`

```ini
[Unit]
Description=Run the ManzilCart notification worker every minute

[Timer]
OnBootSec=30s
OnUnitActiveSec=60s
AccuracySec=10s
Persistent=true

[Install]
WantedBy=timers.target
```

### Auction deadline worker

Copy the two files above, replacing `notifications` with `auctions` and the command
with:

```
ExecStart=/srv/manzilcart/.venv/bin/python manage.py process_auction_deadlines
```

Enable both timers:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now manzilcart.service
sudo systemctl enable --now manzilcart-notifications.timer manzilcart-auctions.timer
```

Both commands are idempotent and re-check state under locks, so overlapping timer runs
are safe (see the module docstrings).

---

## 5. Nginx and HTTPS

`/etc/nginx/sites-available/manzilcart`

```nginx
upstream manzilcart_app {
    server 127.0.0.1:8000;
}

server {
    listen 80;
    server_name manzilcart.ae www.manzilcart.ae;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name manzilcart.ae www.manzilcart.ae;

    ssl_certificate     /etc/letsencrypt/live/manzilcart.ae/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/manzilcart.ae/privkey.pem;

    client_max_body_size 6m;   # matches DATA_UPLOAD_MAX_MEMORY_SIZE

    location /static/ {
        alias /srv/manzilcart/app/staticfiles/;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    location /media/ {
        alias /srv/manzilcart/app/media/;
        expires 7d;
        add_header Cache-Control "public";
    }

    location / {
        proxy_pass http://manzilcart_app;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 65s;
    }

    # WebSocket chat. The Channels consumer is mounted at /ws/.
    location /ws/ {
        proxy_pass http://manzilcart_app;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
    }
}
```

Then:

```bash
sudo ln -s /etc/nginx/sites-available/manzilcart /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d manzilcart.ae -d www.manzilcart.ae
```

HSTS is already sent by Django (`SECURE_HSTS_SECONDS`, include-subdomains, preload).
Only enable preload once every subdomain is HTTPS-only. Check:

```bash
curl -I https://manzilcart.ae/
python manage.py check --deploy
```

Firewall: allow 22, 80, 443 only. PostgreSQL and Redis listen on loopback. SSH keys
only; disable password login. `healthz` (`/healthz`) is the external health check.

---

## 6. Backups (verified procedure)

A backup is only useful if it can be restored. The following drill was executed on
2026-10-19 against the local PostgreSQL 17.5 instance: dump → restore into a scratch
database → compare row counts (`django_migrations` 32, seeded service areas 32) →
drop the scratch database. It succeeded.

### Nightly database dump

```bash
#!/usr/bin/env bash
set -euo pipefail
umask 077
STAMP=$(date -u +%Y%m%dT%H%M%SZ)
DEST=/var/backups/manzilcart
mkdir -p "$DEST"
pg_dump --format=custom --no-owner --no-acl \
  --host=127.0.0.1 --username=manzilcart_backup \
  --dbname=manzilcart > "$DEST/manzilcart-$STAMP.dump"
find "$DEST" -name 'manzilcart-*.dump' -mtime +14 -delete
```

Run from root cron or a systemd timer at 02:00 UTC. Copy dumps **off the host** the
same night (object storage or another region). Keep at least:

- 14 daily dumps,
- 4 weekly dumps,
- 3 monthly dumps,
- and the encryption key/secret store separately.

Use a dedicated `manzilcart_backup` role with `CONNECT` and `SELECT` only; it does not
need write access. Encrypt dumps at rest if the storage bucket is not private.

### Media

```bash
rsync -a --delete /srv/manzilcart/app/media/ backup-host:/backups/manzilcart-media/
```

### Restore drill (run quarterly)

```bash
createdb -h 127.0.0.1 -U postgres manzilcart_restore_test
pg_restore -h 127.0.0.1 -U postgres -d manzilcart_restore_test \
  --no-owner --no-acl /var/backups/manzilcart/manzilcart-YYYYMMDD.dump
psql -h 127.0.0.1 -U postgres -d manzilcart_restore_test \
  -c "select count(*) from django_migrations; select count(*) from locations_servicearea;"
dropdb -h 127.0.0.1 -U postgres manzilcart_restore_test
```

Then point a staging `.env` at the restored database and load the landing page,
marketplace home, a listing, a conversation and Admin. A dump that restores but cannot
serve requests is not a backup.

For point-in-time recovery, enable PostgreSQL WAL archiving (or use a managed service).
That is optional at launch but should be decided before the first real transaction.

---

## 7. Cost estimate (launch scale)

Ranges are indicative and exclude VAT; verify current provider prices. They assume one
small VM, low traffic and no paid marketing.

| Item | Low-cost option | Managed option |
|---|---|---|
| App VM (2 vCPU / 4 GB, UAE region) | $18–35 / month | — |
| PostgreSQL | on the same VM (included) | $15–40 / month |
| Redis | on the same VM (included) | $10–20 / month |
| Object storage + backups | $1–5 / month | $5–15 / month |
| Domain | ~$10–20 / year | ~$10–20 / year |
| Transactional email (SES/SMTP) | ~$0.10 per 1,000 emails | same |
| WhatsApp OTP conversations | provider per-conversation fee (region/template dependent; budget a few US cents per verification) | same |
| SMS fallback | provider per-message fee | same |
| TLS certificate | Let's Encrypt, free | free |
| **Indicative total** | **≈ $20–45 / month** + messaging | **≈ $50–100 / month** + messaging |

Cost control: no Celery/broker, no paid search service, no separate moderation product.
The notification queue is a table and the workers are two cron-driven management
commands. Revisit only when volume or latency makes that painful.

---

## 8. Deploy checklist

- [ ] Production `.env` loaded; `DJANGO_ENV=production`, `DEBUG=false`.
- [ ] Secret key generated and stored in a password manager.
- [ ] Dedicated PostgreSQL role/database created; superuser not used by the app.
- [ ] Redis reachable from the app; `REDIS_URL` set.
- [ ] `migrate` and `collectstatic` run.
- [ ] `check --deploy` clean (email provider configured; `NOTIFICATIONS_MODE=live`).
- [ ] Daphne enabled and healthy at `/healthz`.
- [ ] Both worker timers enabled (`systemctl list-timers`).
- [ ] Nginx HTTPS + WebSocket upgrade tested from a browser (chat sends and receives).
- [ ] `createsuperuser` completed; staff accounts use strong passwords and 2FA at the
      proxy/SSO layer if available.
- [ ] Nightly database dump + media sync copied off-host; one restore drill passed.
- [ ] Live messaging only after credentials and approved templates exist.
- [ ] Desktop and mobile smoke test: landing, switch emirate, register, list, moderate,
      chat, offer, accept deal, auction bid/confirm.

---

## 9. Known production gaps

1. WhatsApp/SMS live backends are guarded stubs. Email is implemented through Django
   6.1 `MAILERS` and needs SMTP credentials.
2. Media is local disk. Move to object storage when more than one app server exists.
3. No WAL/PITR unless a managed PostgreSQL service or archive_command is configured.
4. Notification workers are cron/timer driven; there is no queue dashboard beyond
   Django Admin's read-only delivery log and manual retry action.
5. Database dumps contain personal data (phone numbers, message bodies). Restrict
   access, encrypt at rest, and document retention.
