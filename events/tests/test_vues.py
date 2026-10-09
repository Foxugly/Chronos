"""Quatre représentations du même dossier : chronologie, mois, semaine, liste.

Les trois nouvelles sont calculées côté serveur, sans bibliothèque de
calendrier : vis-timeline garde la chronologie, qu'il fait bien, et une
grille mensuelle est un tableau que Python calcule en vingt lignes. Embarquer
FullCalendar aurait ajouté 250 Ko et une seconde manière de représenter les
mêmes dates.

Conséquence voulue et testée ici : mois, semaine et liste s'affichent **sans
JavaScript**. La liste est le repli quand tout le reste échoue, et c'est
celle qu'un avocat ou un expert ouvrira.
"""

import re
from datetime import date

from django.test import TestCase
from django.urls import reverse

from events import calendrier
from events.models import DossierAccess, Event, ShareLink
from events.tests.factories import grant, make_dossier, make_user


class CalendrierTests(TestCase):
    def test_an_unreadable_period_falls_back_to_today(self):
        """Une URL tronquée dans un courriel ne doit pas produire une page
        d'erreur."""
        reference = date(2026, 10, 8)
        self.assertEqual(
            calendrier.mois_demande("pas-une-date", reference), date(2026, 10, 1)
        )
        self.assertEqual(calendrier.mois_demande(None, reference), date(2026, 10, 1))

    def test_a_week_starts_on_monday(self):
        """Convention française, et celle de la grille mensuelle."""
        jeudi = date(2026, 10, 8)
        self.assertEqual(calendrier.semaine_demandee(None, jeudi), date(2026, 10, 5))

    def test_the_grid_includes_the_days_of_neighbouring_months(self):
        """Une grille qui s'arrête au 31 laisse des trous et se lit mal."""
        semaines = calendrier.grille_du_mois(date(2026, 10, 1), [])
        self.assertTrue(all(len(s) == 7 for s in semaines))
        premier = semaines[0][0]
        self.assertTrue(premier["hors_mois"])
        self.assertEqual(premier["date"].month, 9)

    def test_events_land_on_their_day(self):
        dossier = make_dossier(make_user("u"))
        evenement = Event.objects.create(
            dossier=dossier, title="Devis", date="2026-10-05"
        )
        semaines = calendrier.grille_du_mois(date(2026, 10, 1), [evenement])
        jours = [j for s in semaines for j in s if j["evenements"]]
        self.assertEqual(len(jours), 1)
        self.assertEqual(jours[0]["date"], date(2026, 10, 5))

    def test_the_list_is_grouped_by_month_oldest_first(self):
        """Une chronologie se lit dans le sens où les faits se sont produits."""
        dossier = make_dossier(make_user("u"))
        evenements = [
            Event.objects.create(dossier=dossier, title="Un", date="2026-10-05"),
            Event.objects.create(dossier=dossier, title="Deux", date="2026-10-20"),
            Event.objects.create(dossier=dossier, title="Trois", date="2026-11-03"),
        ]
        groupes = calendrier.liste_par_mois(evenements)
        self.assertEqual(
            [g["titre"] for g in groupes], ["octobre 2026", "novembre 2026"]
        )
        self.assertEqual(len(groupes[0]["evenements"]), 2)


class ViewSwitchingTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        Event.objects.create(dossier=self.dossier, title="Devis", date="2026-10-05")
        self.client.force_login(self.owner)
        self.base = reverse("timeline", args=[self.dossier.pk])

    def _gabarits(self, vue):
        reponse = self.client.get(self.base, {"vue": vue})
        return reponse, [t.name for t in reponse.templates if t.name]

    def test_each_view_renders_its_own_template(self):
        attendu = {
            "chronologie": "events/timeline.html",
            "calendrier-mois": "events/calendrier.html",
            "calendrier-timeline": "events/calendrier.html",
            "mois": "events/vue_mois.html",
            "semaine": "events/vue_semaine.html",
            "liste": "events/vue_liste.html",
        }
        for vue, gabarit in attendu.items():
            with self.subTest(vue=vue):
                reponse, gabarits = self._gabarits(vue)
                self.assertEqual(reponse.status_code, 200)
                self.assertIn(gabarit, gabarits)

    def test_an_unknown_view_falls_back_to_the_chronology(self):
        """Un paramètre d'URL tordu ne doit pas produire une page d'erreur."""
        reponse, gabarits = self._gabarits("n-importe-quoi")
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("events/timeline.html", gabarits)

    def test_the_three_server_views_need_no_javascript(self):
        """La liste est le repli quand tout le reste échoue."""
        for vue in ("mois", "semaine", "liste"):
            with self.subTest(vue=vue):
                page = self.client.get(self.base, {"vue": vue}).content.decode()
                self.assertNotIn("vis-timeline", page)
                self.assertNotIn("quill", page)

    def test_every_view_offers_the_four_tabs(self):
        for vue in ("chronologie", "calendrier", "mois", "semaine", "liste", "documents"):
            with self.subTest(vue=vue):
                page = self.client.get(self.base, {"vue": vue}).content.decode()
                for autre in ("chronologie", "calendrier", "liste", "documents"):
                    self.assertIn("?vue=%s" % autre, page)

    def test_month_and_week_link_to_each_other(self):
        for vue in ("mois", "semaine"):
            with self.subTest(vue=vue):
                page = self.client.get(self.base, {"vue": vue}).content.decode()
                self.assertIn("?vue=mois", page)
                self.assertIn("?vue=semaine", page)

    def test_month_navigation_moves_by_one_month(self):
        reponse = self.client.get(self.base, {"vue": "mois", "periode": "2026-11"})
        self.assertEqual(reponse.context["precedent"], "2026-10")
        self.assertEqual(reponse.context["suivant"], "2026-12")
        self.assertEqual(reponse.context["titre_periode"], "novembre 2026")


