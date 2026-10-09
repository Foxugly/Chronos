"""Ce qui se modifie dans l'administration passe aussi par le journal, et
l'export s'obtient aussi par un lien de partage."""

import hashlib
import io
import shutil
import tempfile
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

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


class JournalDeLAdminTests(MediaMixin, TestCase):
    def setUp(self):
        self.admin = make_user("admin", is_staff=True, is_superuser=True)
        self.dossier = make_dossier(self.admin, name="Litige")
        self.event = Event.objects.create(dossier=self.dossier, title="Devis", date="2026-08-05")
        self.autre = Event.objects.create(dossier=self.dossier, title="Constat", date="2026-08-20")
        contenu = b"%PDF devis"
        self.piece = Document.objects.create(
            dossier=self.dossier, title="devis.pdf",
            file=SimpleUploadedFile("devis.pdf", contenu),
            content_hash=hashlib.sha256(contenu).hexdigest(),
        )
        self.piece.events.add(self.event)
        self.client.force_login(self.admin)

    def entrees(self):
        return list(Journal.objects.order_by("pk").values_list("action", "objet", "via_admin"))

    def test_renommer_un_dossier(self):
        self.client.post(f"/admin/events/dossier/{self.dossier.pk}/change/", {
            "name": "Litige SDB", "owner": self.admin.pk,
            "accesses-TOTAL_FORMS": "0", "accesses-INITIAL_FORMS": "0",
            "accesses-MIN_NUM_FORMS": "0", "accesses-MAX_NUM_FORMS": "1000",
        })
        entree = Journal.objects.get()
        self.assertEqual(entree.action, Journal.Action.DOSSIER_MODIFIE)
        self.assertTrue(entree.via_admin)
        self.assertEqual(entree.details["changements"], {"name": ["Litige", "Litige SDB"]})

    def test_un_acces_accorde_depuis_la_fiche_du_dossier(self):
        lecteur = make_user("lecteur")
        self.client.post(f"/admin/events/dossier/{self.dossier.pk}/change/", {
            "name": "Litige", "owner": self.admin.pk,
            "accesses-TOTAL_FORMS": "1", "accesses-INITIAL_FORMS": "0",
            "accesses-MIN_NUM_FORMS": "0", "accesses-MAX_NUM_FORMS": "1000",
            "accesses-0-user": lecteur.pk, "accesses-0-role": "read",
            "accesses-0-origin": "direct", "accesses-0-granted_by": self.admin.pk,
        })
        self.assertEqual(self.entrees(), [(Journal.Action.ACCES_ACCORDE, "lecteur", True)])

    def test_modifier_puis_retirer_un_acces(self):
        lecteur = make_user("lecteur")
        acces = grant(self.dossier, lecteur, DossierAccess.Role.READ)
        self.client.post(f"/admin/events/dossieraccess/{acces.pk}/change/", {
            "dossier": self.dossier.pk, "user": lecteur.pk, "role": "write",
            "origin": "direct", "granted_by": self.admin.pk,
        })
        self.client.post(f"/admin/events/dossieraccess/{acces.pk}/delete/", {"post": "yes"})
        self.assertEqual(self.entrees(), [
            (Journal.Action.ACCES_MODIFIE, "lecteur", True),
            (Journal.Action.ACCES_RETIRE, "lecteur", True),
        ])
        self.assertEqual(
            Journal.objects.get(action=Journal.Action.ACCES_MODIFIE).details["changements"],
            {"role": ["read", "write"]},
        )

    def test_lier_une_piece_a_un_autre_evenement_et_la_renommer(self):
        self.client.post(f"/admin/events/document/{self.piece.pk}/change/", {
            "dossier": self.dossier.pk, "title": "devis signé.pdf",
            "events": [self.event.pk, self.autre.pk],
        })
        actions = [a for a, _o, _v in self.entrees()]
        self.assertEqual(sorted(actions), sorted([Journal.Action.PIECE_MODIFIEE, Journal.Action.PIECE_AJOUTEE]))
        lien = Journal.objects.get(action=Journal.Action.PIECE_AJOUTEE)
        self.assertEqual(lien.evenement, self.autre)

    def test_supprimer_un_evenement(self):
        self.client.post(f"/admin/events/event/{self.autre.pk}/delete/", {"post": "yes"})
        entree = Journal.objects.get()
        self.assertEqual(entree.action, Journal.Action.EVENEMENT_SUPPRIME)
        self.assertEqual(entree.objet, "Constat")
        self.assertEqual(entree.details["valeurs"]["date"], "2026-08-20")

    def test_supprimer_une_piece(self):
        self.client.post(f"/admin/events/document/{self.piece.pk}/delete/", {"post": "yes"})
        entree = Journal.objects.get()
        self.assertEqual(entree.action, Journal.Action.PIECE_SUPPRIMEE)
        self.assertEqual(entree.details["empreinte"], self.piece.content_hash)

    def test_detacher_une_piece_depuis_l_evenement(self):
        """L'inline des pièces liées, sur la fiche de l'évènement."""
        lien = Document.events.through.objects.get(event=self.event, document=self.piece)
        prefixe = "Document_events"
        self.client.post(f"/admin/events/event/{self.event.pk}/change/", {
            "dossier": self.dossier.pk, "title": "Devis", "date": "2026-08-05", "description": "",
            f"{prefixe}-TOTAL_FORMS": "1", f"{prefixe}-INITIAL_FORMS": "1",
            f"{prefixe}-MIN_NUM_FORMS": "0", f"{prefixe}-MAX_NUM_FORMS": "1000",
            f"{prefixe}-0-id": lien.pk, f"{prefixe}-0-event": self.event.pk,
            f"{prefixe}-0-document": self.piece.pk, f"{prefixe}-0-DELETE": "on",
        })
        self.assertIn((Journal.Action.PIECE_RETIREE, "devis.pdf", True), self.entrees())

    def test_la_page_du_journal_lit_ces_entrees(self):
        self.test_modifier_puis_retirer_un_acces()
        html = self.client.get(reverse("dossier_journal", args=[self.dossier.pk])).content.decode()
        self.assertIn("Accès modifié", html)
        self.assertIn("via l'administration", html)
        self.assertIn("Lecture et écriture", html)


