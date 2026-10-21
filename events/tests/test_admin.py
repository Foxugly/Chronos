import shutil
import tempfile

from django.test import TestCase, override_settings

from events.models import Dossier, DossierAccess, Event
from events.tests.factories import make_document, make_dossier, make_user


class MediaTemporaireMixin:
    """Écrit les pièces d'essai hors du `media/` réel.

    Sans cela, un test qui crée un `Document` dépose un vrai fichier dans le
    répertoire des preuves du dossier — c'est arrivé, deux fichiers de 16
    octets s'y sont retrouvés. Une suite de tests ne doit rien laisser dans
    les données de production.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.media_root = tempfile.mkdtemp()
        cls.enterClassContext(override_settings(MEDIA_ROOT=cls.media_root))

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(cls.media_root, ignore_errors=True)


class AdminAccessTests(MediaTemporaireMixin, TestCase):
    """L'admin est testé en l'exerçant, pas en relisant `admin.py`.

    Les tests précédents répétaient la configuration ligne pour ligne
    (`assertIn("dossier", list_display)`…) : ils viraient au rouge dès qu'on
    renommait une colonne et restaient au vert quand l'admin ne s'affichait
    plus du tout. Ce qui casse réellement — un `fk_name` ou un
    `autocomplete_fields` mal posé — ne se voit qu'en demandant la page.
    """

    def setUp(self):
        self.admin = make_user("admin", is_staff=True, is_superuser=True)
        self.dossier = make_dossier(self.admin)
        self.client.force_login(self.admin)

    def test_superuser_reaches_the_dossier_changelist(self):
        response = self.client.get("/admin/events/dossier/")
        self.assertEqual(response.status_code, 200)

    def test_the_dossier_add_page_renders_the_access_inline(self):
        """C'est l'unique moyen de partager un dossier tant que L4 n'a pas
        livré l'interface d'invitation : si l'inline ne s'affiche pas, plus
        personne ne peut accorder d'accès."""
        response = self.client.get("/admin/events/dossier/add/")
        self.assertEqual(response.status_code, 200)

        # Le formset de l'inline est bien construit et rendu.
        self.assertContains(response, "accesses-TOTAL_FORMS")
        self.assertContains(response, "accesses-0-role")
        self.assertContains(response, "accesses-0-user")

        # `autocomplete_fields = ("user",)` : le widget d'auto-complétion
        # dépend du `search_fields` de l'admin de `User`. Sans lui, la page
        # part en erreur de contrôle plutôt qu'en liste déroulante.
        self.assertContains(response, "admin-autocomplete")

    def test_the_dossier_change_page_renders_an_existing_access(self):
        other = make_user("collaborateur")
        DossierAccess.objects.create(
            dossier=self.dossier, user=other, role=DossierAccess.Role.WRITE
        )
        response = self.client.get(f"/admin/events/dossier/{self.dossier.pk}/change/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "accesses-0-id")


class AdminDeletionIsRefusedTests(MediaTemporaireMixin, TestCase):
    """I2 : un dossier supprimé emporte ses évènements et ses pièces.

    `Event` et `Document` sont en `CASCADE` : une seule erreur de clic sur
    la page d'un dossier détruisait les 24 évènements et les 49 documents,
    c'est-à-dire l'unique copie du dossier de preuves.
    """

    def setUp(self):
        self.admin = make_user("admin", is_staff=True, is_superuser=True)
        self.dossier = make_dossier(self.admin)
        self.event = Event.objects.create(
            dossier=self.dossier, title="Devis signé", date="2026-06-02"
        )
        self.client.force_login(self.admin)

    def test_the_dossier_delete_view_is_refused_to_a_superuser(self):
        response = self.client.get(
            f"/admin/events/dossier/{self.dossier.pk}/delete/"
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Dossier.objects.filter(pk=self.dossier.pk).exists())

    def test_posting_the_dossier_delete_confirmation_changes_nothing(self):
        """La propriété qui compte : ce n'est pas la page qui protège, c'est
        la permission. Un POST forgé doit échouer comme le GET."""
        response = self.client.post(
            f"/admin/events/dossier/{self.dossier.pk}/delete/", {"post": "yes"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Dossier.objects.filter(pk=self.dossier.pk).exists())
        self.assertTrue(Event.objects.filter(pk=self.event.pk).exists())

    def test_the_dossier_changelist_offers_no_bulk_delete(self):
        response = self.client.get("/admin/events/dossier/")
        self.assertNotContains(response, "delete_selected")

    def test_the_event_changelist_offers_no_bulk_delete(self):
        response = self.client.get("/admin/events/event/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "delete_selected")

    def test_the_document_changelist_offers_no_bulk_delete(self):
        make_document(self.event)
        response = self.client.get("/admin/events/document/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "delete_selected")