class AccessRulesHoldInEveryViewTests(TestCase):
    """Changer de vue ne doit pas contourner les droits."""

    def setUp(self):
        self.owner = make_user("owner")
        self.reader = make_user("reader")
        self.stranger = make_user("stranger")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        grant(self.dossier, self.reader, DossierAccess.Role.READ)
        Event.objects.create(dossier=self.dossier, title="Devis", date="2026-10-05")
        self.base = reverse("timeline", args=[self.dossier.pk])

    def test_a_stranger_is_refused_in_every_view(self):
        self.client.force_login(self.stranger)
        for vue in ("chronologie", "mois", "semaine", "liste"):
            with self.subTest(vue=vue):
                self.assertEqual(
                    self.client.get(self.base, {"vue": vue}).status_code, 404
                )

    def test_a_reader_sees_no_action_that_would_be_refused(self):
        """Un lecteur voyait « Nouvel évènement » et récoltait un 404."""
        self.client.force_login(self.reader)
        for vue in ("chronologie", "mois", "semaine", "liste"):
            with self.subTest(vue=vue):
                page = self.client.get(self.base, {"vue": vue}).content.decode()
                self.assertNotIn(
                    reverse("event_add", args=[self.dossier.pk]), page
                )

    def test_only_the_owner_is_offered_the_sharing_link(self):
        self.client.force_login(self.reader)
        page = self.client.get(self.base, {"vue": "liste"}).content.decode()
        self.assertNotIn(reverse("dossier_share", args=[self.dossier.pk]), page)


class SharedLinkKeepsItsViewsTests(TestCase):
    """Passer d'une vue à l'autre ne doit pas éjecter un visiteur anonyme
    hors de son lien."""

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        Event.objects.create(dossier=self.dossier, title="Devis", date="2026-10-05")
        self.lien = ShareLink.objects.create(
            dossier=self.dossier,
            created_by=self.owner,
            role=DossierAccess.Role.READ,
        )
        self.base = reverse("share_timeline", args=[self.lien.token])

    def test_all_four_views_work_through_the_link(self):
        for vue in ("chronologie", "mois", "semaine", "liste"):
            with self.subTest(vue=vue):
                reponse = self.client.get(self.base, {"vue": vue})
                self.assertEqual(reponse.status_code, 200)

    def test_the_tabs_keep_the_token(self):
        page = self.client.get(self.base, {"vue": "liste"}).content.decode()
        self.assertIn(self.lien.token, page)
        self.assertNotIn(reverse("timeline", args=[self.dossier.pk]) + "?vue", page)

    def test_a_read_only_link_offers_no_write_action_in_any_view(self):
        for vue in ("chronologie", "mois", "semaine", "liste"):
            with self.subTest(vue=vue):
                page = self.client.get(self.base, {"vue": vue}).content.decode()
                # Le prédicat vise la **balise**, pas la chaîne : « Nouvel
                # évènement » et `data-ouvrir-formulaire` figurent aussi dans
                # le JavaScript du modal, inerte en lecture seule. Chercher le
                # mot ferait échouer ce test sur du code mort.
                self.assertIsNone(
                    re.search(r"<a [^>]*data-ouvrir-formulaire", page),
                    "un lien de création est proposé en lecture seule",
                )
                self.assertIsNone(
                    re.search(
                        r'<a [^>]*href="%s"'
                        % re.escape(
                            reverse("share_event_add", args=[self.lien.token])
                        ),
                        page,
                    )
                )

    def test_the_protective_headers_survive_a_view_change(self):
        for vue in ("mois", "semaine", "liste"):
            with self.subTest(vue=vue):
                reponse = self.client.get(self.base, {"vue": vue})
                self.assertEqual(reponse["Referrer-Policy"], "no-referrer")
                self.assertIn("noindex", reponse["X-Robots-Tag"])
