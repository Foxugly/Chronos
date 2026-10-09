"""Numéros de pièces définitifs.

Une pièce reçoit son numéro au dépôt, par tous les chemins, et le garde :
jamais changé, jamais réattribué — même après la suppression de la dernière
pièce. Deux dossiers ont chacun leur numérotation. L'export les reprend.
"""

import io
import zipfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from events.models import Document, Event
from events.tests.factories import make_document, make_dossier
from events.tests.test_documents import DocumentFixtureMixin


class NumerosTests(DocumentFixtureMixin, TestCase):
    def test_premiere_piece_numero_un_puis_a_la_suite(self):
        self.assertEqual(self.document.numero, 1)
        autre = make_document(self.event, title="facture.pdf", content=b"facture")
        self.assertEqual(autre.numero, 2)

    def test_chaque_dossier_a_sa_numerotation(self):
        ailleurs = Event.objects.create(dossier=make_dossier(self.owner, "Autre"), title="X", date="2026-01-01")
        self.assertEqual(make_document(ailleurs, title="a.pdf", content=b"a").numero, 1)

    def test_un_numero_n_est_jamais_reattribue(self):
        deuxieme = make_document(self.event, title="b.pdf", content=b"b")
        self.assertEqual(deuxieme.numero, 2)
        deuxieme.delete()
        troisieme = make_document(self.event, title="c.pdf", content=b"c")
        self.assertEqual(troisieme.numero, 3)

    def test_le_numero_ne_change_pas_a_la_modification(self):
        self.document.title = "devis-renomme.pdf"
        self.document.save()
        self.document.refresh_from_db()
        self.assertEqual(self.document.numero, 1)

    def test_depot_par_le_formulaire(self):
        self.client.force_login(self.owner)
        self.client.post(reverse("event_edit", args=[self.event.pk]), {
            "title": "Devis signé", "date": "2026-06-02",
            "documents": [SimpleUploadedFile("photo.jpg", b"jpeg")],
        })
        self.assertEqual(Document.objects.get(title="photo.jpg").numero, 2)

    def test_l_export_partiel_garde_les_vrais_numeros(self):
        autre = Event.objects.create(dossier=self.dossier, title="Facture", date="2026-07-01")
        make_document(autre, title="facture.pdf", content=b"facture")
        self.client.force_login(self.owner)
        reponse = self.client.post(reverse("dossier_export", args=[self.dossier.pk]), {"q": "facture"})
        archive = zipfile.ZipFile(io.BytesIO(b"".join(reponse.streaming_content)))
        self.assertEqual([n for n in archive.namelist() if n.startswith("pieces/")],
                         ["pieces/002-facture.pdf"])

    def test_numero_affiche_et_cherchable(self):
        self.client.force_login(self.owner)
        reponse = self.client.get(reverse("timeline", args=[self.dossier.pk]), {"vue": "documents", "q": "1"})
        self.assertEqual([l["document"].numero for l in reponse.context["pieces"]], [1])
        detail = self.client.get(reverse("event_detail", args=[self.event.pk]))
        self.assertContains(detail, "Pièce 1")
