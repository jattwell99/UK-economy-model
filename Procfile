# For buildpack-based platforms (Railway/Render/Heroku) that don't use the Dockerfile.
# Bind/port come from gunicorn.conf.py (reads $PORT in Python) — no shell expansion,
# which avoids the "'$PORT' is not a valid port number" failure.
release: python manage.py migrate --noinput
web: gunicorn config.wsgi
