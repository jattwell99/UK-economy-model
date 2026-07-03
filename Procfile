# For buildpack-based platforms (Railway/Render/Heroku) that don't use the Dockerfile.
# When a platform builds from the Dockerfile instead, migrations run via
# docker-entrypoint.sh and this file is ignored.
release: python manage.py migrate --noinput
web: gunicorn config.wsgi --bind 0.0.0.0:$PORT --workers 3
