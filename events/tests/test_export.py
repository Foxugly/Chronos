"""L'export d'un dossier : un zip que l'on peut remettre à un avocat."""

import hashlib
import io
import shutil
import tempfile
import zipfile
from pathlib import Path

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from events.export import _description_en_paragraphes
from events.models import Document, DossierAccess, Event, Journal
from events.tests.factories import grant, make_dossier, make_user


class ExportTests(TestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_root = tempfile.mkdtemp()
        cls.enterClassContext(override_settings(MEDIA_ROOT=cls.media_root))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.media_root, ignore_errors=True)

    def setUp(self):
        self.owner = make_user("owner")
        self.lecteur = make_user("lecteur")
        self.dossier = make_dossier(self.owner, name="Litige SDB")
        grant(self.dossier, self.lecteur, DossierAccess.Role.READ)
        self.devis = Event.objects.create(
            dossier=self.dossier, title="Devis signé", date="2026-06-02",
            description="<p>J&#x27;ai signé</p><ul><li>un</li><li>deux</li></ul>",
        )
        self.constat = Event.objects.create(dossier=self.dossier, title="Constat", date="2026-08-20")
        self.contenu = b"%PDF devis"
        self.piece = Document.objects.create(
            dossier=self.dossier, title="devis réservé.pdf",
            file=SimpleUploadedFile("devis.pdf", self.contenu),
            content_hash=hashlib.sha256(self.contenu).hexdigest(),
        )
        self.piece.events.add(self.devis, self.constat)
        self.url = reverse("dossier_export", args=[self.dossier.pk])

    def exporter(self, user=None):
        self.client.force_login(user or self.owner)
        reponse = self.client.post(self.url)
        self.assertEqual(reponse.status_code, 200)
        return zipfile.ZipFile(io.BytesIO(b"".join(reponse.streaming_content)))

    def test_le_zip_contient_la_chronologie_les_pieces_et_les_empreintes(self):
        archive = self.exporter()
        noms = archive.namelist()
        self.assertIn("chronologie.pdf", noms)
        self.assertIn("LISEZMOI.txt", noms)
        self.assertIn("empreintes.sha256", noms)
        # Une pièce liée à deux évènements n'est exportée qu'une fois, en ASCII.
        self.assertEqual([n for n in noms if n.startswith("pieces/")], ["pieces/001-devis_reserve.pdf"])
        self.assertEqual(archive.read("pieces/001-devis_reserve.pdf"), self.contenu)

    def test_les_empreintes_se_verifient(self):
        archive = self.exporter()
        ligne = archive.read("empreintes.sha256").decode().strip()
        empreinte, nom = ligne.split("  ")
        self.assertEqual(empreinte, hashlib.sha256(archive.read(nom)).hexdigest())

    def test_la_chronologie_porte_les_faits_et_le_controle(self):
        pdf = self.exporter().read("chronologie.pdf")
        self.assertTrue(pdf.startswith(b"%PDF"))
        for texte in (b"Devis sign", b"Constat", b"conforme", self.piece.content_hash.encode()):
            self.assertIn(texte, pdf)

    def test_un_fichier_altere_est_signale(self):
        """Le fichier a changé sur le disque depuis son dépôt : l'export le
        dit au lieu de le masquer."""
        Path(self.piece.file.path).write_bytes(b"autre chose")
        pdf = self.exporter().read("chronologie.pdf")
        self.assertIn(b"DIFF", pdf)
        self.assertIn(b"Attention", pdf)

    def test_l_export_est_inscrit_au_journal(self):
        self.exporter(self.lecteur)
        entree = Journal.objects.get(action=Journal.Action.EXPORT)
        self.assertEqual(entree.auteur, self.lecteur)

    def test_un_get_n_exporte_rien(self):
        """Un lien préchargé ne doit pas inscrire d'export au journal."""
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertFalse(Journal.objects.exists())

    def test_un_etranger_recoit_un_404(self):
        self.client.force_login(make_user("etranger"))
        self.assertEqual(self.client.post(self.url).status_code, 404)

    def test_la_description_devient_du_texte(self):
        self.assertEqual(
            _description_en_paragraphes(self.devis.description),
            ["J'ai signé", "• un", "• deux"],
        )
