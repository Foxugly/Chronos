"""La version texte d'un courriel (`.md`) n'est jamais affichée, mais reste une pièce.

Elle sert à la recherche : on la lit, on ne la montre pas. Le courriel (`.eml`)
la représente partout ; l'export, archive complète, la garde.
"""

import io
import zipfile

from django.test import TestCase
from django.urls import reverse

from events.models import Event
from events.tests.factories import make_document, make_dossier, make_user


class VersionTexteMasqueeTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.evenement = Event.objects.create(dossier=self.dossier, title="Mise en demeure", date="2026-09-28")
        self.eml = make_document(self.evenement, "courriel-20260928-2029.eml", b"From: x\r\n\r\ncorps")
        self.md = make_document(self.evenement, "courriel-20260928-2029.md", b"# Mise en demeure\n\ncarrelage fissure")
        self.pdf = make_document(self.evenement, "devis.pdf", b"%PDF-devis")
        self.client.force_login(self.owner)
        self.base = reverse("timeline", args=[self.dossier.pk])

    def test_pieces_affichees(self):
        self.assertTrue(self.md.est_version_texte)
        self.assertFalse(self.eml.est_version_texte)
        self.assertEqual({d.title for d in self.evenement.pieces_affichees},
                         {"courriel-20260928-2029.eml", "devis.pdf"})

    def test_ni_dans_la_liste(self):
        page = self.client.get(self.base, {"vue": "liste"})
        self.assertContains(page, "courriel-20260928-2029.eml")
        self.assertNotContains(page, "courriel-20260928-2029.md")

    def test_ni_dans_le_detail(self):
        page = self.client.get(reverse("event_detail", args=[self.evenement.pk]))
        self.assertContains(page, "devis.pdf")
        self.assertNotContains(page, "courriel-20260928-2029.md")

    def test_ni_dans_le_formulaire(self):
        page = self.client.get(reverse("event_edit", args=[self.evenement.pk]))
        self.assertContains(page, "devis.pdf")
        self.assertNotContains(page, "courriel-20260928-2029.md")

    def test_ni_dans_la_semaine(self):
        page = self.client.get(self.base, {"vue": "semaine", "periode": "2026-09-28"})
        self.assertContains(page, "2 pièces")

    def test_ni_dans_le_tableau_de_bord(self):
        page = self.client.get(reverse("dossier_list"))
        self.assertContains(page, "2 pièces")

    def test_onglet_documents_sans_md_ni_empreinte(self):
        page = self.client.get(self.base, {"vue": "documents"})
        self.assertContains(page, "devis.pdf")
        self.assertNotContains(page, "courriel-20260928-2029.md")
        self.assertNotContains(page, "SHA-256")
        self.assertNotContains(page, self.pdf.content_hash[:10])

    def test_le_texte_du_courriel_se_cherche_sur_son_eml(self):
        """Le mot n'est que dans le `.md` : c'est le courriel qui sort."""
        page = self.client.get(self.base, {"vue": "documents", "q": "carrelage"})
        self.assertContains(page, "courriel-20260928-2029.eml")
        self.assertNotContains(page, "devis.pdf")
        self.assertNotContains(page, "courriel-20260928-2029.md")

    def test_l_export_garde_la_version_texte(self):
        reponse = self.client.post(reverse("dossier_export", args=[self.dossier.pk]))
        noms = zipfile.ZipFile(io.BytesIO(b"".join(reponse.streaming_content)
                                          if reponse.streaming else reponse.content)).namelist()
        self.assertTrue(any(n.endswith(".md") and n.startswith("pieces/") for n in noms))
