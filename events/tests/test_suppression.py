"""Supprimer un évènement.

Ce qui doit tenir : seul qui peut écrire supprime (les autres reçoivent 404,
jamais 403) ; jamais sur un GET ; une pièce partagée avec un autre évènement
reste, une pièce qui n'illustrait que celui-ci part avec son fichier ; le
journal garde la trace de tout.
"""

import os

from django.test import TestCase
from django.urls import reverse

from events.models import CourrielImporte, Document, DossierAccess, Event, Journal, ShareLink
from events.tests.factories import grant, make_document, make_dossier, make_user
from events.tests.test_documents import DocumentFixtureMixin


class SuppressionFixtureMixin(DocumentFixtureMixin):
    def setUp(self):
        super().setUp()
        self.writer = make_user("writer")
        grant(self.dossier, self.writer, DossierAccess.Role.WRITE)
        self.autre = Event.objects.create(dossier=self.dossier, title="Facture", date="2026-06-10")
        # Une pièce partagée par les deux évènements, une propre à `self.event`.
        self.partagee = make_document(self.event, title="photo.jpg", content=b"photo")
        self.partagee.events.add(self.autre)
        self.url = reverse("event_delete", args=[self.event.pk])

    def supprimer(self, **extra):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(self.url, **extra)


class EventDeleteTests(SuppressionFixtureMixin, TestCase):
    def test_get_asks_for_confirmation_and_deletes_nothing(self):
        self.client.force_login(self.owner)
        reponse = self.client.get(self.url)
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Devis signé")
        self.assertContains(reponse, "data-confirmer-suppression")
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_confirmation_tells_which_documents_go_and_which_stay(self):
        self.client.force_login(self.owner)
        reponse = self.client.get(self.url)
        pieces = {p["document"].pk: p["supprimee"] for p in reponse.context["pieces"]}
        self.assertEqual(pieces, {self.document.pk: True, self.partagee.pk: False})

    def test_ajax_get_returns_the_fragment(self):
        self.client.force_login(self.owner)
        reponse = self.client.get(self.url, headers={"x-requested-with": "XMLHttpRequest"})
        self.assertNotContains(reponse, "<html")
        self.assertContains(reponse, "data-confirmer-suppression")

    def test_owner_deletes_event_and_its_own_document_with_its_file(self):
        chemin = self.document.file.path
        self.assertTrue(os.path.exists(chemin))
        self.client.force_login(self.owner)
        reponse = self.supprimer()
        self.assertRedirects(reponse, reverse("timeline", args=[self.dossier.pk]))
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())
        self.assertFalse(Document.objects.filter(pk=self.document.pk).exists())
        self.assertFalse(os.path.exists(chemin))

    def test_document_shared_with_another_event_is_kept(self):
        chemin = self.partagee.file.path
        self.client.force_login(self.owner)
        self.supprimer()
        self.partagee.refresh_from_db()
        self.assertEqual(list(self.partagee.events.all()), [self.autre])
        self.assertTrue(os.path.exists(chemin))

    def test_writer_may_delete(self):
        self.client.force_login(self.writer)
        self.supprimer()
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())

    def test_reader_and_stranger_get_404_and_nothing_moves(self):
        for qui in (self.reader, self.stranger):
            self.client.force_login(qui)
            self.assertEqual(self.client.get(self.url).status_code, 404)
            self.assertEqual(self.supprimer().status_code, 404)
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())
        self.assertTrue(Document.objects.filter(pk=self.document.pk).exists())

    def test_unknown_id_is_the_same_404(self):
        self.client.force_login(self.owner)
        reponse = self.client.post(reverse("event_delete", args=[999999]))
        self.assertEqual(reponse.status_code, 404)

    def test_journal_keeps_the_event_and_each_document(self):
        self.client.force_login(self.writer)
        self.supprimer()
        entrees = Journal.objects.filter(dossier=self.dossier)
        suppression = entrees.get(action=Journal.Action.EVENEMENT_SUPPRIME)
        self.assertEqual(suppression.objet, "Devis signé")
        self.assertEqual(suppression.auteur, self.writer)
        self.assertEqual(suppression.details["valeurs"]["title"], "Devis signé")
        self.assertTrue(entrees.filter(action=Journal.Action.PIECE_SUPPRIMEE, objet="devis.pdf").exists())
        self.assertTrue(entrees.filter(action=Journal.Action.PIECE_RETIREE, objet="photo.jpg").exists())

    def test_ajax_post_answers_json_with_the_id(self):
        self.client.force_login(self.owner)
        reponse = self.supprimer(headers={"x-requested-with": "XMLHttpRequest"})
        self.assertEqual(reponse.json(), {"success": True, "id": self.event.pk})

    def test_safe_retour_is_followed(self):
        self.client.force_login(self.owner)
        retour = reverse("timeline", args=[self.dossier.pk]) + "?vue=liste"
        reponse = self.supprimer(data={"retour": retour})
        self.assertRedirects(reponse, retour, fetch_redirect_response=False)

    def test_foreign_retour_is_ignored(self):
        self.client.force_login(self.owner)
        reponse = self.supprimer(data={"retour": "https://ailleurs.example/"})
        self.assertRedirects(reponse, reverse("timeline", args=[self.dossier.pk]))

    def test_imported_mail_may_be_imported_again(self):
        CourrielImporte.objects.create(dossier=self.dossier, message_id="<a@b>", evenement=self.event)
        self.client.force_login(self.owner)
        self.supprimer()
        self.assertFalse(CourrielImporte.objects.filter(message_id="<a@b>").exists())


