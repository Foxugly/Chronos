"""Descriptions en texte enrichi, et l'assainissement qui les rend sûres.

L'éditeur du navigateur est un confort. **Le rempart est côté serveur** : un
POST direct, un lien de partage en écriture ou n'importe quel client HTTP
contourne l'éditeur en une ligne.

Le modèle de menace est connu depuis L1 : un dossier est partagé entre un
propriétaire et des contributeurs en écriture — voire, depuis L4, avec des
porteurs de lien anonymes. Du script injecté dans une description
s'exécuterait dans la session du propriétaire, avec son cookie et son jeton
CSRF, sur des pièces de litige.
"""

import re
from django.test import TestCase
from django.urls import reverse

from events.models import Event
from events.sanitize import est_vide, nettoyer
from events.tests.factories import make_dossier, make_user


class SanitiserTests(TestCase):
    def test_script_is_removed_entirely(self):
        self.assertEqual(nettoyer("<script>window.pwned=1</script>"), "")

    def test_images_are_removed(self):
        """Une image distante suffirait à faire fuiter l'adresse IP et le
        navigateur du lecteur vers un hôte choisi par l'auteur."""
        self.assertEqual(nettoyer('<img src="https://evil.test/pixel.gif">'), "")

    def test_event_handlers_are_stripped_but_text_survives(self):
        self.assertEqual(nettoyer('<p onclick="alert(1)">clic</p>'), "<p>clic</p>")

    def test_inline_styles_are_stripped(self):
        self.assertEqual(
            nettoyer('<p style="background:url(http://evil.test)">x</p>'), "<p>x</p>"
        )

    def test_javascript_urls_lose_their_href(self):
        nettoye = nettoyer('<a href="javascript:alert(1)">piège</a>')
        self.assertNotIn("javascript:", nettoye)
        self.assertIn("piège", nettoye)

    def test_data_urls_lose_their_href(self):
        """Un `data:` permettrait d'embarquer un document entier."""
        nettoye = nettoyer('<a href="data:text/html,<script>x</script>">x</a>')
        self.assertNotIn("data:", nettoye)

    def test_iframes_and_svg_are_removed(self):
        for hostile in (
            '<iframe src="https://evil.test"></iframe>',
            "<svg onload=alert(1)></svg>",
        ):
            with self.subTest(hostile=hostile):
                self.assertEqual(nettoyer(hostile), "")

    def test_legitimate_formatting_survives(self):
        """Un assainisseur qui vide tout serait « sûr » et inutile."""
        riche = (
            "<p>Texte <strong>gras</strong> et <em>italique</em></p>"
            "<ul><li>puce</li></ul>"
            "<ol><li>étape</li></ol>"
            "<blockquote>citation</blockquote>"
        )
        self.assertEqual(nettoyer(riche), riche)

    def test_external_links_carry_noreferrer(self):
        """Sans `noreferrer`, suivre un lien depuis un dossier partagé
        transmettrait l'URL de la page — jeton de partage compris — au site
        visé."""
        nettoye = nettoyer('<a href="https://example.be">lien</a>')
        self.assertIn("noreferrer", nettoye)
        self.assertIn('href="https://example.be"', nettoye)

    def test_an_empty_editor_counts_as_empty(self):
        """Un éditeur riche laissé vide renvoie `<p><br></p>`, pas ""."""
        self.assertTrue(est_vide("<p><br></p>"))
        self.assertTrue(est_vide(""))
        self.assertFalse(est_vide("<p>a</p>"))


