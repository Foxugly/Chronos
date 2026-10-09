"""Le formulaire d'évènement en modal, et sa soumission sans rechargement.

La page complète reste servie et fonctionnelle : elle est le repli quand
JavaScript échoue. Ces tests vérifient que les deux chemins coexistent sans
que l'un casse l'autre.
"""

from django.test import TestCase
from django.urls import reverse

from events.models import Event
from events.tests.factories import make_dossier, make_user

AJAX = {"headers": {"X-Requested-With": "XMLHttpRequest"}}


class FormFragmentTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        self.event = Event.objects.create(
            dossier=self.dossier, title="Devis signe", date="2026-06-02"
        )
        self.client.force_login(self.owner)

    def _ajax_get(self, url):
        return self.client.get(url, headers={"x-requested-with": "XMLHttpRequest"})

    def test_an_ajax_get_returns_the_form_alone(self):
        """Le modal injecte ce fragment : il ne doit pas contenir de page."""
        page = self._ajax_get(
            reverse("event_add", args=[self.dossier.pk])
        ).content.decode()
        self.assertIn("data-event-form", page)
        self.assertNotIn("<!DOCTYPE html>", page)
        self.assertNotIn("navbar-brand", page)

    def test_a_plain_get_still_returns_the_whole_page(self):
        """Sans JavaScript, l'URL doit continuer de fonctionner seule."""
        page = self.client.get(
            reverse("event_add", args=[self.dossier.pk])
        ).content.decode()
        self.assertIn("<!DOCTYPE html>", page)
        self.assertIn("navbar-brand", page)
        self.assertIn("data-event-form", page)

    def test_the_two_renderings_use_distinct_field_ids(self):
        """La page et le modal peuvent coexister dans un même document :
        deux `id` identiques feraient pointer le label du second vers le
        champ du premier."""
        fragment = self._ajax_get(
            reverse("event_add", args=[self.dossier.pk])
        ).content.decode()
        page = self.client.get(
            reverse("event_add", args=[self.dossier.pk])
        ).content.decode()
        self.assertIn('id="documents-modal"', fragment)
        self.assertIn('id="documents"', page)

    def test_the_form_action_is_explicit_not_empty(self):
        """Dans le modal, un `action` vide pointerait vers la chronologie."""
        fragment = self._ajax_get(
            reverse("event_edit", args=[self.event.pk])
        ).content.decode()
        self.assertIn(reverse("event_edit", args=[self.event.pk]), fragment)


class AjaxSubmissionTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        self.client.force_login(self.owner)

    def _post(self, url, donnees):
        return self.client.post(
            url, donnees, headers={"x-requested-with": "XMLHttpRequest"}
        )

    def test_a_valid_ajax_creation_answers_with_the_event_in_json(self):
        """La chronologie se met à jour avec ces données, sans recharger."""
        reponse = self._post(
            reverse("event_add", args=[self.dossier.pk]),
            {"title": "Mise en demeure", "date": "2026-09-27", "description": ""},
        )
        self.assertEqual(reponse.status_code, 200)
        charge = reponse.json()
        self.assertTrue(charge["success"])
        cree = Event.objects.get(title="Mise en demeure")
        self.assertEqual(
            charge["event"],
            {"id": cree.pk, "content": "Mise en demeure", "start": "2026-09-27"},
        )

    def test_an_invalid_ajax_submission_answers_400_with_the_form(self):
        """Un 200 ferait croire au modal qu'il a enregistré."""
        reponse = self._post(
            reverse("event_add", args=[self.dossier.pk]),
            {"title": "", "date": "", "description": ""},
        )
        self.assertEqual(reponse.status_code, 400)
        page = reponse.content.decode()
        self.assertIn("data-event-form", page)
        self.assertNotIn("<!DOCTYPE html>", page)
        self.assertFalse(Event.objects.exists())

    def test_a_plain_submission_still_redirects(self):
        """Le repli sans JavaScript ne doit pas recevoir du JSON."""
        reponse = self.client.post(
            reverse("event_add", args=[self.dossier.pk]),
            {"title": "Mise en demeure", "date": "2026-09-27", "description": ""},
        )
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(
            reponse["Location"], reverse("timeline", args=[self.dossier.pk])
        )

    def test_an_ajax_edit_answers_with_the_updated_event(self):
        event = Event.objects.create(
            dossier=self.dossier, title="Avant", date="2026-06-02"
        )
        reponse = self._post(
            reverse("event_edit", args=[event.pk]),
            {"title": "Apres", "date": "2026-06-03", "description": ""},
        )
        self.assertEqual(reponse.status_code, 200)
        self.assertEqual(
            reponse.json()["event"],
            {"id": event.pk, "content": "Apres", "start": "2026-06-03"},
        )

    def test_a_reader_is_refused_through_ajax_too(self):
        """Le chemin AJAX ne doit pas être une porte dérobée."""
        from events.models import DossierAccess
        from events.tests.factories import grant

        lecteur = make_user("lecteur")
        grant(self.dossier, lecteur, DossierAccess.Role.READ)
        self.client.force_login(lecteur)
        reponse = self._post(
            reverse("event_add", args=[self.dossier.pk]),
            {"title": "Interdit", "date": "2026-09-27", "description": ""},
        )
        self.assertEqual(reponse.status_code, 404)
        self.assertFalse(Event.objects.filter(title="Interdit").exists())


class TimelineWiringTests(TestCase):
    """Le câblage côté page, à défaut de moteur JavaScript dans la suite.

    Le comportement réel a été vérifié au navigateur : ouverture du modal,
    création sans rechargement (24 → 25 évènements, témoin de page intact),
    modification pré-remplie, et retrait d'une pièce supprimant le fichier
    du disque quand c'était le dernier lien.
    """

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        self.client.force_login(self.owner)

    def _page(self):
        return self.client.get(
            reverse("timeline", args=[self.dossier.pk])
        ).content.decode()

    def test_the_timeline_carries_the_modal_and_its_wiring(self):
        page = self._page()
        self.assertIn('id="modal-evenement"', page)
        self.assertIn("data-ouvrir-formulaire", page)
        self.assertIn("items.update(data.event)", page)

    def test_the_new_event_button_keeps_a_real_href(self):
        """Sans JavaScript, le lien doit mener à la page complète."""
        page = self._page()
        self.assertIn(reverse("event_add", args=[self.dossier.pk]), page)

    def test_the_detach_button_is_wired_and_no_longer_disabled(self):
        fragment = self.client.get(
            reverse("event_add", args=[self.dossier.pk]),
            headers={"x-requested-with": "XMLHttpRequest"},
        ).content.decode()
        self.assertNotIn("Le retrait d'une pièce n'est pas encore actif", fragment)
        page = self._page()
        self.assertIn("equiperRetraitDesPieces", page)
