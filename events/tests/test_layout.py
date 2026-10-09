"""Gabarit partagé : navbar, déconnexion, sélecteur de dossier, pages d'erreur.

Avant ce lot, l'application n'offrait **aucun moyen de se déconnecter** — la
vue de déconnexion d'allauth exige un POST et rien ne le déclenchait — et les
pages servies hors de la chronologie n'héritaient d'aucune mise en page
commune.
"""

import re
from django.test import TestCase, override_settings
from django.urls import reverse

from events.models import DossierAccess, Event
from events.tests.factories import grant, make_dossier, make_user


class NavbarTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        self.client.force_login(self.owner)

    def _accueil(self):
        return self.client.get(reverse("dossier_list")).content.decode()

    def test_the_navbar_offers_a_way_out(self):
        """Le point du lot : il n'existait aucune déconnexion. allauth refuse
        un GET (`ACCOUNT_LOGOUT_ON_GET` est faux), donc un simple lien ne
        suffit pas — il faut un formulaire POST."""
        page = self._accueil()
        self.assertIn(reverse("account_logout"), page)
        self.assertIn('method="post"', page)

    def test_logging_out_really_ends_the_session(self):
        reponse = self.client.post(reverse("account_logout"), follow=True)
        self.assertEqual(reponse.status_code, 200)
        # Une fois déconnecté, la racine renvoie vers la page de connexion.
        suite = self.client.get(reverse("dossier_list"))
        self.assertEqual(suite.status_code, 302)
        self.assertIn("/accounts/login/", suite["Location"])

    def test_the_navbar_names_the_signed_in_account(self):
        self.assertIn("owner", self._accueil())

    def test_the_dossier_switcher_lists_only_what_the_user_may_see(self):
        autre = make_dossier(make_user("ailleurs"), name="Dossier de quelqu un")
        page = self._accueil()
        self.assertIn(self.dossier.name, page)
        self.assertNotIn(autre.name, page)

    def test_a_reader_also_sees_the_dossier_in_the_switcher(self):
        lecteur = make_user("lecteur")
        grant(self.dossier, lecteur, DossierAccess.Role.READ)
        self.client.force_login(lecteur)
        self.assertIn(self.dossier.name, self._accueil())


class ContextProcessorTests(TestCase):
    def test_an_anonymous_request_gets_an_empty_queryset_not_a_crash(self):
        """Le processeur tourne sur toutes les pages, connexion comprise."""
        reponse = self.client.get(reverse("account_login"))
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(reponse.context["accessible_dossiers"])


class EventFormInheritsTheSharedLayoutTests(TestCase):
    """Le formulaire embarquait son propre document HTML : son `<head>`, un
    Bootstrap 5.3.3 en décalage avec le 5.3.8 du reste, un second bundle et
    aucune balise viewport."""

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        self.client.force_login(self.owner)

    def _formulaire(self):
        return self.client.get(
            reverse("event_add", args=[self.dossier.pk])
        ).content.decode()

    def test_the_form_now_carries_the_shared_navbar(self):
        self.assertIn("topbar__brand", self._formulaire())

    def test_the_form_declares_a_single_document(self):
        page = self._formulaire()
        self.assertEqual(page.count("<!DOCTYPE html>"), 1)
        self.assertEqual(page.count("<html"), 1)

    def test_the_form_no_longer_pins_its_own_bootstrap_version(self):
        page = self._formulaire()
        self.assertNotIn("bootstrap@5.3.3", page)
        self.assertIn("bootstrap@5.3.8", page)

    def test_every_external_resource_still_carries_its_integrity_hash(self):
        """Le site sert des pièces de dossier : une compromission de CDN qui
        injecterait du script est dans le périmètre."""
        page = self._formulaire()
        # Les ressources *chargées* (script, feuille de style), pas les simples liens
        # (le footer pointe vers foxugly.com, qui n'est rien chargé dans la page).
        ressources = len(re.findall(r'<(?:script|link)\b[^>]*\b(?:src|href)="https://', page))
        self.assertEqual(ressources, page.count("integrity="))

    def test_the_pending_file_list_is_built_without_markup(self):
        """Le nom de fichier est saisi par la personne qui téléverse ; il
        était concaténé dans du HTML via jQuery `.append()`."""
        page = self._formulaire()
        self.assertIn("nom.textContent = file.name", page)
        for puits in (".innerHTML =", ".outerHTML =", ".insertAdjacentHTML("):
            self.assertNotIn(puits, page)


@override_settings(DEBUG=False, ALLOWED_HOSTS=["testserver"])
class ErrorPagesTests(TestCase):
    def setUp(self):
        self.user = make_user("owner")
        self.client.force_login(self.user)

    def test_a_refusal_is_rendered_in_french(self):
        """Tout refus de ce projet est un 404 : c'est la page que les invités
        verront le plus souvent. Django y servait son texte anglais."""
        reponse = self.client.get("/dossiers/999999/")
        self.assertEqual(reponse.status_code, 404)
        page = reponse.content.decode()
        self.assertIn("Cette page n'est pas disponible", page)
        self.assertNotIn("Not Found", page)

    def test_the_refusal_page_says_nothing_about_existence(self):
        """Un dossier inaccessible et un identifiant inexistant doivent
        produire exactement la même page, sinon le texte devient un oracle."""
        import re

        def sans_jeton(reponse):
            # Le jeton CSRF du formulaire de déconnexion et le nonce CSP des
            # scripts changent à chaque requête, tirés au hasard : ce sont les
            # seules différences légitimes entre deux rendus, et elles ne
            # disent rien de l'existence du dossier.
            texte = re.sub(r'value="[^"]{32,}"', 'value="JETON"', reponse.content.decode())
            return re.sub(r'nonce="[^"]+"', 'nonce="NONCE"', texte)

        autre = make_dossier(make_user("ailleurs"))
        interdit = sans_jeton(self.client.get(f"/dossiers/{autre.pk}/"))
        inexistant = sans_jeton(self.client.get("/dossiers/999999/"))
        self.assertEqual(interdit, inexistant)


class ServerErrorPageTests(TestCase):
    def test_the_500_page_stands_alone(self):
        """Django la rend avec un contexte vide : ni `user`, ni les
        processeurs de contexte. Hériter de `base.html`, dont la navbar lit
        `user`, ferait échouer la page d'erreur elle-même."""
        from django.template.loader import render_to_string

        page = render_to_string("500.html", {})
        self.assertIn("<!DOCTYPE html>", page)
        self.assertIn("Le serveur a rencontré une erreur", page)
        # Si l'incident vient du réseau, la page doit s'afficher quand même.
        self.assertNotIn("https://", page)
