#!/usr/bin/env sh
# Migrate, load bundled data on a fresh DB (idempotent, no-op once seeded), then
# start gunicorn. Port/workers come from gunicorn.conf.py (reads $PORT in Python).
set -e

python manage.py migrate --noinput
python manage.py bootstrap_seed || true

exec gunicorn config.wsgi
