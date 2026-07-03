# Production image for the UK place-centric research engine.
# Builds a self-contained web container: Django + gunicorn + WhiteNoise static.
# Works on any Docker host (Fly.io, Railway, Render, a VPS).

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_DEBUG=0

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Collect static at build time (WhiteNoise manifest). No DB is touched here;
# a throwaway SECRET_KEY keeps settings importable during the build.
RUN DJANGO_SECRET_KEY=build-only DJANGO_DEBUG=0 python manage.py collectstatic --noinput

EXPOSE 8000
ENTRYPOINT ["./docker-entrypoint.sh"]
