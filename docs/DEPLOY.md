# Deploying the UK place-centric research engine

The app is a standard Django + PostgreSQL service. It ships a `Dockerfile` that
builds a self-contained web container (gunicorn + WhiteNoise for static files),
so it deploys to any Docker host. Two low-friction paths are documented here:
**Fly.io** and **Railway**. Render or a plain VPS work the same way.

## What the container does

- `Dockerfile` installs deps and runs `collectstatic` at build time.
- `docker-entrypoint.sh` runs `migrate` on boot, then starts gunicorn on `$PORT`
  (default 8000).
- Static files are served by WhiteNoise (no nginx/CDN needed).

## Configuration (environment variables)

| Variable | Required | Notes |
|----------|----------|-------|
| `DJANGO_SECRET_KEY` | **yes** | Long random string. `python -c "import secrets;print(secrets.token_urlsafe(50))"` |
| `DATABASE_URL` | **yes** | `postgres://user:pass@host:5432/db`. Injected automatically by Fly/Railway Postgres. Takes precedence over the `POSTGRES_*` parts. |
| `DJANGO_DEBUG` | no | Defaults off in the image (`0`). Never set to `1` in production. |
| `DJANGO_ALLOWED_HOSTS` | no | Comma-separated. `*.fly.dev` / Railway domains are added automatically (see below). Add a custom domain here. |
| `DJANGO_CSRF_TRUSTED_ORIGINS` | no | Extra full origins (`https://example.org`) if you use a custom domain. |
| `DJANGO_SECURE_SSL_REDIRECT` | no | Default on in production; set `0` only if terminating TLS yourself. |
| `DJANGO_SECURE_HSTS_SECONDS` | no | Default `0` (off). Set e.g. `31536000` once you're sure the site is HTTPS-only. |
| `WEB_CONCURRENCY` | no | gunicorn workers (default 3). |
| `NOMIS_API_KEY` | no | Nomis labour-market API key, passed as `&uid=`. The API works keyless but rate-limits; recommended for the large claimant-count history pull on first deploy. |
| `HPI_EDITION` | no | UK HPI edition to load (default `2026-04`). |

`ALLOWED_HOSTS` / CSRF are handled for you: `settings.py` appends
`"<FLY_APP_NAME>.fly.dev"` and Railway's `RAILWAY_PUBLIC_DOMAIN` at runtime, and
trusts `https://` for every non-local host.

---

## Fly.io

```bash
# 1. Install flyctl and log in
curl -L https://fly.io/install.sh | sh
fly auth login

# 2. From the repo root — edit `app` in fly.toml first if you want a different name
fly launch --no-deploy --copy-config --name uk-economy-model --region lhr

# 3. Secrets + database
fly secrets set DJANGO_SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(50))")
fly postgres create --name uk-economy-db --region lhr
fly postgres attach uk-economy-db          # sets DATABASE_URL automatically

# 4. Deploy (build image, run migrations on boot)
fly deploy

# 5. One-off data load (dimensions + boundaries). Upload your ONS workbooks first,
#    or run these locally against the same DATABASE_URL (see "Loading data").
fly ssh console -C "python manage.py seed_v1 --dimensions --geography"

# 6. Admin user
fly ssh console -C "python manage.py createsuperuser"

# Open it
fly open            # https://uk-economy-model.fly.dev/places/
```

## Railway

```bash
# 1. Install the CLI and log in
npm i -g @railway/cli && railway login

# 2. New project + Postgres plugin (injects DATABASE_URL)
railway init
railway add --plugin postgresql

# 3. Secret
railway variables set DJANGO_SECRET_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(50))")

# 4. Deploy (Railway builds from the Dockerfile; migrations run via the entrypoint)
railway up

# 5. Data + admin (run against the deployed service)
railway run python manage.py seed_v1 --dimensions --geography
railway run python manage.py createsuperuser
```

