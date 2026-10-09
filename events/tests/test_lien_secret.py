"""Lien de consultation par jeton, utilisable **sans compte**.

Troisième mécanisme de partage, et le seul accès anonyme de l'application :
il contredit délibérément la règle « tout est privé » posée en L1. Le
propriétaire l'a demandé en connaissance de cause.

Les vues forment un chemin séparé : elles ne passent par aucun helper de
`events.permissions`, ne lisent jamais `request.user`, et bornent chaque
requête au dossier du lien. Ces tests gardent cette séparation autant que le
comportement.
"""

import hashlib
import re
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from events.models import Document, DossierAccess, Event, ShareLink
from events.tests.factories import grant, make_dossier, make_user


class LienFixtureMixin:
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
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        self.autre = make_dossier(self.owner, name="Autre affaire")
        self.event = Event.objects.create(
            dossier=self.dossier, title="Devis signe", date="2026-06-02"
        )
        self.event_ailleurs = Event.objects.create(
            dossier=self.autre, title="Secret", date="2026-06-02"
        )
        contenu = b"%PDF-1.4 piece"
        self.document = Document.objects.create(
            dossier=self.dossier,
            title="devis.pdf",
            file=SimpleUploadedFile("devis.pdf", contenu),
            content_hash=hashlib.sha256(contenu).hexdigest(),
        )
        self.document.events.add(self.event)
        self.lecture = ShareLink.objects.create(
            dossier=self.dossier,
            created_by=self.owner,
            role=DossierAccess.Role.READ,
        )
        self.ecriture = ShareLink.objects.create(
            dossier=self.dossier,
            created_by=self.owner,
            role=DossierAccess.Role.WRITE,
        )
        # Aucun `force_login` : tout se joue en visiteur anonyme.
        self.anon = self.client


