"""Le lien « transfert de » dans la vue liste et dans l'export PDF."""

import io
import shutil
import tempfile
import zipfile

from django.test import TestCase, override_settings
from django.urls import reverse

from events.models import DossierAccess, Event, ShareLink
from events.tests.factories import make_dossier, make_user


class LiensDeTransfertTests(TestCase):
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
        self.dossier = make_dossier(self.owner, name="ACMEBAT")
        self.mise_en_demeure = Event.objects.create(
            dossier=self.dossier, title="Mise en demeure", date="2026-09-28")
        self.transfert = Event.objects.create(
            dossier=self.dossier, title="TR: Mise en demeure", date="2026-10-07",
            transfert_de=self.mise_en_demeure)
        self.client.force_login(self.owner)

    def liste(self, url=None):
        url = url or reverse("timeline", args=[self.dossier.pk])
        return self.client.get(url + "?vue=liste").content.decode()

    def test_la_liste_relie_les_deux_evenements(self):
        page = self.liste()
        self.assertIn("Transfert de :", page)
        self.assertIn("Transféré ensuite :", page)
        self.assertIn(reverse("event_detail", args=[self.mise_en_demeure.pk]), page)
        self.assertIn(reverse("event_detail", args=[self.transfert.pk]), page)

    def test_par_un_lien_de_partage_les_liens_restent_dans_le_partage(self):
        lien = ShareLink.objects.create(dossier=self.dossier, created_by=self.owner,
                                        role=DossierAccess.Role.READ)
        self.client.logout()
        page = self.liste(reverse("share_timeline", args=[lien.token]))
        self.assertIn(reverse("share_event_detail", args=[lien.token, self.mise_en_demeure.pk]), page)
        self.assertNotIn('href="' + reverse("event_detail", args=[self.mise_en_demeure.pk]), page)

    def test_sans_transfert_rien_n_est_affiche(self):
        Event.objects.filter(pk=self.transfert.pk).update(transfert_de=None)
        page = self.liste()
        self.assertNotIn("Transfert de :", page)
        self.assertNotIn("Transféré ensuite :", page)

    def exporter(self, **donnees):
        reponse = self.client.post(reverse("dossier_export", args=[self.dossier.pk]), donnees)
        self.assertEqual(reponse.status_code, 200)
        archive = zipfile.ZipFile(io.BytesIO(b"".join(reponse.streaming_content)))
        return archive.read("chronologie.pdf")

    def test_l_export_pdf_porte_les_liens(self):
        pdf = self.exporter()
        self.assertIn(b"Transfert de : Mise en demeure", pdf)
        self.assertIn(b"ensuite : TR: Mise en demeure", pdf)

    def test_un_export_partiel_signale_l_evenement_hors_selection(self):
        pdf = self.exporter(du="2026-10", au="2026-10")
        self.assertIn(b"Transfert de : Mise en demeure", pdf)
        self.assertIn(b"hors de cette s", pdf)
