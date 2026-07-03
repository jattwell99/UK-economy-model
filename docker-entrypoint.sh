#!/usr/bin/env sh
# Apply migrations, then start gunicorn. Binds $PORT (platforms set it; default 8000).
set -e

python manage.py migrate --noinput

exec gunicorn config.wsgi \
    --bind "0.0.0.0:${PORT:-8000}" \
    --workers "${WEB_CONCURRENCY:-3}" \
    --access-logfile - \
    --error-logfile -