class DeleteButtonTests(SuppressionFixtureMixin, TestCase):
    def test_detail_offers_delete_to_writer_only(self):
        url = reverse("event_detail", args=[self.event.pk])
        cible = reverse("event_delete", args=[self.event.pk])
        self.client.force_login(self.writer)
        self.assertContains(self.client.get(url), cible)
        self.client.force_login(self.reader)
        self.assertNotContains(self.client.get(url), cible)

    def test_list_view_offers_delete_to_writer_only(self):
        url = reverse("timeline", args=[self.dossier.pk]) + "?vue=liste"
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(url), reverse("event_delete", args=[self.event.pk]))
        self.client.force_login(self.reader)
        self.assertNotContains(self.client.get(url), reverse("event_delete", args=[self.event.pk]))


class ShareLinkDeleteTests(SuppressionFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.lecture = ShareLink.objects.create(
            dossier=self.dossier, created_by=self.owner, role=DossierAccess.Role.READ
        )
        self.ecriture = ShareLink.objects.create(
            dossier=self.dossier, created_by=self.owner, role=DossierAccess.Role.WRITE
        )

    def url_partage(self, lien, event_id=None):
        return reverse("share_event_delete", args=[lien.token, event_id or self.event.pk])

    def test_write_link_deletes_and_journal_names_the_link(self):
        with self.captureOnCommitCallbacks(execute=True):
            reponse = self.client.post(self.url_partage(self.ecriture))
        self.assertRedirects(reponse, reverse("share_timeline", args=[self.ecriture.token]),
                             fetch_redirect_response=False)
        self.assertFalse(Event.objects.filter(pk=self.event.pk).exists())
        entree = Journal.objects.get(action=Journal.Action.EVENEMENT_SUPPRIME)
        self.assertEqual(entree.lien, self.ecriture)

    def test_read_link_gets_404(self):
        self.assertEqual(self.client.post(self.url_partage(self.lecture)).status_code, 404)
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_link_cannot_reach_another_dossier(self):
        ailleurs = Event.objects.create(dossier=make_dossier(self.owner, "Autre"), title="X", date="2026-01-01")
        reponse = self.client.post(self.url_partage(self.ecriture, ailleurs.pk))
        self.assertEqual(reponse.status_code, 404)
        self.assertTrue(Event.objects.filter(pk=ailleurs.pk).exists())

    def test_get_shows_confirmation(self):
        reponse = self.client.get(self.url_partage(self.ecriture))
        self.assertContains(reponse, "data-confirmer-suppression")
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())
