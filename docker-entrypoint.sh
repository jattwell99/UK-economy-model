#!/usr/bin/env sh
# Apply migrations, then start gunicorn. Bind/port/workers come from
# gunicorn.conf.py (which reads $PORT in Python), so no shell expansion is needed.
set -e

python manage.py migrate --noinput

exec gunicorn config.wsgi