class ReadOnlyLinkTests(LienFixtureMixin, TestCase):
    def test_an_anonymous_visitor_reads_the_dossier(self):
        reponse = self.anon.get(
            reverse("share_timeline", args=[self.lecture.token])
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, self.dossier.name)

    def test_the_pages_refuse_to_leak_the_token_or_be_indexed(self):
        """Le jeton voyage dans l'URL : sans `no-referrer`, il partirait dans
        l'en-tête `Referer` vers tout site tiers atteint depuis ces pages."""
        reponse = self.anon.get(
            reverse("share_timeline", args=[self.lecture.token])
        )
        self.assertEqual(reponse["Referrer-Policy"], "no-referrer")
        self.assertIn("noindex", reponse["X-Robots-Tag"])

    def test_documents_are_served(self):
        reponse = self.anon.get(
            reverse(
                "share_document_download", args=[self.lecture.token, self.document.pk]
            )
        )
        self.assertEqual(reponse.status_code, 200)

    def test_nothing_that_writes_is_reachable(self):
        """Un lien en lecture répond 404 et non 403 : il ne doit pas
        apprendre à qui le détient qu'une porte existe ailleurs."""
        for url in (
            reverse("share_event_add", args=[self.lecture.token]),
            reverse("share_event_edit", args=[self.lecture.token, self.event.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.anon.get(url).status_code, 404)

    def test_the_page_offers_no_action_that_would_be_refused(self):
        """Proposer un bouton qui mène à un 404 est une promesse que
        l'application ne tient pas."""
        html = self.anon.get(
            reverse("share_timeline", args=[self.lecture.token])
        ).content.decode()
        self.assertIn("const LECTURE_SEULE = true", html)
        # Le modal est là — il montre le détail, lire est permis — mais
        # aucun lien n'y charge de formulaire.
        self.assertIsNone(re.search(r"<a [^>]*data-ouvrir-formulaire", html))


class LinkIsBoundToOneDossierTests(LienFixtureMixin, TestCase):
    def test_an_event_of_another_dossier_is_invisible(self):
        reponse = self.anon.get(
            reverse(
                "share_event_detail",
                args=[self.lecture.token, self.event_ailleurs.pk],
            )
        )
        self.assertEqual(reponse.status_code, 404)

    def test_a_document_of_another_dossier_is_invisible(self):
        contenu = b"%PDF-1.4 ailleurs"
        ailleurs = Document.objects.create(
            dossier=self.autre,
            title="secret.pdf",
            file=SimpleUploadedFile("secret.pdf", contenu),
            content_hash=hashlib.sha256(contenu).hexdigest(),
        )
        ailleurs.events.add(self.event_ailleurs)
        reponse = self.anon.get(
            reverse("share_document_download", args=[self.lecture.token, ailleurs.pk])
        )
        self.assertEqual(reponse.status_code, 404)

    def test_the_link_opens_nothing_of_the_authenticated_application(self):
        """Il ne doit donner ni session ni droit ailleurs."""
        self.anon.get(reverse("share_timeline", args=[self.lecture.token]))
        for url in (
            reverse("dossier_list"),
            reverse("timeline", args=[self.dossier.pk]),
            reverse("dossier_share", args=[self.dossier.pk]),
        ):
            with self.subTest(url=url):
                reponse = self.anon.get(url)
                self.assertEqual(reponse.status_code, 302)
                self.assertIn("/accounts/login/", reponse["Location"])


class WriteLinkTests(LienFixtureMixin, TestCase):
    def test_an_anonymous_visitor_can_create_an_event(self):
        reponse = self.anon.post(
            reverse("share_event_add", args=[self.ecriture.token]),
            {"title": "Ajoute par lien", "date": "2026-10-01", "description": ""},
        )
        self.assertEqual(reponse.status_code, 302)
        cree = Event.objects.get(title="Ajoute par lien")
        self.assertEqual(cree.dossier, self.dossier)

    def test_an_anonymous_visitor_can_edit_an_event(self):
        self.anon.post(
            reverse("share_event_edit", args=[self.ecriture.token, self.event.pk]),
            {"title": "Modifie par lien", "date": "2026-06-02", "description": ""},
        )
        self.event.refresh_from_db()
        self.assertEqual(self.event.title, "Modifie par lien")

    def test_an_event_of_another_dossier_cannot_be_edited(self):
        reponse = self.anon.post(
            reverse(
                "share_event_edit",
                args=[self.ecriture.token, self.event_ailleurs.pk],
            ),
            {"title": "Pirate", "date": "2026-06-02", "description": ""},
        )
        self.assertEqual(reponse.status_code, 404)
        self.event_ailleurs.refresh_from_db()
        self.assertEqual(self.event_ailleurs.title, "Secret")

    def test_detaching_the_last_link_deletes_the_file(self):
        stockage = self.document.file.storage
        chemin = self.document.file.name
        reponse = self.anon.post(
            reverse(
                "share_document_detach",
                args=[self.ecriture.token, self.event.pk, self.document.pk],
            )
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertTrue(reponse.json()["supprime"])
        self.assertFalse(stockage.exists(chemin))

    def test_a_get_cannot_detach(self):
        reponse = self.anon.get(
            reverse(
                "share_document_detach",
                args=[self.ecriture.token, self.event.pk, self.document.pk],
            )
        )
        self.assertEqual(reponse.status_code, 405)
        self.assertTrue(Document.objects.filter(pk=self.document.pk).exists())

    def test_a_write_link_still_cannot_share_the_dossier(self):
        """Le droit d'écriture ne se propage jamais en droit de partage,
        pas même par un lien."""
        reponse = self.anon.get(reverse("dossier_share", args=[self.dossier.pk]))
        self.assertEqual(reponse.status_code, 302)
        self.assertIn("/accounts/login/", reponse["Location"])


class RevocationAndExpiryTests(LienFixtureMixin, TestCase):
    def test_a_revoked_link_is_dead(self):
        self.lecture.revoked_at = timezone.now()
        self.lecture.save(update_fields=["revoked_at"])
        self.assertEqual(
            self.anon.get(
                reverse("share_timeline", args=[self.lecture.token])
            ).status_code,
            404,
        )

    def test_an_expired_link_is_dead(self):
        self.lecture.expires_at = timezone.now() - timezone.timedelta(days=1)
        self.lecture.save(update_fields=["expires_at"])
        self.assertEqual(
            self.anon.get(
                reverse("share_timeline", args=[self.lecture.token])
            ).status_code,
            404,
        )

    def test_a_link_without_expiry_stays_open(self):
        """La durée illimitée est possible, sur décision explicite."""
        self.assertIsNone(self.lecture.expires_at)
        self.assertTrue(self.lecture.is_active)

    def test_an_unknown_token_answers_like_a_revoked_one(self):
        """Distinguer les deux dirait à qui tâtonne qu'il a trouvé un jeton
        réel."""
        self.lecture.revoked_at = timezone.now()
        self.lecture.save(update_fields=["revoked_at"])
        revoque = self.anon.get(
            reverse("share_timeline", args=[self.lecture.token])
        )
        inconnu = self.anon.get(
            reverse("share_timeline", args=["jeton-qui-n-existe-pas"])
        )
        self.assertEqual(revoque.status_code, inconnu.status_code)

    def test_use_is_counted(self):
        """Sans ce compteur, rien ne signale qu'un lien transmis à une
        personne est consulté par dix."""
        self.anon.get(reverse("share_timeline", args=[self.lecture.token]))
        self.anon.get(reverse("share_timeline", args=[self.lecture.token]))
        self.lecture.refresh_from_db()
        self.assertEqual(self.lecture.use_count, 2)
        self.assertIsNotNone(self.lecture.last_used_at)


class OnlyTheOwnerManagesLinksTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.writer = make_user("writer")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        grant(self.dossier, self.writer, DossierAccess.Role.WRITE)

    def test_the_owner_creates_a_link(self):
        self.client.force_login(self.owner)
        reponse = self.client.post(
            reverse("share_link_create", args=[self.dossier.pk]),
            {"label": "Avocat", "role": DossierAccess.Role.READ, "duree": "30"},
        )
        self.assertEqual(reponse.status_code, 302)
        lien = ShareLink.objects.get()
        self.assertEqual(lien.created_by, self.owner)
        self.assertIsNotNone(lien.expires_at)

    def test_the_owner_may_choose_no_expiry(self):
        self.client.force_login(self.owner)
        self.client.post(
            reverse("share_link_create", args=[self.dossier.pk]),
            {"label": "Expert", "role": DossierAccess.Role.WRITE, "duree": ""},
        )
        self.assertIsNone(ShareLink.objects.get().expires_at)

    def test_a_writer_cannot_create_a_link(self):
        self.client.force_login(self.writer)
        reponse = self.client.post(
            reverse("share_link_create", args=[self.dossier.pk]),
            {"label": "Complice", "role": DossierAccess.Role.WRITE, "duree": ""},
        )
        self.assertEqual(reponse.status_code, 404)
        self.assertFalse(ShareLink.objects.exists())

    def test_a_writer_cannot_revoke_a_link(self):
        lien = ShareLink.objects.create(dossier=self.dossier, created_by=self.owner)
        self.client.force_login(self.writer)
        reponse = self.client.post(
            reverse("share_link_revoke", args=[self.dossier.pk, lien.pk])
        )
        self.assertEqual(reponse.status_code, 404)
        lien.refresh_from_db()
        self.assertTrue(lien.is_active)

    def test_a_link_of_another_dossier_cannot_be_revoked_from_here(self):
        autre = make_dossier(self.owner, name="Autre affaire")
        ailleurs = ShareLink.objects.create(dossier=autre, created_by=self.owner)
        self.client.force_login(self.owner)
        reponse = self.client.post(
            reverse("share_link_revoke", args=[self.dossier.pk, ailleurs.pk])
        )
        self.assertEqual(reponse.status_code, 404)
        ailleurs.refresh_from_db()
        self.assertTrue(ailleurs.is_active)
