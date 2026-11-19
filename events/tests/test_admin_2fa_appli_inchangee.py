"""La 2FA de l'admin (admin_2fa/) ne change rien hors de /admin/, staff compris.

Un compte staff, enrôlé ou non, se connecte à l'application par le seul mot de
passe (allauth) et y travaille ; /health/ reste public.
"""
from django.core.cache import cache
from django.test import TestCase
from django_otp.plugins.otp_totp.models import TOTPDevice

from events.models import Event
from events.tests.factories import make_dossier, make_user, verifier_adresse

MOT_DE_PASSE = "un-bon-mot-de-passe"


class AppliInchangeeTests(TestCase):
    def setUp(self):
        cache.clear()  # limites de débit d'allauth

    def _staff(self, nom, enrole):
        user = make_user(nom, password=MOT_DE_PASSE, is_staff=True, is_superuser=True)
        verifier_adresse(user)
        if enrole:
            TOTPDevice.objects.create(user=user, name="admin-2fa", confirmed=True)
        return user

    def test_un_staff_utilise_l_application_avec_son_seul_mot_de_passe(self):
        for enrole in (False, True):
            with self.subTest(enrole=enrole):
                self.client.logout()
                user = self._staff(f"staff-{int(enrole)}", enrole)
                dossier = make_dossier(user, name="Dossier fictif")
                Event.objects.create(dossier=dossier, title="Devis ACME Bat", date="2026-01-15")

                connexion = self.client.post(
                    "/accounts/login/", {"login": user.username, "password": MOT_DE_PASSE}
                )
                self.assertEqual(connexion.status_code, 302)
                self.assertEqual(connexion["Location"], "/")
                accueil = self.client.get("/")
                self.assertEqual(accueil.status_code, 200)
                self.assertContains(accueil, "Dossier fictif")

    def test_health_reste_public(self):
        self.assertEqual(self.client.get("/health/").status_code, 200)
