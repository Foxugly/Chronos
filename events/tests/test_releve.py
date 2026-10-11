"""Le bouton « Relever les courriels » d'un dossier, et le verrou qu'il partage
avec l'import planifié."""

from collections import Counter
from contextlib import contextmanager
from io import StringIO
from unittest import mock

from django.contrib.messages import get_messages
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from events.graph import ErreurGraph
from events.import_courriels import Releve
from events.models import DossierAccess, Journal
from events.tests.factories import grant, make_dossier, make_user

GRAPH = dict(GRAPH_TENANT_ID="t", GRAPH_CLIENT_ID="c", GRAPH_CLIENT_SECRET="s",
             COURRIEL_BOITE="hello@exemple.be")


def releve(importes=None, deja=None, ignores=0, erreurs=0):
    r = Releve()
    r.importes.update(importes or {})
    r.deja.update(deja or {})
    r.ignores, r.erreurs = ignores, erreurs
    return r


@contextmanager
def verrou(libre):
    yield libre


@override_settings(**GRAPH)
class ReleverTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Litige SDB")
        self.dossier.alias_courriel = "dossier-sdb@exemple.be"
        self.dossier.save()
        self.client.force_login(self.owner)
        self.url = reverse("dossier_relever", args=[self.dossier.pk])

    def poster(self, resultat=None, libre=True, **donnees):
        with mock.patch("events.views.parametres.verrou_de_releve", return_value=verrou(libre)), \
             mock.patch("events.views.parametres.relever_la_boite", return_value=resultat or releve()) as relever:
            reponse = self.client.post(self.url, donnees)
        return reponse, relever

    def messages(self, reponse):
        return [str(m) for m in get_messages(reponse.wsgi_request)]

    def test_le_proprietaire_releve_et_revient_d_ou_il_vient(self):
        retour = reverse("timeline", args=[self.dossier.pk]) + "?vue=liste"
        reponse, relever = self.poster(releve(importes={self.dossier.pk: 1}), retour=retour)
        relever.assert_called_once()
        self.assertRedirects(reponse, retour, fetch_redirect_response=False)
        self.assertIn("1 courriel importé dans ce dossier.", self.messages(reponse))

    def test_un_redacteur_peut_relever(self):
        redacteur = make_user("redacteur")
        grant(self.dossier, redacteur, DossierAccess.Role.WRITE)
        self.client.force_login(redacteur)
        _, relever = self.poster()
        relever.assert_called_once()

    def test_un_lecteur_recoit_404(self):
        lecteur = make_user("lecteur")
        grant(self.dossier, lecteur, DossierAccess.Role.READ)
        self.client.force_login(lecteur)
        reponse, relever = self.poster()
        self.assertEqual(reponse.status_code, 404)
        relever.assert_not_called()

    def test_un_inconnu_recoit_404(self):
        self.client.force_login(make_user("inconnu"))
        reponse, relever = self.poster()
        self.assertEqual(reponse.status_code, 404)
        relever.assert_not_called()

    def test_get_ne_releve_rien(self):
        with mock.patch("events.views.parametres.relever_la_boite") as relever:
            reponse = self.client.get(self.url)
        self.assertEqual(reponse.status_code, 302)
        relever.assert_not_called()

    def test_ne_cite_que_les_courriels_de_ce_dossier(self):
        """Un collaborateur n'apprend rien des autres dossiers ni du reste de la boîte."""
        autre = make_dossier(self.owner, name="Autre")
        reponse, _ = self.poster(releve(importes={autre.pk: 3, self.dossier.pk: 1}, ignores=40))
        texte = " ".join(self.messages(reponse))
        self.assertIn("1 courriel importé dans ce dossier.", texte)
        self.assertNotIn("3", texte)
        self.assertNotIn("40", texte)

    def test_rien_de_nouveau(self):
        reponse, _ = self.poster(releve())
        self.assertIn("Aucun nouveau courriel pour ce dossier.", self.messages(reponse))

    def test_le_geste_est_journalise(self):
        self.poster(releve(importes={self.dossier.pk: 2}))
        entree = Journal.objects.get(action=Journal.Action.COURRIELS_RELEVES)
        self.assertEqual(entree.dossier, self.dossier)
        self.assertEqual(entree.auteur, self.owner)
        self.assertEqual(entree.details, {"importes": 2, "deja": 0, "erreurs": 0})

    def test_un_import_deja_en_cours_ne_releve_pas_une_seconde_fois(self):
        reponse, relever = self.poster(libre=False)
        relever.assert_not_called()
        self.assertIn("Un import est déjà en cours : réessayez dans une minute.", self.messages(reponse))
        self.assertFalse(Journal.objects.filter(action=Journal.Action.COURRIELS_RELEVES).exists())

    def test_sans_alias_rien_n_est_releve(self):
        self.dossier.alias_courriel = None
        self.dossier.save()
        reponse, relever = self.poster()
        relever.assert_not_called()
        self.assertEqual(reponse.status_code, 302)

    @override_settings(GRAPH_CLIENT_SECRET="")
    def test_sans_configuration_rien_n_est_releve(self):
        reponse, relever = self.poster()
        relever.assert_not_called()
        self.assertEqual(reponse.status_code, 302)

    def test_une_erreur_graph_s_affiche(self):
        with mock.patch("events.views.parametres.verrou_de_releve", return_value=verrou(True)), \
             mock.patch("events.views.parametres.relever_la_boite", side_effect=ErreurGraph("HTTP 403")):
            reponse = self.client.post(self.url)
        self.assertEqual(reponse.status_code, 302)
        self.assertTrue(any("HTTP 403" in m for m in self.messages(reponse)))
        self.assertFalse(Journal.objects.filter(action=Journal.Action.COURRIELS_RELEVES).exists())


