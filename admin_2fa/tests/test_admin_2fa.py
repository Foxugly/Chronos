"""Admin 2FA: the fleet's generic tests (Poker_server), ported to Django's test runner.

Chronos specifics: the password step is allauth's ``/accounts/login/`` (``/admin/login/``
is shadowed in ``config/urls.py`` and only routes to the right place), accounts need a
verified e-mail address, and ``LoginRequiredMiddleware`` sends anonymous visitors to
``LOGIN_URL``. The application side ("nothing changes outside /admin/") is tested in
``events/tests/test_admin_2fa_appli_inchangee.py``.
"""
import re
import time
from io import StringIO

from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.checks import run_checks
from django.core.management import CommandError, call_command
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django_otp.oath import TOTP
from django_otp.plugins.otp_static.models import StaticDevice, StaticToken
from django_otp.plugins.otp_totp.models import TOTPDevice

from admin_2fa.utils import BACKUP_CODE_COUNT, Redacted

PASSWORD = "Adm1n-Passw0rd!"
INDEX = "/admin/"
LOGIN = "/accounts/login/"


def _make_user(login, staff=True, superuser=False):
    user = get_user_model().objects.create_user(
        username=login, email=f"{login}@example.test", password=PASSWORD
    )
    user.is_staff = staff
    user.is_superuser = superuser
    user.save()
    EmailAddress.objects.create(user=user, email=user.email, primary=True, verified=True)
    return user


def _code(device, offset=0):
    totp = TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift)
    totp.time = time.time() + offset * device.step
    return f"{totp.token():0{device.digits}d}"


def _enrolled_device(user):
    return TOTPDevice.objects.create(user=user, name="admin-2fa", confirmed=True)


def _backup_codes(user, codes=("aaaa2222", "bbbb3333")):
    device = StaticDevice.objects.create(user=user, name="admin-2fa-backup", confirmed=True)
    for code in codes:
        StaticToken.objects.create(device=device, token=code)
    return device


class Admin2FATestCase(TestCase):
    def setUp(self):
        cache.clear()  # allauth's login rate limits
        self.staff = _make_user("staff", superuser=True)

    def password_login(self, user=None, next_url=INDEX, client=None):
        """The real password step (allauth), then the redirect chain to the second factor."""
        user = user or self.staff
        client = client or self.client
        response = client.post(
            LOGIN, {"login": user.username, "password": PASSWORD, "next": next_url}
        )
        self.assertEqual(response.status_code, 302)
        return client.get(response["Location"], follow=True)


class WiringTests(Admin2FATestCase):
    def test_system_checks_pass_with_the_settings_of_this_project(self):
        self.assertEqual([e for e in run_checks() if e.id.startswith("admin_2fa.")], [])

    def test_redacted_hides_the_value_from_tracebacks(self):
        secret = Redacted(["code1", "code2"])
        self.assertEqual(repr(secret), "<redacted>")
        self.assertEqual(list(secret), ["code1", "code2"])
        self.assertEqual(str(Redacted("ABCD")), "ABCD")
        self.assertEqual(Redacted("<b>").__html__(), "&lt;b&gt;")

    def test_system_checks_catch_a_half_done_port(self):
        from django.conf import settings

        from admin_2fa.checks import check_admin_2fa

        middleware = [m for m in settings.MIDDLEWARE if "django_otp" not in m]
        with override_settings(MIDDLEWARE=middleware, OTP_TOTP_THROTTLE_FACTOR=0):
            self.assertEqual(
                {e.id for e in check_admin_2fa(None)}, {"admin_2fa.E002", "admin_2fa.E004"}
            )


