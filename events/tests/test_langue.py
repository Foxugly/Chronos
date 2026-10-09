"""Les dates suivent la langue courante, partout.

Le site ne sert que `fr` aujourd'hui (`LANGUAGES`), mais rien de ce qui
touche aux dates n'est écrit pour le français : ces tests activent le
néerlandais et l'anglais directement, pour prouver que l'ajout d'une langue
n'aura pas à revisiter ce code.
"""

import json
import re
from datetime import date, time

from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import translation

from events import calendrier
from events.context_processors import langue
from events.forms import EventForm
from events.models import Event
from events.tests.factories import make_dossier, make_user


class PeriodeAfficheeTests(TestCase):
    def setUp(self):
        self.dossier = make_dossier(make_user("owner"))

    def evenement(self, **champs):
        return Event(dossier=self.dossier, title="Fait", **champs)

    def test_une_date_au_format_court_de_chaque_langue(self):
        e = self.evenement(date=date(2026, 8, 5))
        attendu = {"fr": "05/08/2026", "nl": "5-8-2026", "en-gb": "05/08/2026"}
        for code, texte in attendu.items():
            with self.subTest(langue=code), translation.override(code):
                self.assertEqual(e.periode_affichee, texte)

    def test_un_instant_porte_l_heure_au_format_de_la_langue(self):
        e = self.evenement(date=date(2026, 8, 21), heure=time(14, 32))
        with translation.override("fr"):
            self.assertEqual(e.periode_affichee, "21/08/2026 à 14:32")

    def test_un_intervalle(self):
        e = self.evenement(date=date(2026, 8, 18), date_fin=date(2026, 9, 28))
        with translation.override("fr"):
            self.assertEqual(e.periode_affichee, "du 18/08/2026 au 28/09/2026")
        with translation.override("nl"):
            # Tournure non traduite (pas encore de .po), format néerlandais.
            self.assertIn("18-8-2026", e.periode_affichee)


class CalendrierSansJavascriptTests(TestCase):
    def test_noms_de_mois_et_de_jours_dans_la_langue_courante(self):
        with translation.override("fr"):
            self.assertEqual(calendrier.titre_du_mois(date(2026, 8, 1)), "août 2026")
            self.assertEqual(calendrier.noms_de_jours()[0], "lundi")
        with translation.override("nl"):
            self.assertEqual(calendrier.titre_du_mois(date(2026, 8, 1)), "augustus 2026")
            self.assertEqual(calendrier.noms_de_jours()[0], "maandag")
        with translation.override("en-gb"):
            self.assertEqual(calendrier.noms_de_jours()[6], "Sunday")

    def test_la_liste_titre_ses_mois_dans_la_langue_courante(self):
        dossier = make_dossier(make_user("owner"))
        e = Event.objects.create(dossier=dossier, title="Fait", date="2026-08-05")
        with translation.override("nl"):
            self.assertEqual(calendrier.liste_par_mois([e])[0]["titre"], "augustus 2026")


class LangueCouranteTests(TestCase):
    def contexte(self, code):
        with translation.override(code):
            return langue(RequestFactory().get("/"))["langue"]

    def test_le_processeur_de_contexte_suit_la_langue(self):
        self.assertEqual(
            self.contexte("fr"),
            {"code": "fr", "base": "fr", "format_date": "d/m/Y"},
        )
        self.assertEqual(self.contexte("nl")["format_date"], "j-n-Y")
        self.assertEqual(self.contexte("en-gb")["format_date"], "d/m/Y")

    def test_un_format_que_flatpickr_lirait_autrement_n_est_pas_transmis(self):
        """Un format court avec un nom de mois (« j F Y ») n'a pas le même jeton
        dans flatpickr : il est remplacé par l'ISO plutôt que mal interprété."""
        from unittest import mock

        with mock.patch("django.utils.formats.get_format", return_value="j F Y"):
            self.assertEqual(self.contexte("fr")["format_date"], "Y-m-d")

    def test_la_page_annonce_sa_langue(self):
        owner = make_user("owner")
        dossier = make_dossier(owner)
        self.client.force_login(owner)
        html = self.client.get(reverse("timeline", args=[dossier.pk])).content.decode()

        self.assertIn('<html lang="fr">', html)
        donnees = re.search(
            r'<script id="langue-courante" type="application/json">(.*?)</script>', html
        ).group(1)
        self.assertEqual(json.loads(donnees)["base"], "fr")
        # vis-timeline reçoit la langue au lieu de deviner celle du navigateur.
        self.assertIn("locale: langue.base", html)

    def test_fullcalendar_prend_la_langue_courante(self):
        owner = make_user("owner")
        dossier = make_dossier(owner)
        self.client.force_login(owner)
        html = self.client.get(
            reverse("timeline", args=[dossier.pk]), {"vue": "calendrier-mois"}
        ).content.decode()
        self.assertIn("locale: langue.code", html)
        self.assertIn("locales-all.global.min.js", html)
        self.assertNotIn('locale: "fr"', html)


class FormulaireTests(TestCase):
    def test_les_fichiers_de_langue_suivent_la_langue_courante(self):
        rendus = {}
        for code in ("fr", "nl", "en-gb"):
            with translation.override(code):
                contexte = langue(RequestFactory().get("/"))
                rendus[code] = render_to_string("events/_formulaire_js.html", contexte)

        self.assertIn("flatpickr@4.6.13/dist/l10n/fr.js", rendus["fr"])
        self.assertIn("flatpickr@4.6.13/dist/l10n/nl.js", rendus["nl"])
        self.assertNotIn("/l10n/", rendus["en-gb"])
        for code, html in rendus.items():
            with self.subTest(langue=code):
                for balise in re.findall(r"<script [^>]*src=\"https://[^>]*>", html):
                    self.assertIn('integrity="sha', balise)

    def test_le_champ_envoye_reste_iso_quelle_que_soit_la_langue(self):
        """flatpickr affiche le format de la langue ; ce qui part au serveur,
        et ce que la norme HTML exige, reste `AAAA-MM-JJ`."""
        e = Event(
            dossier=make_dossier(make_user("owner")),
            title="Fait",
            date=date(2026, 8, 5),
            heure=time(8, 30),
        )
        for code in ("fr", "nl", "en-gb"):
            with self.subTest(langue=code), translation.override(code):
                formulaire = EventForm(instance=e)
                self.assertIn('value="2026-08-05"', str(formulaire["date"]))
                self.assertIn('value="08:30"', str(formulaire["heure"]))
