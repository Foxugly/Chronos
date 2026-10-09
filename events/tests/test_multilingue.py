"""Le site en trois langues : français, néerlandais, anglais.

Le français est la langue source (les identifiants de traduction sont en
français) ; le néerlandais et l'anglais ont un catalogue dans `locale/`. La
langue se choisit par le sélecteur de la barre de navigation, qui poste vers
`set_language` et pose le cookie `django_language`.
"""

import re

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from events.models import Event
from events.tests.factories import make_dossier, make_user


class SelecteurDeLangueTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)

    def test_les_trois_langues_sont_declarees(self):
        self.assertEqual([code for code, _ in settings.LANGUAGES], ["fr", "nl", "en-gb"])

    def test_le_selecteur_est_dans_la_barre_et_coche_la_langue_courante(self):
        self.client.force_login(self.owner)
        html = self.client.get(reverse("dossier_list")).content.decode()

        self.assertIn('class="selecteur-langue__declencheur"', html)
        boutons = re.findall(r'<button type="submit" name="language" value="([\w-]+)"', html)
        self.assertEqual(boutons, ["fr", "nl", "en-gb"])
        self.assertRegex(html, r'value="fr"[^>]*aria-current="true"')

    def test_le_selecteur_est_aussi_sur_la_page_de_connexion(self):
        """La langue se choisit avant de se connecter."""
        html = self.client.get(reverse("account_login")).content.decode()
        self.assertIn('class="selecteur-langue__declencheur"', html)

    def test_changer_de_langue_sans_etre_connecte(self):
        reponse = self.client.post(
            reverse("set_language"), {"language": "nl", "next": reverse("account_login")}
        )
        self.assertRedirects(reponse, reverse("account_login"), fetch_redirect_response=False)
        self.assertEqual(reponse.cookies[settings.LANGUAGE_COOKIE_NAME].value, "nl")

    def test_next_hors_du_site_est_ignore(self):
        """Pas de redirection ouverte : `set_language` valide `next`."""
        reponse = self.client.post(
            reverse("set_language"), {"language": "nl", "next": "https://exemple.invalid/"}
        )
        self.assertEqual(reponse.status_code, 302)
        self.assertNotIn("exemple.invalid", reponse["Location"])

    def test_un_get_ne_change_rien(self):
        """Changer de langue est un POST, avec son jeton CSRF : un lien piégé
        ne doit pas pouvoir changer la langue de quelqu'un."""
        reponse = self.client.get(reverse("set_language"), {"language": "nl"})
        self.assertNotIn(settings.LANGUAGE_COOKIE_NAME, reponse.cookies)


class PageDansLaLangueChoisieTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Dossier")
        Event.objects.create(dossier=self.dossier, title="Devis", date="2026-08-05")
        self.client.force_login(self.owner)

    def page(self, langue, url=None, **params):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = langue
        return self.client.get(url or reverse("timeline", args=[self.dossier.pk]), params)

    def test_la_langue_du_cookie_s_applique_a_toute_la_page(self):
        attendu = {"fr": ("fr", "05/08/2026"), "nl": ("nl", "5-8-2026"), "en-gb": ("en-gb", "05/08/2026")}
        for langue, (code_html, date) in attendu.items():
            with self.subTest(langue=langue):
                html = self.page(langue, vue="liste").content.decode()
                self.assertIn('<html lang="%s">' % code_html, html)
                self.assertIn(date, html)

    def test_le_declencheur_affiche_le_code_de_la_langue(self):
        for langue, code in (("fr", "FR"), ("nl", "NL"), ("en-gb", "EN")):
            with self.subTest(langue=langue):
                html = self.page(langue).content.decode()
                declencheur = re.search(
                    r'class="selecteur-langue__declencheur".*?>\s*(\w+)\s*</button>', html, re.S
                ).group(1)
                self.assertEqual(declencheur, code)


class SiteTraduitTests(TestCase):
    """Le néerlandais et l'anglais ont leurs catalogues : la page change de langue."""

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Dossier")
        Event.objects.create(dossier=self.dossier, title="Devis", date="2026-08-05")
        self.client.force_login(self.owner)

    def page(self, langue, url, **params):
        self.client.cookies[settings.LANGUAGE_COOKIE_NAME] = langue
        return self.client.get(url, params).content.decode()

    def test_l_interface_est_traduite(self):
        attendu = {
            "fr": ("Se déconnecter", "Mes dossiers"),
            "nl": ("Afmelden", "Mijn dossiers"),
            "en-gb": ("Sign out", "My case files"),
        }
        for langue, textes in attendu.items():
            with self.subTest(langue=langue):
                html = self.page(langue, reverse("dossier_list"))
                for texte in textes:
                    self.assertIn(texte, html)

    def test_les_onglets_et_la_periode_sont_traduits(self):
        html = self.page("nl", reverse("timeline", args=[self.dossier.pk]), vue="liste")
        for texte in ("Chronologie", "Kalender", "Lijst", "Stukken", "augustus 2026"):
            self.assertIn(texte, html)

    def test_les_messages_javascript_sont_traduits_et_echappes(self):
        html = self.page("en-gb", reverse("event_add", args=[self.dossier.pk]))
        self.assertIn("already in the list, ignored", html)
        self.assertIn("Remove %(nom)s", html)

    def test_aucun_marqueur_doublé_dans_les_pages(self):
        """`{{ _("…%(x)s…") }}` double les `%` : le marqueur ne serait jamais
        remplacé. Les messages à variables passent par `{% translate … as %}`."""
        for langue in ("fr", "nl", "en-gb"):
            for url in (
                reverse("event_add", args=[self.dossier.pk]),
                reverse("timeline", args=[self.dossier.pk]),
            ):
                with self.subTest(langue=langue, url=url):
                    self.assertNotIn("%%(", self.page(langue, url))

    def test_la_page_de_connexion_d_allauth_suit_la_langue(self):
        self.client.logout()
        html = self.page("nl", reverse("account_login"))
        self.assertIn("Nog geen account?", html)
        self.assertIn('<html lang="nl">', html)


class CataloguesCompletsTests(TestCase):
    """Un texte non traduit s'afficherait en français au milieu d'une page
    néerlandaise ou anglaise : chaque catalogue doit être complet."""

    def test_aucune_entree_vide_ni_approximative(self):
        import re
        from pathlib import Path

        for langue in ("nl", "en_GB"):
            with self.subTest(langue=langue):
                po = Path(settings.BASE_DIR, "locale", langue, "LC_MESSAGES", "django.po")
                contenu = po.read_text(encoding="utf-8")
                entrees = contenu.split("\n\n")[1:]  # la première est l'en-tête
                self.assertNotIn("#, fuzzy", "\n\n".join(entrees))
                vides = [e for e in entrees if re.search(r'^msgstr(\[\d\])? ""\Z', e.strip(), re.M)
                         and not re.search(r'^"', e.strip().split("msgstr", 1)[1], re.M)]
                self.assertEqual(vides, [])
                self.assertTrue(po.with_suffix(".mo").exists(), "catalogue non compilé")