class EnrolmentTests(Admin2FATestCase):
    def test_staff_without_device_is_sent_to_enrolment_and_never_reaches_the_admin(self):
        response = self.password_login()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.redirect_chain[-1][0].startswith(reverse("admin:2fa_setup")))
        self.assertTemplateUsed(response, "admin_2fa/setup.html")

        index = self.client.get(INDEX)
        self.assertEqual(index.status_code, 302)
        self.assertTrue(index["Location"].startswith(reverse("admin:login")))
        # The (shadowed) admin login forwards the half-logged-in user to the second factor.
        login_page = self.client.get(index["Location"])
        self.assertEqual(login_page.status_code, 302)
        self.assertTrue(login_page["Location"].startswith(reverse("admin:2fa_setup")))
        # Any admin page, not just the index.
        self.assertEqual(self.client.get(reverse("admin:password_change")).status_code, 302)

    def test_enrolment_page_shows_a_qr_code_and_the_key_and_keeps_the_same_secret(self):
        self.password_login()
        first = self.client.get(reverse("admin:2fa_setup"))
        self.assertEqual(first.status_code, 200)
        body = first.content.decode()
        self.assertIn('role="img" aria-label="QR code"', body)
        self.assertIsNone(re.search(r"<script(?![^>]*\bsrc=)", body))  # no inline script (CSP)
        device = TOTPDevice.objects.get(user=self.staff)
        self.assertFalse(device.confirmed)
        self.assertIn("no-cache", first["Cache-Control"])
        self.assertIn("no-store", first["Cache-Control"])

        self.client.get(reverse("admin:2fa_setup"))
        self.assertEqual(TOTPDevice.objects.filter(user=self.staff).count(), 1)  # same QR on reload

    def test_enrolment_with_the_right_code_confirms_and_shows_backup_codes_once(self):
        self.password_login()
        self.client.get(reverse("admin:2fa_setup"))
        device = TOTPDevice.objects.get(user=self.staff)

        response = self.client.post(reverse("admin:2fa_setup"), {"token": _code(device), "next": INDEX})
        self.assertEqual(response.status_code, 200)
        device.refresh_from_db()
        self.assertTrue(device.confirmed)
        tokens = list(StaticToken.objects.filter(device__user=self.staff).values_list("token", flat=True))
        self.assertEqual(len(tokens), BACKUP_CODE_COUNT)
        body = response.content.decode()
        self.assertTrue(all(token in body for token in tokens))
        self.assertIn("no-store", response["Cache-Control"])

        self.assertEqual(self.client.get(INDEX).status_code, 200)
        # Re-posting (a reload) never shows the codes again.
        again = self.client.post(reverse("admin:2fa_setup"), {"token": _code(device), "next": INDEX})
        self.assertEqual(again.status_code, 302)
        index = self.client.get(INDEX).content.decode()
        self.assertFalse(any(token in index for token in tokens))

    def test_enrolment_with_a_wrong_code_is_refused(self):
        self.password_login()
        self.client.get(reverse("admin:2fa_setup"))
        device = TOTPDevice.objects.get(user=self.staff)
        wrong = f"{(int(_code(device)) + 1) % 1_000_000:06d}"

        response = self.client.post(reverse("admin:2fa_setup"), {"token": wrong})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Wrong code")
        device.refresh_from_db()
        self.assertFalse(device.confirmed)
        self.assertFalse(StaticDevice.objects.filter(user=self.staff).exists())
        self.assertEqual(self.client.get(INDEX).status_code, 302)

    def test_enrolled_user_cannot_re_enrol_without_a_reset(self):
        _enrolled_device(self.staff)
        self.password_login()
        response = self.client.get(reverse("admin:2fa_setup"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response["Location"].startswith(reverse("admin:2fa_verify")))
        self.assertEqual(TOTPDevice.objects.filter(user=self.staff).count(), 1)


class VerifyTests(Admin2FATestCase):
    def test_staff_with_a_device_must_give_the_code(self):
        device = _enrolled_device(self.staff)
        response = self.password_login(next_url="/admin/auth/group/")
        self.assertTrue(response.redirect_chain[-1][0].startswith(reverse("admin:2fa_verify")))
        self.assertEqual(self.client.get(INDEX).status_code, 302)

        bad = self.client.post(reverse("admin:2fa_verify"), {"token": "000000"})
        self.assertEqual(bad.status_code, 200)
        self.assertEqual(self.client.get(INDEX).status_code, 302)

        device.throttle_reset()  # the wrong code above armed the 1 s delay
        good = self.client.post(
            reverse("admin:2fa_verify"), {"token": _code(device), "next": "/admin/auth/group/"}
        )
        self.assertEqual(good.status_code, 302)
        self.assertEqual(good["Location"], "/admin/auth/group/")
        self.assertEqual(self.client.get(INDEX).status_code, 200)

    def test_wrong_codes_are_throttled_by_django_otp(self):
        device = _enrolled_device(self.staff)
        self.password_login()
        self.client.post(reverse("admin:2fa_verify"), {"token": "000000"})
        device.refresh_from_db()
        self.assertEqual(device.throttling_failure_count, 1)

        # Even the right code is refused while the delay runs.
        response = self.client.post(reverse("admin:2fa_verify"), {"token": _code(device)})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Too many failed attempts")
        self.assertEqual(self.client.get(INDEX).status_code, 302)

    def test_backup_code_works_exactly_once(self):
        _enrolled_device(self.staff)
        _backup_codes(self.staff)

        self.password_login()
        used = self.client.post(reverse("admin:2fa_verify"), {"token": "AAAA 2222"})  # spaces, caps
        self.assertEqual(used.status_code, 302)
        self.assertEqual(self.client.get(INDEX).status_code, 200)
        self.assertFalse(StaticToken.objects.filter(token="aaaa2222").exists())

        self.client.logout()
        other = Client()
        self.password_login(client=other)
        reused = other.post(reverse("admin:2fa_verify"), {"token": "aaaa2222"})
        self.assertEqual(reused.status_code, 200)
        self.assertContains(reused, "Invalid code")
        self.assertEqual(other.get(INDEX).status_code, 302)

    def test_next_is_never_an_external_url(self):
        device = _enrolled_device(self.staff)
        self.password_login()
        response = self.client.post(
            reverse("admin:2fa_verify"), {"token": _code(device), "next": "https://evil.example/"}
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], INDEX)