class TheServerDecidesTests(TestCase):
    """L'assainissement doit tenir quel que soit le chemin d'entrée."""

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        self.client.force_login(self.owner)

    def _creer(self, description):
        self.client.post(
            reverse("event_add", args=[self.dossier.pk]),
            {"title": "Pièce", "date": "2026-06-02", "description": description},
        )
        return Event.objects.get(title="Pièce")

    def test_a_direct_post_is_sanitised_like_anything_else(self):
        cree = self._creer("<p>ok</p><script>window.pwned=1</script>")
        self.assertNotIn("<script", cree.description)
        self.assertIn("<p>ok</p>", cree.description)

    def test_an_editor_left_empty_stores_nothing(self):
        self.assertEqual(self._creer("<p><br></p>").description, "")

    def test_what_the_detail_view_serves_is_sanitised_again(self):
        """Réassaini à la sortie, et pas seulement à l'entrée : l'admin
        permet d'écrire dans ce champ sans passer par `EventForm`."""
        event = Event.objects.create(
            dossier=self.dossier,
            title="Par l'admin",
            date="2026-06-02",
            description='<img src=x onerror=alert(1)><p>texte</p>',
        )
        servi = self.client.get(
            reverse("event_detail", args=[event.pk]),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        ).content.decode()
        self.assertNotIn("onerror", servi)
        self.assertNotIn("<img", servi)
        self.assertIn("<p>texte</p>", servi)


class ExistingDescriptionsWereConvertedTests(TestCase):
    """Les descriptions étaient du texte brut : sans conversion, leurs
    retours à la ligne auraient disparu d'un coup."""

    def test_line_breaks_became_markup(self):
        from events.migrations import (
            __name__ as _,  # noqa: F401  (le module porte un nom numérique)
        )
        import importlib

        migration = importlib.import_module(
            "events.migrations.0009_descriptions_en_html"
        )
        converti = migration._texte_vers_html("1er jour : \r\nplafond")
        self.assertEqual(converti, "<p>1er jour : <br>plafond</p>")

    def test_blank_lines_became_paragraphs(self):
        import importlib

        migration = importlib.import_module(
            "events.migrations.0009_descriptions_en_html"
        )
        self.assertEqual(
            migration._texte_vers_html("Un\r\n\r\nDeux\r\nTrois"),
            "<p>Un</p><p>Deux<br>Trois</p>",
        )

    def test_angle_brackets_were_escaped_not_interpreted(self):
        """Aucune description actuelle n'en contient, mais la migration doit
        rester juste sur une base restaurée qui en contiendrait."""
        import importlib

        migration = importlib.import_module(
            "events.migrations.0009_descriptions_en_html"
        )
        self.assertEqual(
            migration._texte_vers_html("a < b & c"), "<p>a &lt; b &amp; c</p>"
        )


class TheEditorIsOnlyAConvenienceTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        self.client.force_login(self.owner)

    def _page(self):
        return self.client.get(
            reverse("event_add", args=[self.dossier.pk])
        ).content.decode()

    def test_the_editor_is_served_with_its_integrity_hash(self):
        page = self._page()
        self.assertIn("quill", page)
        # Les ressources *chargées* (script, feuille de style), pas les simples liens
        # (le footer pointe vers foxugly.com, qui n'est rien chargé dans la page).
        # Scripts copiés dans static/vendor compris : ils gardent leur empreinte.
        ressources = len(re.findall(r'<(?:script|link)\b[^>]*\b(?:src|href)="(?:https://|/static/vendor/)', page))
        self.assertEqual(ressources, page.count("integrity="))

    def test_the_textarea_stays_usable_without_javascript(self):
        """Sans Quill, la zone de texte reste le champ envoyé : le serveur
        assainit de toute façon ce qui arrive."""
        page = self._page()
        self.assertIn("data-editeur-riche", page)
        self.assertIn("<textarea", page)

    def test_quill_scaffolding_is_normalised_away_before_sending(self):
        """Quill 2 met les puces dans `<ol><li data-list="bullet">` et sème
        des `<span class="ql-ui">`. Stocker cela lierait les descriptions à
        cet éditeur ; une description de dossier doit lui survivre."""
        page = self._page()
        self.assertIn("function normaliser(racine)", page)
        self.assertIn('data-list', page)
        self.assertIn('.ql-ui', page)
