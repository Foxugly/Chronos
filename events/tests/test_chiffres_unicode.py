"""Un identifiant mal formé est ignoré, jamais une erreur 500 (audit du 2026-10-18, M1).

`"²".isdigit()` est vrai mais `int("²")` lève `ValueError` ; un nombre trop
grand pour un `bigint` fait lever PostgreSQL. Les deux arrivaient par l'URL,
y compris par un lien de partage anonyme — et chaque 500 partait vers Sentry.
"""

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from events.filtres import identifiant
from events.models import DossierAccess, Etiquette, Event, ShareLink
from events.tests.factories import make_dossier, make_user

MAL_FORMES = ("²", "٣", "¹²", "1²", "99999999999999999999", "-1", " 1", "")


class IdentifiantTests(SimpleTestCase):
    def test_seuls_les_chiffres_ascii_bornes_passent(self):
        self.assertEqual(identifiant("42"), 42)
        self.assertEqual(identifiant(42), 42)
        for valeur in (*MAL_FORMES, None):
            with self.subTest(valeur=valeur):
                self.assertIsNone(identifiant(valeur))


class ParametresMalFormesTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.event = Event.objects.create(dossier=self.dossier, title="Devis signé", date="2026-06-02")
        self.etiquette = Etiquette.objects.create(dossier=self.dossier, nom="Devis")
        self.lien = ShareLink.objects.create(
            dossier=self.dossier, created_by=self.owner, role=DossierAccess.Role.WRITE
        )

    def test_les_filtres_de_la_chronologie(self):
        self.client.force_login(self.owner)
        url = reverse("timeline", args=[self.dossier.pk])
        for parametre in ("expediteur", "etiquette"):
            for valeur in MAL_FORMES:
                for vue in ("", "liste", "documents"):
                    with self.subTest(parametre=parametre, valeur=valeur, vue=vue):
                        reponse = self.client.get(url, {parametre: valeur, "vue": vue})
                        self.assertEqual(reponse.status_code, 200)

    def test_les_filtres_d_un_lien_de_partage(self):
        url = reverse("share_timeline", args=[self.lien.token])
        for parametre in ("expediteur", "etiquette"):
            for valeur in MAL_FORMES:
                with self.subTest(parametre=parametre, valeur=valeur):
                    self.assertEqual(self.client.get(url, {parametre: valeur, "vue": "liste"}).status_code, 200)

    def test_le_lien_tout_juste_cree(self):
        self.client.force_login(self.owner)
        url = reverse("dossier_share", args=[self.dossier.pk])
        for valeur in MAL_FORMES:
            with self.subTest(valeur=valeur):
                self.assertEqual(self.client.get(url, {"nouveau": valeur}).status_code, 200)

    def test_les_etiquettes_cochees(self):
        self.client.force_login(self.owner)
        url = reverse("event_edit", args=[self.event.pk])
        for valeur in MAL_FORMES:
            with self.subTest(valeur=valeur):
                reponse = self.client.post(url, {
                    "title": "Devis signé", "date": "2026-06-02", "etiquettes_presentes": "1",
                    "etiquettes": [valeur, str(self.etiquette.pk)],
                })
                self.assertIn(reponse.status_code, (200, 302))
                self.assertEqual(list(self.event.etiquettes.values_list("nom", flat=True)), ["Devis"])

    def test_le_formulaire_refuse_garde_les_etiquettes_lisibles(self):
        """`contexte_du_formulaire` relit le POST quand le formulaire revient en erreur."""
        self.client.force_login(self.owner)
        url = reverse("event_edit", args=[self.event.pk])
        reponse = self.client.post(url, {
            "title": "", "date": "", "etiquettes_presentes": "1", "etiquettes": ["²", "99999999999999999999"],
        })
        self.assertEqual(reponse.status_code, 200)

    def test_la_fusion_de_doublons(self):
        autre = Event.objects.create(dossier=self.dossier, title="Devis", date="2026-06-02")
        self.client.force_login(self.owner)
        url = reverse("doublons_fusionner", args=[self.dossier.pk])
        for valeur in MAL_FORMES:
            with self.subTest(valeur=valeur):
                self.assertEqual(self.client.post(url, {"a": valeur, "b": autre.pk, "garder": autre.pk}).status_code, 404)
                self.assertEqual(self.client.post(url, {"a": self.event.pk, "b": autre.pk, "garder": valeur}).status_code, 404)
        self.assertEqual(Event.objects.filter(dossier=self.dossier).count(), 2)