class WhoMayReachTheStepsTests(Admin2FATestCase):
    def test_non_staff_gets_no_admin_access(self):
        user = _make_user("user", staff=False)
        self.client.force_login(user)
        for url in (INDEX, reverse("admin:2fa_setup"), reverse("admin:2fa_verify")):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(reverse("admin:login")))
                # ... where the shadowed admin login answers 404 (config/urls.py).
                self.assertEqual(self.client.get(response["Location"]).status_code, 404)
        self.assertFalse(TOTPDevice.objects.filter(user=user).exists())

    def test_anonymous_is_sent_to_the_password_login(self):
        for url in (reverse("admin:2fa_setup"), reverse("admin:2fa_verify")):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(LOGIN))
        self.assertFalse(TOTPDevice.objects.exists())

    def test_post_without_csrf_token_is_refused(self):
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.staff)
        response = csrf_client.post(reverse("admin:2fa_setup"), {"token": "123456"})
        self.assertEqual(response.status_code, 403)


class ResetCommandTests(Admin2FATestCase):
    def test_reset_command_deletes_the_devices_and_forces_a_new_enrolment(self):
        device = _enrolled_device(self.staff)
        _backup_codes(self.staff)
        self.password_login()
        self.client.post(reverse("admin:2fa_verify"), {"token": _code(device)})
        self.assertEqual(self.client.get(INDEX).status_code, 200)

        out = StringIO()
        call_command("admin_2fa_reset", self.staff.username.upper(), stdout=out)
        self.assertIn("Deleted 2 OTP device(s)", out.getvalue())
        self.assertFalse(TOTPDevice.objects.filter(user=self.staff).exists())
        self.assertFalse(StaticDevice.objects.filter(user=self.staff).exists())

        # The session verified with the deleted device is no longer verified.
        response = self.client.get(INDEX)
        self.assertEqual(response.status_code, 302)
        login_page = self.client.get(response["Location"])
        self.assertTrue(login_page["Location"].startswith(reverse("admin:2fa_setup")))

    def test_reset_command_unlock_keeps_the_devices(self):
        device = _enrolled_device(self.staff)
        device.throttle_increment()
        call_command("admin_2fa_reset", self.staff.username, "--unlock", stdout=StringIO())
        device.refresh_from_db()
        self.assertEqual(device.throttling_failure_count, 0)
        self.assertTrue(TOTPDevice.objects.filter(user=self.staff).exists())

    def test_reset_command_unknown_user(self):
        with self.assertRaises(CommandError):
            call_command("admin_2fa_reset", "nobody", stdout=StringIO())

    def test_force_admin_login_helper_opens_the_admin(self):
        from admin_2fa.testing import force_admin_login

        force_admin_login(self.client, self.staff)
        self.assertEqual(self.client.get(INDEX).status_code, 200)
