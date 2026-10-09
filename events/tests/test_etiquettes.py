"""Étiquettes d'évènements (plusieurs par évènement, propres à un dossier)."""

from django.test import TestCase
from django.urls import reverse

from events.doublons import fusionner
from events.models import DossierAccess, Etiquette, Event, Journal, ShareLink
from events.tests.factories import grant, make_dossier, make_user


class EtiquettesFixture(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.writer = make_user("writer")
        self.reader = make_user("reader")
        self.dossier = make_dossier(self.owner)
        grant(self.dossier, self.writer, DossierAccess.Role.WRITE)
        grant(self.dossier, self.reader, DossierAccess.Role.READ)
        self.event = Event.objects.create(dossier=self.dossier, title="Devis signé", date="2026-06-02")
        self.devis = Etiquette.objects.create(dossier=self.dossier, nom="Devis")
        self.url_edit = reverse("event_edit", args=[self.event.pk])

    def modifier(self, **donnees):
        self.client.force_login(self.writer)
        return self.client.post(self.url_edit, {
            "title": "Devis signé", "date": "2026-06-02", "etiquettes_presentes": "1", **donnees})

    def noms(self, evenement=None):
        return sorted((evenement or self.event).etiquettes.values_list("nom", flat=True))


class PoserDesEtiquettesTests(EtiquettesFixture):
    def test_cocher_et_creer(self):
        self.modifier(etiquettes=[self.devis.pk], nouvelles_etiquettes="Mise en demeure,  Photo  du chantier ")
        self.assertEqual(self.noms(), ["Devis", "Mise en demeure", "Photo du chantier"])
        self.assertEqual(self.dossier.etiquettes.count(), 3)

    def test_une_etiquette_existante_est_reutilisee_sans_tenir_compte_des_majuscules(self):
        self.modifier(nouvelles_etiquettes="devis")
        self.assertEqual(self.noms(), ["Devis"])
        self.assertEqual(self.dossier.etiquettes.count(), 1)

    def test_decocher_retire(self):
        self.event.etiquettes.add(self.devis)
        self.modifier()
        self.assertEqual(self.noms(), [])

    def test_sans_le_marqueur_rien_n_est_touche(self):
        self.event.etiquettes.add(self.devis)
        self.client.force_login(self.writer)
        self.client.post(self.url_edit, {"title": "Devis signé", "date": "2026-06-02"})
        self.assertEqual(self.noms(), ["Devis"])

    def test_une_etiquette_d_un_autre_dossier_est_ignoree(self):
        ailleurs = Etiquette.objects.create(dossier=make_dossier(self.owner, "Autre"), nom="Secret")
        self.modifier(etiquettes=[ailleurs.pk])
        self.assertEqual(self.noms(), [])

    def test_a_la_creation(self):
        self.client.force_login(self.writer)
        self.client.post(reverse("event_add", args=[self.dossier.pk]), {
            "title": "Facture", "date": "2026-07-01", "etiquettes_presentes": "1",
            "nouvelles_etiquettes": "Facture"})
        self.assertEqual(self.noms(Event.objects.get(title="Facture")), ["Facture"])

    def test_par_un_lien_en_ecriture(self):
        lien = ShareLink.objects.create(dossier=self.dossier, created_by=self.owner, role=DossierAccess.Role.WRITE)
        self.client.post(reverse("share_event_edit", args=[lien.token, self.event.pk]), {
            "title": "Devis signé", "date": "2026-06-02", "etiquettes_presentes": "1",
            "etiquettes": [self.devis.pk]})
        self.assertEqual(self.noms(), ["Devis"])

    def test_inscrit_au_journal(self):
        self.modifier(etiquettes=[self.devis.pk])
        entree = Journal.objects.get(action=Journal.Action.EVENEMENT_MODIFIE)
        self.assertEqual(entree.details["changements"]["etiquettes"], ["", "Devis"])

    def test_le_formulaire_propose_les_etiquettes(self):
        self.client.force_login(self.writer)
        reponse = self.client.get(self.url_edit)
        self.assertContains(reponse, 'name="etiquettes_presentes"')
        self.assertContains(reponse, "Devis")
        self.assertIn("Mise en demeure", reponse.context["suggestions_etiquettes"])
        self.assertNotIn("Devis", reponse.context["suggestions_etiquettes"])


class FiltreEtAffichageTests(EtiquettesFixture):
    def setUp(self):
        super().setUp()
        self.event.etiquettes.add(self.devis)
        Event.objects.create(dossier=self.dossier, title="Autre fait", date="2026-06-03")

    def test_filtre_par_etiquette_dans_toutes_les_vues(self):
        self.client.force_login(self.reader)
        base = reverse("timeline", args=[self.dossier.pk])
        for vue in ("chronologie", "calendrier", "liste", "documents", "mois"):
            with self.subTest(vue=vue):
                reponse = self.client.get(base, {"vue": vue, "etiquette": self.devis.pk})
                self.assertEqual(reponse.status_code, 200)
                self.assertEqual(reponse.context["nb_retenus"], 1)

    def test_badges_dans_la_liste_et_le_detail(self):
        self.client.force_login(self.reader)
        liste = self.client.get(reverse("timeline", args=[self.dossier.pk]), {"vue": "liste"})
        self.assertContains(liste, 'bi-tag me-1" aria-hidden="true"></i>Devis')
        detail = self.client.get(reverse("event_detail", args=[self.event.pk]))
        self.assertContains(detail, 'bi-tag me-1" aria-hidden="true"></i>Devis')

    def test_la_fusion_reunit_les_etiquettes(self):
        autre = Event.objects.get(title="Autre fait")
        photo = Etiquette.objects.create(dossier=self.dossier, nom="Photo")
        autre.etiquettes.add(photo)
        fusionner(Event.objects.get(pk=self.event.pk), autre, request=type("R", (), {"user": self.owner})())
        self.assertEqual(self.noms(), ["Devis", "Photo"])


class GestionDesEtiquettesTests(EtiquettesFixture):
    def url(self, etiquette=None):
        return reverse("dossier_etiquette", args=[self.dossier.pk, (etiquette or self.devis).pk])

    def test_renommer(self):
        self.client.force_login(self.owner)
        self.client.post(self.url(), {"nom": "Devis signés"})
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.nom, "Devis signés")
        self.assertTrue(Journal.objects.filter(details__changements__etiquettes=["Devis", "Devis signés"]).exists())

    def test_supprimer_garde_les_evenements(self):
        self.event.etiquettes.add(self.devis)
        self.client.force_login(self.owner)
        self.client.post(self.url(), {"supprimer": "1"})
        self.assertFalse(Etiquette.objects.filter(pk=self.devis.pk).exists())
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_un_nom_deja_pris_est_refuse(self):
        Etiquette.objects.create(dossier=self.dossier, nom="Facture")
        self.client.force_login(self.owner)
        self.client.post(self.url(), {"nom": "facture"})
        self.devis.refresh_from_db()
        self.assertEqual(self.devis.nom, "Devis")

    def test_reserve_au_proprietaire(self):
        for qui in (self.writer, self.reader):
            self.client.force_login(qui)
            self.assertEqual(self.client.post(self.url(), {"supprimer": "1"}).status_code, 404)
        self.assertTrue(Etiquette.objects.filter(pk=self.devis.pk).exists())

    def test_etiquette_d_un_autre_dossier_404(self):
        ailleurs = Etiquette.objects.create(dossier=make_dossier(self.owner, "Autre"), nom="X")
        self.client.force_login(self.owner)
        self.assertEqual(self.client.post(self.url(ailleurs), {"supprimer": "1"}).status_code, 404)

    def test_page_parametres_les_liste(self):
        self.client.force_login(self.owner)
        reponse = self.client.get(reverse("dossier_parametres", args=[self.dossier.pk]))
        self.assertContains(reponse, self.url())
