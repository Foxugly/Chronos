"""Deux points de l'audit du partage (2026-11-17), restés « plausibles » :

- retirer une pièce d'un évènement auquel elle n'est pas liée journalisait un
  retrait qui n'a pas eu lieu (et supprimait une pièce orpheline au passage) ;
- le compteur d'un lien comptait aussi les requêtes HEAD (robots, aperçus).
"""

import shutil
import tempfile

from django.test import TestCase, override_settings
from django.urls import reverse

from events.models import Document, DossierAccess, Event, Journal, ShareLink
from events.tests.factories import make_document, make_dossier, make_user


class MediaTemporaireMixin:
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_root = tempfile.mkdtemp()
        cls.enterClassContext(override_settings(MEDIA_ROOT=cls.media_root))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.media_root, ignore_errors=True)


class DetacherUnePieceNonLieeTests(MediaTemporaireMixin, TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.a = Event.objects.create(dossier=self.dossier, title="A", date="2026-06-01")
        self.b = Event.objects.create(dossier=self.dossier, title="B", date="2026-06-02")
        self.piece = make_document(self.b)  # liée à B seulement
        self.lien = ShareLink.objects.create(dossier=self.dossier, created_by=self.owner,
                                             role=DossierAccess.Role.WRITE)

    def verifier_rien_n_a_change(self, reponse):
        self.assertEqual(reponse.status_code, 404)
        self.assertTrue(Document.objects.filter(pk=self.piece.pk, events=self.b).exists())
        self.assertFalse(Journal.objects.exists())

    def test_par_un_compte(self):
        self.client.force_login(self.owner)
        reponse = self.client.post(reverse("document_detach", args=[self.a.pk, self.piece.pk]))
        self.verifier_rien_n_a_change(reponse)

    def test_par_un_lien(self):
        reponse = self.client.post(
            reverse("share_document_detach", args=[self.lien.token, self.a.pk, self.piece.pk]))
        self.verifier_rien_n_a_change(reponse)

    def test_une_piece_orpheline_ne_se_supprime_pas_par_un_evenement_quelconque(self):
        orpheline = make_document(self.b, title="orpheline.pdf", content=b"autre")
        orpheline.events.clear()
        self.client.force_login(self.owner)
        reponse = self.client.post(reverse("document_detach", args=[self.a.pk, orpheline.pk]))
        self.assertEqual(reponse.status_code, 404)
        self.assertTrue(Document.objects.filter(pk=orpheline.pk).exists())

    def test_le_cas_normal_marche_toujours(self):
        self.client.force_login(self.owner)
        reponse = self.client.post(reverse("document_detach", args=[self.b.pk, self.piece.pk]))
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(Document.objects.filter(pk=self.piece.pk).exists())


class CompteurDesConsultationsTests(TestCase):
    def setUp(self):
        owner = make_user("owner")
        self.lien = ShareLink.objects.create(dossier=make_dossier(owner), created_by=owner,
                                             role=DossierAccess.Role.READ)
        self.url = reverse("share_timeline", args=[self.lien.token])

    def test_un_head_ne_compte_pas(self):
        self.assertEqual(self.client.head(self.url).status_code, 200)
        self.lien.refresh_from_db()
        self.assertEqual(self.lien.use_count, 0)
        self.assertIsNone(self.lien.last_used_at)

    def test_un_get_compte(self):
        self.client.get(self.url)
        self.lien.refresh_from_db()
        self.assertEqual(self.lien.use_count, 1)