Railway auto-detects the `Dockerfile` and ignores the `Procfile`. The `Procfile`
is there only for buildpack-based platforms (Render/Heroku).

---

## Loading data

**Self-seeding (default):** the ONS workbooks are bundled in `seed_data/gva/`, and
`docker-entrypoint.sh` runs `python manage.py bootstrap_seed` on boot. On a fresh
(empty) database it loads dimensions, geography and all observations; once data
exists it no-ops, so it's safe on every boot. A brand-new deploy therefore
populates itself with no shell access. (If you host with a custom start command
that overrides the entrypoint, include `python manage.py bootstrap_seed` in it.)

**Fail-loudly on a broken seed (why the entrypoint has no `|| true`):**
`bootstrap_seed` catches *per-dataset* failures internally — a flaky source (ONS /
Nomis outage) is logged and retried on the next deploy, and the command still exits 0
so one bad source never downs the site. A **non-zero exit therefore means the whole
seed crashed or was killed mid-run**, so the entrypoint does **not** swallow it: it
prints `FATAL: …` and exits non-zero, the deploy fails visibly, and the platform keeps
the last healthy deploy serving *complete* data rather than cutting over to a
half-loaded one. **The most likely trigger for this FATAL path is a DISK-FULL write
failure during seed** — the append-only design grows the Postgres volume every refresh
(at ~79% as of the 2025 GVA refresh), so when the disk ceiling is hit the deploy
degrades *safely and visibly* instead of silently stranding partial data. If a deploy
fails with the `FATAL` line, check the volume usage first.

**Manual load** — to populate it yourself (same commands as local — see the
README), run against the hosted database:

```bash
python manage.py seed_v1 --dimensions --geography     # dimensions + LAD/WPC boundaries
python manage.py ingest_gva --path data/gva/
python manage.py ingest_population --path data/gva/populationestimatesbylocalauthority.xlsx --vintage 2020-06-mye
python manage.py derive_per_head
```

The ONS workbooks are **not** committed (gitignored `data/`). Either upload them
to the host before ingesting, or run the ingest commands locally with
`DATABASE_URL` pointed at the hosted Postgres — the fastest way to seed a remote
DB from your own machine:

```bash
export DATABASE_URL="postgres://…"    # the hosted database URL
python manage.py migrate
python manage.py seed_v1 --dimensions --geography
python manage.py ingest_gva --path data/gva/
python manage.py ingest_population --path data/gva/populationestimatesbylocalauthority.xlsx --vintage 2020-06-mye
python manage.py derive_per_head
```

## Creating an admin user

The Django admin is at `/admin/` and needs a superuser.

- **Locally / interactively:** `python manage.py createsuperuser`.
- **On a deployed box (no prompt):** set the credentials as env vars and run the
  idempotent `seed_admin` command — safe to re-run (it just resets the password):

  ```bash
  # Railway: set these in the web service Variables, then:
  railway run python manage.py seed_admin
  # or Fly:
  fly ssh console -C "python manage.py seed_admin"
  ```

  Reads `DJANGO_SUPERUSER_USERNAME` (default `admin`), `DJANGO_SUPERUSER_EMAIL`
  (optional), `DJANGO_SUPERUSER_PASSWORD` (required). You can also pass
  `--username/--email/--password` directly.

## Notes

- **Migrations on boot:** the entrypoint runs `migrate` each start — fine for a
  single instance. If you scale to multiple machines, move migration to a
  release step (Fly `[deploy] release_command`, Railway/Render release phase) to
  avoid concurrent runs.
- **Geography edition:** `seed_v1 --geography` currently loads the LAD **December
  2019** set (to match the 2019 GVA edition) plus the July 2024 WPCs. Swap the
  `GEO_SOURCES["LAD"]` URL for later phases.
- **`check --deploy`:** clean apart from HSTS (off by default) and the
  SECRET_KEY warning (satisfied once you set a real key).
