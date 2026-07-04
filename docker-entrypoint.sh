#!/usr/bin/env sh
# Migrate, load bundled data on a fresh DB (idempotent, no-op once seeded), then
# start gunicorn. Port/workers come from gunicorn.conf.py (reads $PORT in Python).
set -e

python manage.py migrate --noinput

# bootstrap_seed catches per-dataset failures internally (a flaky source logs and
# retries next deploy) and still exits 0 — so a NON-zero exit here means bootstrap
# itself crashed or was killed mid-seed (e.g. OOM, or a DISK-FULL write failure — the
# most likely trigger as the Postgres volume fills). The old `|| true` swallowed that
# and started the site on half-loaded data with no signal. Fail loudly instead: a bad
# deploy stays out and the platform keeps the last healthy one serving complete data.
if ! python manage.py bootstrap_seed; then
    echo "FATAL: bootstrap_seed crashed or was killed mid-seed — refusing to start on partial data." >&2
    exit 1
fi

exec gunicorn config.wsgi
