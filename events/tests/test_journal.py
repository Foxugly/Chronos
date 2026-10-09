"""Le journal d'un dossier : chaque geste laisse une trace, et la trace reste."""

import hashlib
import shutil
import tempfile

from django.contrib.admin.sites import AdminSite
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from events.admin import EventAdmin
from events.models import Document, DossierAccess, Event, Journal, ShareLink
from events.tests.factories import grant, make_dossier, make_user


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


class ConsignationTests(MediaMixin, TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.event = Event.objects.create(
            dossier=self.dossier, title="Devis", date="2026-08-05", description="<p>avant</p>"
        )
        self.client.force_login(self.owner)

    def actions(self):
        return list(Journal.objects.order_by("pk").values_list("action", flat=True))

    def modifier(self, **valeurs):
        donnees = {"title": "Devis", "date": "2026-08-05", "description": "<p>avant</p>",
                   "version": Event.objects.get(pk=self.event.pk).version}
        donnees.update(valeurs)
        return self.client.post(reverse("event_edit", args=[self.event.pk]), donnees)

    def test_un_ajout_avec_piece_laisse_deux_entrees(self):
        self.client.post(reverse("event_add", args=[self.dossier.pk]), {
            "title": "Constat", "date": "2026-08-20", "description": "",
            "documents": SimpleUploadedFile("constat.pdf", b"%PDF constat"),
        })
        self.assertEqual(self.actions(), [Journal.Action.EVENEMENT_CREE, Journal.Action.PIECE_AJOUTEE])
        piece = Journal.objects.get(action=Journal.Action.PIECE_AJOUTEE)
        self.assertEqual(piece.details["empreinte"], hashlib.sha256(b"%PDF constat").hexdigest())
        self.assertEqual(piece.auteur, self.owner)

    def test_une_modification_garde_l_avant_et_l_apres(self):
        self.modifier(title="Devis signé", date="2026-08-06")
        entree = Journal.objects.get()
        self.assertEqual(entree.action, Journal.Action.EVENEMENT_MODIFIE)
        self.assertEqual(entree.details["changements"], {
            "title": ["Devis", "Devis signé"],
            "date": ["2026-08-05", "2026-08-06"],
        })

    def test_un_enregistrement_sans_changement_ne_laisse_rien(self):
        self.modifier()
        self.assertEqual(self.actions(), [])

    def test_un_enregistrement_refuse_ne_laisse_rien(self):
        """Conflit de version : rien n'est enregistré, rien n'est inscrit."""
        self.modifier(title="Version A")
        self.client.post(reverse("event_edit", args=[self.event.pk]), {
            "title": "Version B", "date": "2026-08-05", "description": "", "version": 1,
        })
        self.assertEqual(self.actions(), [Journal.Action.EVENEMENT_MODIFIE])

    def test_retirer_et_supprimer_une_piece(self):
        autre = Event.objects.create(dossier=self.dossier, title="Autre", date="2026-08-06")
        contenu = b"%PDF piece"
        document = Document.objects.create(
            dossier=self.dossier, title="piece.pdf",
            file=SimpleUploadedFile("piece.pdf", contenu),
            content_hash=hashlib.sha256(contenu).hexdigest(),
        )
        document.events.add(self.event, autre)
        self.client.post(reverse("document_detach", args=[self.event.pk, document.pk]))
        self.client.post(reverse("document_detach", args=[autre.pk, document.pk]))
        self.assertEqual(self.actions(), [Journal.Action.PIECE_RETIREE, Journal.Action.PIECE_SUPPRIMEE])
        derniere = Journal.objects.order_by("pk").last()
        self.assertEqual(derniere.objet, "piece.pdf")
        self.assertEqual(derniere.details["empreinte"], document.content_hash)

    def test_le_partage_est_inscrit(self):
        lecteur = make_user("lecteur")
        self.client.post(reverse("dossier_share", args=[self.dossier.pk]),
                         {"user": lecteur.pk, "role": DossierAccess.Role.READ})
        acces = DossierAccess.objects.get()
        self.client.post(reverse("dossier_access_revoke", args=[self.dossier.pk, acces.pk]))
        self.client.post(reverse("share_link_create", args=[self.dossier.pk]),
                         {"label": "Avocat", "role": DossierAccess.Role.READ, "duree": "7"})
        lien = ShareLink.objects.get()
        self.client.post(reverse("share_link_revoke", args=[self.dossier.pk, lien.pk]))
        self.assertEqual(self.actions(), [
            Journal.Action.ACCES_ACCORDE, Journal.Action.ACCES_RETIRE,
            Journal.Action.LIEN_CREE, Journal.Action.LIEN_REVOQUE,
        ])
        self.assertEqual(Journal.objects.order_by("pk").first().objet, "lecteur")

    def test_un_lien_anonyme_est_note_comme_auteur(self):
        lien = ShareLink.objects.create(
            dossier=self.dossier, created_by=self.owner, role=DossierAccess.Role.WRITE, label="Expert",
        )
        self.client.logout()
        self.client.post(reverse("share_event_edit", args=[lien.token, self.event.pk]), {
            "title": "Par le lien", "date": "2026-08-05", "description": "<p>avant</p>",
        })
        entree = Journal.objects.get()
        self.assertIsNone(entree.auteur)
        self.assertEqual(entree.lien, lien)

    def test_l_administration_est_inscrite_et_signalee(self):
        requete = RequestFactory().post("/admin/")
        requete.user = self.owner
        self.event.title = "Corrigé dans l'admin"
        EventAdmin(Event, AdminSite()).save_model(requete, self.event, form=None, change=True)
        entree = Journal.objects.get()
        self.assertTrue(entree.via_admin)
        self.assertEqual(entree.details["changements"]["title"], ["Devis", "Corrigé dans l'admin"])


class AjoutSeulTests(TestCase):
    def setUp(self):
        self.dossier = make_dossier(make_user("owner"))
        self.entree = Journal.objects.create(
            dossier=self.dossier, action=Journal.Action.EVENEMENT_CREE, objet="x"
        )

    def test_une_entree_ne_se_modifie_pas(self):
        self.entree.objet = "falsifié"
        with self.assertRaises(PermissionError):
            self.entree.save()

    def test_une_entree_ne_se_supprime_pas(self):
        with self.assertRaises(PermissionError):
            self.entree.delete()
        with self.assertRaises(PermissionError):
            Journal.objects.all().delete()
        with self.assertRaises(PermissionError):
            Journal.objects.update(objet="falsifié")

    def test_un_dossier_qui_a_un_journal_ne_se_supprime_pas(self):
        from django.db.models import ProtectedError

        with self.assertRaises(ProtectedError):
            self.dossier.delete()


class PageDuJournalTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.lecteur = make_user("lecteur")
        self.dossier = make_dossier(self.owner)
        grant(self.dossier, self.lecteur, DossierAccess.Role.READ)
        Journal.objects.create(
            dossier=self.dossier, action=Journal.Action.EVENEMENT_MODIFIE,
            objet="<script>window.pwned=1</script>", auteur=self.owner,
            details={"changements": {
                "description": ["<p>un</p><p>deux</p>", "<p>trois</p>"],
                "date": ["2026-08-05", "2026-08-06"],
            }},
        )
        Journal.objects.create(
            dossier=self.dossier, action=Journal.Action.ACCES_ACCORDE,
            objet="lecteur", auteur=self.owner, details={"role": "read"},
        )
        self.url = reverse("dossier_journal", args=[self.dossier.pk])

    def test_le_proprietaire_voit_tout(self):
        self.client.force_login(self.owner)
        html = self.client.get(self.url).content.decode()
        self.assertIn("Accès accordé", html)
        self.assertIn("Évènement modifié", html)
        self.assertIn("05/08/2026", html)
        self.assertIn("un\ndeux", html)

    def test_un_lecteur_ne_voit_pas_le_partage(self):
        self.client.force_login(self.lecteur)
        html = self.client.get(self.url).content.decode()
        self.assertIn("Évènement modifié", html)
        self.assertNotIn("Accès accordé", html)

    def test_un_etranger_recoit_un_404(self):
        self.client.force_login(make_user("etranger"))
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_rien_n_y_est_interprete_comme_du_balisage(self):
        self.client.force_login(self.owner)
        html = self.client.get(self.url).content.decode()
        self.assertNotIn("<script>window.pwned", html)
        self.assertNotIn("<p>un</p>", html)