@override_settings(**GRAPH)
class BoutonReleverTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.dossier.alias_courriel = "dossier-sdb@exemple.be"
        self.dossier.save()
        self.url_bouton = reverse("dossier_relever", args=[self.dossier.pk])

    def page(self, utilisateur):
        self.client.force_login(utilisateur)
        return self.client.get(reverse("timeline", args=[self.dossier.pk]) + "?vue=liste")

    def test_visible_pour_qui_peut_ecrire(self):
        self.assertContains(self.page(self.owner), self.url_bouton)

    def test_absent_pour_un_lecteur(self):
        lecteur = make_user("lecteur")
        grant(self.dossier, lecteur, DossierAccess.Role.READ)
        self.assertNotContains(self.page(lecteur), self.url_bouton)

    def test_absent_sans_alias(self):
        self.dossier.alias_courriel = None
        self.dossier.save()
        self.assertNotContains(self.page(self.owner), self.url_bouton)

    @override_settings(COURRIEL_BOITE="")
    def test_absent_sans_configuration(self):
        self.assertNotContains(self.page(self.owner), self.url_bouton)


class CommandeVerrouTests(TestCase):
    @override_settings(**GRAPH)
    def test_l_import_planifie_s_efface_si_une_releve_tourne_deja(self):
        sortie = StringIO()
        with mock.patch("events.management.commands.importer_courriels.verrou_de_releve",
                        return_value=verrou(False)), \
             mock.patch("events.management.commands.importer_courriels.relever_la_boite") as relever:
            call_command("importer_courriels", stdout=sortie)
        relever.assert_not_called()
        self.assertIn("Un import est déjà en cours", sortie.getvalue())

    def test_releve_compte_par_dossier(self):
        r = releve(importes={1: 2, 2: 1}, deja={1: 1})
        self.assertEqual(sum(r.importes.values()), 3)
        self.assertEqual(r.importes[1], 2)
        self.assertIsInstance(r.importes, Counter)
