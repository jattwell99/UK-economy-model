"""
Create or update a Django superuser non-interactively (idempotent).

Locally the interactive `python manage.py createsuperuser` is simplest. This
command exists for places you can't type at a prompt — a deployed container,
CI, a one-off `railway run`. It reads credentials from the environment:

    DJANGO_SUPERUSER_USERNAME   (default "admin")
    DJANGO_SUPERUSER_EMAIL      (optional)
    DJANGO_SUPERUSER_PASSWORD   (required)

    python manage.py seed_admin

Re-running updates the existing user's password rather than erroring, so it's
safe to run repeatedly.
"""

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Create or update a superuser from DJANGO_SUPERUSER_* env vars (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--username",
            default=os.environ.get("DJANGO_SUPERUSER_USERNAME", "admin"),
        )
        parser.add_argument(
            "--email",
            default=os.environ.get("DJANGO_SUPERUSER_EMAIL", ""),
        )
        parser.add_argument(
            "--password",
            default=os.environ.get("DJANGO_SUPERUSER_PASSWORD"),
            help="Falls back to DJANGO_SUPERUSER_PASSWORD; required.",
        )

    def handle(self, *args, **opts):
        User = get_user_model()
        username = opts["username"]
        password = opts["password"]
        if not password:
            raise CommandError(
                "No password given. Set DJANGO_SUPERUSER_PASSWORD or pass --password."
            )

        user, created = User.objects.get_or_create(
            **{User.USERNAME_FIELD: username},
            defaults={"email": opts["email"]},
        )
        user.is_staff = True
        user.is_superuser = True
        if opts["email"]:
            user.email = opts["email"]
        user.set_password(password)
        user.save()

        self.stdout.write(self.style.SUCCESS(
            f"Superuser {username!r} {'created' if created else 'updated'}."
        ))