class ExportParUnLienTests(MediaMixin, TestCase):
    def setUp(self):
        owner = make_user("owner")
        self.dossier = make_dossier(owner, name="Litige")
        event = Event.objects.create(dossier=self.dossier, title="Devis", date="2026-08-05")
        contenu = b"%PDF devis"
        piece = Document.objects.create(
            dossier=self.dossier, title="devis.pdf",
            file=SimpleUploadedFile("devis.pdf", contenu),
            content_hash=hashlib.sha256(contenu).hexdigest(),
        )
        piece.events.add(event)
        self.lien = ShareLink.objects.create(
            dossier=self.dossier, created_by=owner, role=DossierAccess.Role.READ, label="Avocat",
        )
        self.url = reverse("share_dossier_export", args=[self.lien.token])

    def test_un_lien_en_lecture_exporte_le_dossier(self):
        reponse = self.client.post(self.url)
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(reponse["Referrer-Policy"], "no-referrer")
        archive = zipfile.ZipFile(io.BytesIO(b"".join(reponse.streaming_content)))
        self.assertIn("pieces/001-devis.pdf", archive.namelist())
        self.assertIn("Avocat", archive.read("LISEZMOI.txt").decode())

    def test_l_export_est_inscrit_au_nom_du_lien(self):
        self.client.post(self.url)
        entree = Journal.objects.get()
        self.assertEqual(entree.action, Journal.Action.EXPORT)
        self.assertEqual(entree.lien, self.lien)
        self.assertIsNone(entree.auteur)

    def test_un_get_n_exporte_rien(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertFalse(Journal.objects.exists())

    def test_un_lien_revoque_n_exporte_rien(self):
        self.lien.revoked_at = timezone.now()
        self.lien.save()
        self.assertEqual(self.client.post(self.url).status_code, 404)

    def test_le_bouton_est_sur_les_pages_du_lien(self):
        html = self.client.get(reverse("share_timeline", args=[self.lien.token])).content.decode()
        self.assertIn('action="%s"' % self.url, html)
