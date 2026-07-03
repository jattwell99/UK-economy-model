"""seed_admin: non-interactive, idempotent superuser creation."""

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase


class SeedAdminTests(TestCase):
    def test_creates_superuser(self):
        call_command("seed_admin", username="josh", email="josh@example.com",
                     password="s3cret-pw")
        u = get_user_model().objects.get(username="josh")
        self.assertTrue(u.is_superuser and u.is_staff)
        self.assertEqual(u.email, "josh@example.com")
        self.assertTrue(u.check_password("s3cret-pw"))

    def test_idempotent_updates_password(self):
        call_command("seed_admin", username="josh", password="first-pw")
        call_command("seed_admin", username="josh", password="second-pw")
        User = get_user_model()
        self.assertEqual(User.objects.filter(username="josh").count(), 1)
        self.assertTrue(User.objects.get(username="josh").check_password("second-pw"))

    def test_requires_password(self):
        with self.assertRaises(CommandError):
            call_command("seed_admin", username="josh", password=None)
