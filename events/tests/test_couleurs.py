"""Une couleur par évènement ; celle de l'expéditeur pour un évènement importé par courriel."""

import json
import re
import shutil
import tempfile

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from events.couleurs import PALETTE, couleur_du_texte, prochaine_couleur
from events.courriels import analyser
from events.import_courriels import importer
from events.models import DossierAccess, Event, Expediteur, Journal
from events.tests.factories import grant, make_dossier, make_user
from events.tests.test_courriels import ALIAS, courriel, original


class PaletteTests(TestCase):
    def test_le_texte_se_lit_sur_chaque_couleur(self):
        self.assertEqual(couleur_du_texte("#2563eb"), "#ffffff")
        self.assertEqual(couleur_du_texte("#fde047"), "#000000")

    def test_la_premiere_couleur_libre(self):
        self.assertEqual(prochaine_couleur([]), PALETTE[0])
        self.assertEqual(prochaine_couleur([PALETTE[0]]), PALETTE[1])
        self.assertEqual(prochaine_couleur(PALETTE), PALETTE[0])

    def test_une_couleur_mal_formee_est_refusee(self):
        dossier = make_dossier(make_user("owner"))
        for valeur in ("red", "#fff", "#12345g", "#123456;background:url(x)"):
            with self.subTest(valeur=valeur), self.assertRaises(ValidationError):
                Expediteur(dossier=dossier, adresse="a@x.be", couleur=valeur).full_clean()


class MediaMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_root = tempfile.mkdtemp()
        cls.enterClassContext(override_settings(MEDIA_ROOT=cls.media_root))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.media_root, ignore_errors=True)


