"""La page Paramètres d'un dossier : son nom, son alias d'import par courriel.

Réservée au propriétaire — 404 pour tout autre, lecteur, collaborateur en
écriture ou inconnu (invariants 3 et 5). Chaque changement va au journal.
"""

from django.test import TestCase, override_settings
from django.urls import reverse

from events.models import Dossier, DossierAccess, Journal
from events.tests.factories import grant, make_dossier, make_user


class ParametresTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.writer = make_user("writer")
        self.reader = make_user("reader")
        self.stranger = make_user("stranger")
        self.dossier = make_dossier(self.owner, name="Salle de bain")
        grant(self.dossier, self.writer, DossierAccess.Role.WRITE)
        grant(self.dossier, self.reader, DossierAccess.Role.READ)
        self.url = reverse("dossier_parametres", args=[self.dossier.pk])
        self.url_alias = reverse("dossier_alias", args=[self.dossier.pk])

    def test_owner_sees_the_page(self):
        self.client.force_login(self.owner)
        reponse = self.client.get(self.url)
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "Import par courriel")
        self.assertContains(reponse, self.url_alias)

    def test_everyone_else_gets_404(self):
        for qui in (self.writer, self.reader, self.stranger):
            self.client.force_login(qui)
            self.assertEqual(self.client.get(self.url).status_code, 404)
            self.assertEqual(self.client.post(self.url, {"name": "Piraté"}).status_code, 404)
            self.assertEqual(self.client.post(self.url_alias, {"alias_courriel": "x@ex.be"}).status_code, 404)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.name, "Salle de bain")
        self.assertIsNone(self.dossier.alias_courriel)

    def test_header_button_for_owner_only(self):
        timeline = reverse("timeline", args=[self.dossier.pk]) + "?vue=liste"
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(timeline), self.url)
        self.client.force_login(self.writer)
        self.assertNotContains(self.client.get(timeline), self.url)

    def test_rename_is_journaled(self):
        self.client.force_login(self.owner)
        reponse = self.client.post(self.url, {"name": "  Litige SDB  "})
        self.assertRedirects(reponse, self.url)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.name, "Litige SDB")
        entree = Journal.objects.get(action=Journal.Action.DOSSIER_MODIFIE)
        self.assertEqual(entree.details["changements"]["name"], ["Salle de bain", "Litige SDB"])

    def test_unchanged_name_writes_nothing(self):
        self.client.force_login(self.owner)
        self.client.post(self.url, {"name": "Salle de bain"})
        self.assertFalse(Journal.objects.exists())

    def test_empty_name_is_refused(self):
        self.client.force_login(self.owner)
        reponse = self.client.post(self.url, {"name": "   "})
        self.assertEqual(reponse.status_code, 200)
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.name, "Salle de bain")

    def test_add_modify_and_disable_alias(self):
        self.client.force_login(self.owner)
        reponse = self.client.post(self.url_alias, {"alias_courriel": "sdb-a1@exemple.be"})
        self.assertRedirects(reponse, self.url)
        self.client.post(self.url_alias, {"alias_courriel": "sdb-b2@exemple.be"})
        self.dossier.refresh_from_db()
        self.assertEqual(self.dossier.alias_courriel, "sdb-b2@exemple.be")
        self.client.post(self.url_alias, {"alias_courriel": ""})
        self.dossier.refresh_from_db()
        self.assertIsNone(self.dossier.alias_courriel)
        changements = [e.details["changements"]["alias_courriel"]
                       for e in Journal.objects.order_by("pk")]
        self.assertEqual(changements, [["", "sdb-a1@exemple.be"],
                                       ["sdb-a1@exemple.be", "sdb-b2@exemple.be"],
                                       ["sdb-b2@exemple.be", ""]])

    def test_page_shows_current_alias_and_disable_button(self):
        self.dossier.alias_courriel = "sdb-a1@exemple.be"
        self.dossier.save()
        self.client.force_login(self.owner)
        reponse = self.client.get(self.url)
        self.assertContains(reponse, "sdb-a1@exemple.be")
        self.assertContains(reponse, "Désactiver l'import par courriel")

    def test_taken_alias_in_other_case_is_refused_cleanly(self):
        autre = make_dossier(self.owner, name="Autre")
        autre.alias_courriel = "pris@exemple.be"
        autre.save()
        self.client.force_login(self.owner)
        reponse = self.client.post(self.url_alias, {"alias_courriel": " Pris@Exemple.be "}, follow=True)
        self.assertContains(reponse, "déjà utilisé")
        self.dossier.refresh_from_db()
        self.assertIsNone(self.dossier.alias_courriel)

    @override_settings(COURRIEL_BOITE="import@exemple.be")
    def test_mailbox_itself_is_refused(self):
        self.client.force_login(self.owner)
        self.client.post(self.url_alias, {"alias_courriel": "Import@exemple.be"})
        self.dossier.refresh_from_db()
        self.assertIsNone(self.dossier.alias_courriel)

    @override_settings(COURRIEL_BOITE="import@exemple.be")
    def test_proposal_uses_the_mailbox_domain(self):
        self.client.force_login(self.owner)
        proposition = self.client.get(self.url).context["alias_propose"]
        self.assertRegex(proposition, r"^salle-de-bain-[0-9a-f]{6}@exemple\.be$")
        self.assertFalse(Dossier.objects.filter(alias_courriel=proposition).exists())

    @override_settings(COURRIEL_BOITE="")
    def test_no_proposal_without_a_mailbox(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self.url).context["alias_propose"], "")

    def test_share_page_no_longer_holds_the_alias_form(self):
        self.client.force_login(self.owner)
        reponse = self.client.get(reverse("dossier_share", args=[self.dossier.pk]))
        self.assertNotContains(reponse, self.url_alias)
