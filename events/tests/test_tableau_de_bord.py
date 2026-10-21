"""L'accueil en tableau de bord : un dossier par carte, rien de plus que ses droits."""

from django.test import TestCase
from django.urls import reverse

from events.models import CourrielImporte, DossierAccess, Event
from events.tests.factories import grant, make_user
from events.tests.test_documents import DocumentFixtureMixin


class TableauDeBordTests(DocumentFixtureMixin, TestCase):
    def cartes(self, qui):
        self.client.force_login(qui)
        return {c["dossier"].pk: c for c in self.client.get(reverse("dossier_list")).context["cartes"]}

    def test_role_et_volumes(self):
        carte = self.cartes(self.owner)[self.dossier.pk]
        self.assertEqual(str(carte["role"]), "Propriétaire")
        self.assertEqual(carte["dossier"].event_count, 1)
        self.assertEqual(carte["dossier"].piece_count, 1)
        self.assertEqual(str(self.cartes(self.reader)[self.dossier.pk]["role"]), "Lecture")
        writer = make_user("writer")
        grant(self.dossier, writer, DossierAccess.Role.WRITE)
        self.assertEqual(str(self.cartes(writer)[self.dossier.pk]["role"]), "Écriture")

    def test_un_inconnu_ne_voit_pas_le_dossier(self):
        self.assertNotIn(self.dossier.pk, self.cartes(self.stranger))

    def test_derniers_faits_du_plus_recent_au_plus_ancien(self):
        Event.objects.create(dossier=self.dossier, title="Plus tard", date="2026-09-01")
        titres = [e.title for e in self.cartes(self.owner)[self.dossier.pk]["derniers_faits"]]
        self.assertEqual(titres, ["Plus tard", "Devis signé"])

    def test_doublons_seulement_pour_qui_peut_ecrire(self):
        double = Event.objects.create(dossier=self.dossier, title="Devis signé (copie)", date="2026-06-02")
        self.document.events.add(double)
        self.assertEqual(self.cartes(self.owner)[self.dossier.pk]["nb_doublons"], 1)
        self.assertEqual(self.cartes(self.reader)[self.dossier.pk]["nb_doublons"], 0)

    def test_derniers_imports_et_alias_pour_le_proprietaire(self):
        CourrielImporte.objects.create(dossier=self.dossier, message_id="<x>", evenement=self.event)
        self.dossier.alias_courriel = "sdb-x@exemple.be"
        self.dossier.save()
        self.client.force_login(self.owner)
        page = self.client.get(reverse("dossier_list"))
        self.assertContains(page, "sdb-x@exemple.be")
        self.assertEqual(len(page.context["cartes"][0]["derniers_imports"]), 1)
        self.client.force_login(self.reader)
        self.assertNotContains(self.client.get(reverse("dossier_list")), "sdb-x@exemple.be")
