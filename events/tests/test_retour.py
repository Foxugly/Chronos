"""Après un enregistrement, on revient à la vue d'où l'on venait.

Les liens vers le formulaire et le détail portent `?retour=<vue>`. Le
paramètre vient du navigateur : seule une adresse du site est suivie, et un
lien de partage ne renvoie que vers ses propres pages.
"""

from urllib.parse import quote

from django.test import TestCase
from django.urls import reverse

from events.models import DossierAccess, Event, ShareLink
from events.tests.factories import make_dossier, make_user


class RetourTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.event = Event.objects.create(dossier=self.dossier, title="Devis", date="2026-08-05")
        self.client.force_login(self.owner)
        self.vue_mois = reverse("timeline", args=[self.dossier.pk]) + "?vue=mois&periode=2026-08"

    def enregistrer(self, retour, url=None):
        return self.client.post(
            url or reverse("event_edit", args=[self.event.pk]),
            {"title": "Devis signé", "date": "2026-08-05", "description": "", "retour": retour},
        )

    def test_l_enregistrement_ramene_a_la_vue_d_origine(self):
        self.assertRedirects(self.enregistrer(self.vue_mois), self.vue_mois, fetch_redirect_response=False)

    def test_un_ajout_ramene_aussi_a_la_vue_d_origine(self):
        reponse = self.enregistrer(self.vue_mois, url=reverse("event_add", args=[self.dossier.pk]))
        self.assertRedirects(reponse, self.vue_mois, fetch_redirect_response=False)

    def test_sans_retour_on_revient_a_la_chronologie(self):
        reponse = self.enregistrer("")
        self.assertRedirects(
            reponse, reverse("timeline", args=[self.dossier.pk]), fetch_redirect_response=False
        )

    def test_une_adresse_exterieure_est_ignoree(self):
        for retour in ("https://ailleurs.invalid/", "//ailleurs.invalid/", "javascript:alert(1)"):
            with self.subTest(retour=retour):
                reponse = self.enregistrer(retour)
                self.assertEqual(reponse["Location"], reverse("timeline", args=[self.dossier.pk]))

    def test_le_formulaire_garde_le_retour_et_l_annulation_y_mene(self):
        url = reverse("event_edit", args=[self.event.pk]) + "?retour=" + quote(self.vue_mois)
        html = self.client.get(url).content.decode()
        valeur = self.vue_mois.replace("&", "&amp;")
        self.assertIn('name="retour" value="%s"' % valeur, html)
        self.assertIn('href="%s"' % valeur, html)

    def test_les_liens_des_vues_portent_leur_retour(self):
        html = self.client.get(self.vue_mois).content.decode()
        self.assertIn(
            reverse("event_detail", args=[self.event.pk]) + "?retour=" + quote(self.vue_mois, safe="/"),
            html,
        )

    def test_le_detail_transmet_le_retour_au_bouton_modifier(self):
        url = reverse("event_detail", args=[self.event.pk]) + "?retour=" + quote(self.vue_mois)
        html = self.client.get(url).content.decode()
        self.assertIn("retour=" + quote(self.vue_mois, safe=""), html)


class RetourDepuisUnLienDePartageTests(TestCase):
    def setUp(self):
        owner = make_user("owner")
        self.dossier = make_dossier(owner)
        self.event = Event.objects.create(dossier=self.dossier, title="Devis", date="2026-08-05")
        self.lien = ShareLink.objects.create(
            dossier=self.dossier, created_by=owner, role=DossierAccess.Role.WRITE
        )
        self.base = reverse("share_timeline", args=[self.lien.token])

    def enregistrer(self, retour):
        return self.client.post(
            reverse("share_event_edit", args=[self.lien.token, self.event.pk]),
            {"title": "Devis signé", "date": "2026-08-05", "description": "", "retour": retour},
        )

    def test_retour_vers_une_vue_du_lien(self):
        vue = self.base + "?vue=liste"
        self.assertEqual(self.enregistrer(vue)["Location"], vue)

    def test_retour_hors_du_lien_ignore(self):
        """Un visiteur anonyme ne doit pas être renvoyé vers l'application
        authentifiée, ni vers un autre lien."""
        for retour in (reverse("dossier_list"), "/partage/autre-jeton/"):
            with self.subTest(retour=retour):
                self.assertEqual(self.enregistrer(retour)["Location"], self.base)
