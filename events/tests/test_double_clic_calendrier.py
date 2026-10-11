"""Double-cliquer une case vide du calendrier ouvre la création à cette date (et heure).

Le serveur pré-remplit le formulaire depuis `?date=AAAA-MM-JJ&heure=HH:MM` ; le
calendrier, pour qui peut écrire, y mène au double-clic.
"""

import re

from django.test import TestCase
from django.urls import reverse

from events.models import DossierAccess, Event, ShareLink
from events.tests.factories import grant, make_dossier, make_user


class PreRemplissageTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.client.force_login(self.owner)
        self.url = reverse("event_add", args=[self.dossier.pk])

    def champ(self, html, nom):
        return re.search(r'<input[^>]*name="%s"[^>]*>' % nom, html).group(0)

    def mode(self, html):
        return re.search(r'<input[^>]*name="precision"[^>]*value="(\w+)"[^>]*checked', html).group(1)

    def test_une_date_et_une_heure(self):
        html = self.client.get(self.url, {"date": "2026-09-12", "heure": "10:30"}).content.decode()
        self.assertIn('value="2026-09-12"', self.champ(html, "date"))
        self.assertIn('value="10:30"', self.champ(html, "heure"))
        self.assertEqual(self.mode(html), "instant")

    def test_une_date_seule(self):
        html = self.client.get(self.url, {"date": "2026-09-12"}).content.decode()
        self.assertIn('value="2026-09-12"', self.champ(html, "date"))
        self.assertEqual(self.mode(html), "date")

    def test_des_valeurs_invalides_sont_ignorees(self):
        for parametres in ({"date": "12/09/2026"}, {"date": "2026-13-40"}, {"date": "2026-09-12", "heure": "25:99"},
                           {"heure": "10:30"}):
            with self.subTest(parametres=parametres):
                reponse = self.client.get(self.url, parametres)
                self.assertEqual(reponse.status_code, 200)
                html = reponse.content.decode()
                self.assertNotIn("12/09/2026", html)
                self.assertNotIn('value="25:99"', html)

    def test_aussi_en_ajax_pour_le_modal(self):
        html = self.client.get(self.url, {"date": "2026-09-12", "heure": "08:05"},
                               HTTP_X_REQUESTED_WITH="XMLHttpRequest").content.decode()
        self.assertIn('value="08:05"', self.champ(html, "heure"))

    def test_par_un_lien_de_partage_en_ecriture(self):
        lien = ShareLink.objects.create(dossier=self.dossier, created_by=self.owner, role=DossierAccess.Role.WRITE)
        self.client.logout()
        html = self.client.get(reverse("share_event_add", args=[lien.token]),
                               {"date": "2026-09-12", "heure": "10:30"}).content.decode()
        self.assertIn('value="2026-09-12"', self.champ(html, "date"))
        self.assertEqual(self.mode(html), "instant")


class CalendrierTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        Event.objects.create(dossier=self.dossier, title="Fait", date="2026-09-12")
        self.vue = reverse("timeline", args=[self.dossier.pk])

    def page(self, user):
        self.client.force_login(user)
        return self.client.get(self.vue, {"vue": "calendrier-semaine"}).content.decode()

    def test_un_redacteur_peut_creer_au_double_clic(self):
        redacteur = make_user("redacteur")
        grant(self.dossier, redacteur, DossierAccess.Role.WRITE)
        for user in (self.owner, redacteur):
            with self.subTest(user=user.username):
                html = self.page(user)
                self.assertIn('const URL_AJOUT = "%s"' % reverse("event_add", args=[self.dossier.pk]), html)
                self.assertIn("Double-cliquez", html)

    def test_un_lecteur_non(self):
        lecteur = make_user("lecteur")
        grant(self.dossier, lecteur, DossierAccess.Role.READ)
        html = self.page(lecteur)
        self.assertIn("const URL_AJOUT = null", html)
        self.assertNotIn("Double-cliquez", html)

    def test_un_lien_de_partage_en_ecriture_aussi(self):
        lien = ShareLink.objects.create(dossier=self.dossier, created_by=self.owner, role=DossierAccess.Role.WRITE)
        html = self.client.get(reverse("share_timeline", args=[lien.token]),
                               {"vue": "calendrier-semaine"}).content.decode()
        self.assertIn('const URL_AJOUT = "%s"' % reverse("share_event_add", args=[lien.token]), html)