class CouleurDesExpediteursTests(MediaMixin, TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.dossier.alias_courriel = ALIAS
        self.dossier.save()

    def importer(self, de, message_id):
        m = courriel("Sujet", "Texte", de=de, message_id=message_id)
        return importer(self.dossier, analyser(m.as_bytes()))

    def test_chaque_expediteur_recoit_sa_couleur(self):
        a1 = self.importer("Alice <alice@x.be>", "<1@x>")
        b = self.importer("bob@x.be", "<2@x>")
        a2 = self.importer("ALICE@x.be", "<3@x>")
        self.assertEqual(a1.expediteur, a2.expediteur)
        self.assertEqual(a1.couleur_affichee, PALETTE[0])
        self.assertEqual(b.couleur_affichee, PALETTE[1])
        self.assertEqual(str(a1.expediteur), "Alice")

    def test_le_transfert_en_piece_jointe_prend_l_auteur_d_origine(self):
        m = courriel("TR: x", "y", joint=original())
        evenement = importer(self.dossier, analyser(m.as_bytes()))
        self.assertEqual(evenement.expediteur.adresse, "contact@acme-bat.example")

    def test_changer_la_couleur_de_l_expediteur_recolore_tous_ses_evenements(self):
        e1 = self.importer("alice@x.be", "<1@x>")
        e2 = self.importer("alice@x.be", "<2@x>")
        expediteur = e1.expediteur
        expediteur.couleur = "#16a34a"
        expediteur.save()
        for e in (e1, e2):
            e = Event.objects.get(pk=e.pk)
            self.assertEqual(e.pour_calendrier()["backgroundColor"], "#16a34a")
            self.assertIn("background-color: #16a34a", e.pour_chronologie()["style"])

    def test_l_expediteur_prime_sur_la_couleur_manuelle(self):
        e = self.importer("alice@x.be", "<1@x>")
        e.couleur = "#dc2626"
        e.save()
        self.assertEqual(Event.objects.get(pk=e.pk).couleur_affichee, PALETTE[0])


class CouleurManuelleTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.event = Event.objects.create(dossier=self.dossier, title="Devis", date="2026-08-05")
        self.client.force_login(self.owner)

    def enregistrer(self, couleur):
        return self.client.post(reverse("event_edit", args=[self.event.pk]), {
            "title": "Devis", "date": "2026-08-05", "description": "", "couleur": couleur,
            "version": Event.objects.get(pk=self.event.pk).version,
        })

    def test_sans_couleur_rien_n_est_ajoute(self):
        self.event.refresh_from_db()
        self.assertIsNone(self.event.couleur_affichee)
        self.assertNotIn("backgroundColor", self.event.pour_calendrier())
        self.assertNotIn("style", self.event.pour_chronologie())

    def test_choisir_une_couleur_de_la_palette(self):
        self.enregistrer("#7c3aed")
        self.assertEqual(Event.objects.get(pk=self.event.pk).couleur, "#7c3aed")
        entree = Journal.objects.get()
        self.assertEqual(entree.details["changements"]["couleur"], ["", "#7c3aed"])

    def test_une_couleur_hors_palette_est_refusee(self):
        reponse = self.enregistrer('#000000" onmouseover="alert(1)')
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(Event.objects.get(pk=self.event.pk).couleur, "")

    def test_le_formulaire_propose_la_palette(self):
        html = self.client.get(reverse("event_edit", args=[self.event.pk])).content.decode()
        for couleur in PALETTE:
            self.assertIn('value="%s"' % couleur, html)


class AffichageTests(MediaMixin, TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.dossier.alias_courriel = ALIAS
        self.dossier.save()
        for i in range(2):
            importer(self.dossier, analyser(courriel("Sujet %d" % i, "x", de="alice@x.be",
                                                    message_id="<%d@x>" % i, recu="Wed, 9 Sep 2026 10:0%d:00 +0000" % i).as_bytes()))
        self.client.force_login(self.owner)
        self.base = reverse("timeline", args=[self.dossier.pk])

    def test_chaque_vue_montre_la_couleur(self):
        couleur = PALETTE[0]
        for vue in ("liste", "mois", "semaine", "chronologie", "calendrier-mois"):
            with self.subTest(vue=vue):
                params = {"vue": vue}
                if vue in ("mois", "semaine"):
                    params["periode"] = "2026-09" if vue == "mois" else "2026-09-07"
                html = self.client.get(self.base, params).content.decode()
                self.assertIn(couleur, html)

    def test_la_couleur_ne_coute_pas_une_requete_par_evenement(self):
        def requetes():
            with CaptureQueriesContext(connection) as capture:
                self.client.get(self.base, {"vue": "liste"})
            return len(capture.captured_queries)

        avant = requetes()
        importer(self.dossier, analyser(courriel("Sujet 9", "x", de="bob@x.be", message_id="<9@x>").as_bytes()))
        self.assertLessEqual(requetes(), avant + 1)  # au plus la requête des pièces du nouvel évènement


class PageDesExpediteursTests(MediaMixin, TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.lecteur = make_user("lecteur")
        self.dossier = make_dossier(self.owner)
        grant(self.dossier, self.lecteur, DossierAccess.Role.READ)
        self.dossier.alias_courriel = ALIAS
        self.dossier.save()
        self.evenement = importer(self.dossier, analyser(courriel("x", "y", de="alice@x.be").as_bytes()))
        self.expediteur = self.evenement.expediteur
        self.url = reverse("dossier_expediteurs", args=[self.dossier.pk])

    def test_le_lecteur_voit_la_legende_sans_pouvoir_la_changer(self):
        self.client.force_login(self.lecteur)
        html = self.client.get(self.url).content.decode()
        self.assertIn("alice@x.be", html)
        self.assertNotIn('type="color"', html)
        self.assertEqual(self.client.post(self.url, {"couleur-%d" % self.expediteur.pk: "#000000"}).status_code, 404)

    def test_le_proprietaire_change_la_couleur_et_c_est_journalise(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {"couleur-%d" % self.expediteur.pk: "#16A34A"})
        self.expediteur.refresh_from_db()
        self.assertEqual(self.expediteur.couleur, "#16a34a")
        entree = Journal.objects.get(action=Journal.Action.DOSSIER_MODIFIE)
        self.assertEqual(entree.details["changements"]["couleur"], [PALETTE[0], "#16a34a"])

    def test_une_couleur_invalide_ne_passe_pas(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {"couleur-%d" % self.expediteur.pk: "red;}"})
        self.expediteur.refresh_from_db()
        self.assertEqual(self.expediteur.couleur, PALETTE[0])

    def test_un_etranger_recoit_un_404(self):
        self.client.force_login(make_user("etranger"))
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_le_bouton_apparait_quand_il_y_a_des_expediteurs(self):
        self.client.force_login(self.owner)
        html = self.client.get(reverse("timeline", args=[self.dossier.pk])).content.decode()
        self.assertIn(self.url, html)
