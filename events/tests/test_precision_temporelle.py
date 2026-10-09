"""Une date, un instant, un intervalle — et aucun champ « type ».

Un évènement peut être situé de trois manières : « devis signé le 02/06 »,
« mail envoyé le 21/08 à 14h32 », « chantier du 18 août au 28 septembre ».

La tentation serait de stocker ce choix dans un champ. On ne le fait pas :
un champ « type » peut contredire les dates — un évènement marqué
« intervalle » sans date de fin, ou l'inverse — et il faudrait alors décider
lequel des deux a raison. Ici la précision se **déduit** des champs remplis,
donc elle ne peut pas mentir.

Ce que ces tests gardent, au-delà de la déduction : qu'un intervalle
apparaisse sur **chaque** jour qu'il couvre dans les vues mois et semaine. Un
chantier de six semaines affiché sur son seul premier jour disparaîtrait
derrière une case de calendrier.
"""

from datetime import date, time

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import translation

from events import calendrier
from events.forms import EventForm
from events.models import Event
from events.tests.factories import make_dossier, make_user


class PrecisionDeduiteTests(TestCase):
    def setUp(self):
        self.dossier = make_dossier(make_user("u"))

    def _event(self, **kwargs):
        return Event.objects.create(
            dossier=self.dossier, title="Fait", date=date(2026, 6, 2), **kwargs
        )

    def test_a_bare_date_is_a_date(self):
        self.assertEqual(self._event().precision, "date")

    def test_an_hour_makes_it_an_instant(self):
        self.assertEqual(self._event(heure=time(14, 32)).precision, "instant")

    def test_an_end_date_makes_it_an_interval(self):
        evenement = self._event(date_fin=date(2026, 6, 20))
        self.assertEqual(evenement.precision, "intervalle")

    def test_an_interval_with_hours_stays_an_interval(self):
        """L'heure ne dégrade pas un intervalle en instant : la date de fin
        l'emporte, sinon un chantier horodaté deviendrait un point."""
        evenement = self._event(
            heure=time(8, 0), date_fin=date(2026, 6, 20), heure_fin=time(17, 0)
        )
        self.assertEqual(evenement.precision, "intervalle")

    def test_the_days_covered_include_both_bounds(self):
        evenement = self._event(date_fin=date(2026, 6, 5))
        self.assertEqual(
            evenement.jours_couverts,
            [date(2026, 6, d) for d in (2, 3, 4, 5)],
        )

    def test_a_single_day_covers_itself(self):
        self.assertEqual(self._event().jours_couverts, [date(2026, 6, 2)])

    def test_the_period_reads_differently_in_each_case(self):
        self.assertEqual(self._event().periode_affichee, "02/06/2026")
        self.assertEqual(
            self._event(heure=time(14, 32)).periode_affichee, "02/06/2026 à 14:32"
        )
        self.assertEqual(
            self._event(date_fin=date(2026, 9, 28)).periode_affichee,
            "du 02/06/2026 au 28/09/2026",
        )
        self.assertEqual(
            self._event(
                heure=time(8, 0), date_fin=date(2026, 6, 3), heure_fin=time(17, 0)
            ).periode_affichee,
            "du 02/06/2026 à 08:00 au 03/06/2026 à 17:00",
        )

    def test_a_period_within_one_day_does_not_repeat_the_date(self):
        """« du 02/06 à 09h00 au 02/06 à 12h30 » se dit « le 02/06 de 9h à
        12h30 » — c'est une matinée, pas deux dates."""
        self.assertEqual(
            self._event(
                heure=time(9, 0), date_fin=date(2026, 6, 2), heure_fin=time(12, 30)
            ).periode_affichee,
            "le 02/06/2026 de 09:00 à 12:30",
        )


class LeModeleRefuseUneFinAvantLeDebutTests(TestCase):
    """Deux remparts, parce qu'ils n'attrapent pas la même chose : `clean()`
    rend un message au formulaire, la contrainte protège l'admin et tout
    script qui écrirait en base sans passer par là."""

    def setUp(self):
        self.dossier = make_dossier(make_user("u"))

    def test_clean_rejects_an_end_before_the_start(self):
        evenement = Event(
            dossier=self.dossier,
            title="Chantier",
            date=date(2026, 6, 20),
            date_fin=date(2026, 6, 2),
        )
        with self.assertRaises(ValidationError) as leve:
            evenement.clean()
        self.assertIn("date_fin", leve.exception.message_dict)

    def test_the_database_refuses_it_too(self):
        from django.db import IntegrityError, transaction

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Event.objects.create(
                    dossier=self.dossier,
                    title="Chantier",
                    date=date(2026, 6, 20),
                    date_fin=date(2026, 6, 2),
                )

    def test_clean_rejects_an_end_hour_before_the_start_hour_same_day(self):
        evenement = Event(
            dossier=self.dossier,
            title="Réunion",
            date=date(2026, 6, 2),
            date_fin=date(2026, 6, 2),
            heure=time(17, 0),
            heure_fin=time(8, 0),
        )
        with self.assertRaises(ValidationError) as leve:
            evenement.clean()
        self.assertIn("heure_fin", leve.exception.message_dict)

    def test_an_end_hour_on_a_later_day_may_precede_the_start_hour(self):
        """Du 2 juin à 17h au 3 juin à 8h est une nuit, pas une erreur."""
        Event(
            dossier=self.dossier,
            title="Nuit",
            date=date(2026, 6, 2),
            date_fin=date(2026, 6, 3),
            heure=time(17, 0),
            heure_fin=time(8, 0),
        ).clean()


class LeModeChoisiEffaceLesAutresChampsTests(TestCase):
    """Le mode fait autorité sur ce qui est enregistré.

    On efface plutôt que de refuser : une heure laissée par un changement
    d'avis n'est pas une faute de l'utilisateur, c'est un reste — et le
    champ qui la porte est masqué, donc il ne peut pas la vider lui-même.
    """

    def setUp(self):
        self.dossier = make_dossier(make_user("u"))

    def _poster(self, **extra):
        donnees = {
            "title": "Fait",
            "date": "2026-06-02",
            "description": "",
            **extra,
        }
        return EventForm(data=donnees, instance=Event(dossier=self.dossier))

    def test_choosing_a_date_drops_a_leftover_hour(self):
        formulaire = self._poster(
            precision="date", heure="14:32", date_fin="2026-06-20"
        )
        self.assertTrue(formulaire.is_valid(), formulaire.errors)
        evenement = formulaire.save()
        self.assertIsNone(evenement.heure)
        self.assertIsNone(evenement.date_fin)
        self.assertEqual(evenement.precision, "date")

    def test_choosing_an_instant_drops_a_leftover_end_date(self):
        formulaire = self._poster(
            precision="instant", heure="14:32", date_fin="2026-06-20"
        )
        self.assertTrue(formulaire.is_valid(), formulaire.errors)
        evenement = formulaire.save()
        self.assertIsNone(evenement.date_fin)
        self.assertEqual(evenement.precision, "instant")

    def test_an_instant_needs_its_hour(self):
        formulaire = self._poster(precision="instant")
        self.assertFalse(formulaire.is_valid())
        self.assertIn("heure", formulaire.errors)

    def test_an_interval_needs_its_end_date(self):
        """La raison d'être du mode : un intervalle sans fin est impossible."""
        formulaire = self._poster(precision="intervalle", heure="08:00")
        self.assertFalse(formulaire.is_valid())
        self.assertIn("date_fin", formulaire.errors)

    def test_an_interval_refuses_an_end_before_its_start(self):
        formulaire = self._poster(precision="intervalle", date_fin="2026-05-01")
        self.assertFalse(formulaire.is_valid())
        self.assertIn("date_fin", formulaire.errors)

    def test_the_form_opens_on_the_mode_of_the_event_it_edits(self):
        evenement = Event.objects.create(
            dossier=self.dossier,
            title="Chantier",
            date=date(2026, 6, 2),
            date_fin=date(2026, 9, 28),
        )
        formulaire = EventForm(instance=evenement)
        self.assertEqual(formulaire.fields["precision"].initial, "intervalle")

    def test_the_three_modes_are_offered_with_their_fields(self):
        """Sans JavaScript, le formulaire doit rester complet."""
        rendu = EventForm(instance=Event(dossier=self.dossier)).as_p()
        for champ in ("date", "heure", "date_fin", "heure_fin"):
            self.assertIn('name="%s"' % champ, rendu)
        for mode in ("date", "instant", "intervalle"):
            self.assertIn('value="%s"' % mode, rendu)


class UnEnvoiSansModeSeLitDansSesChampsTests(TestCase):
    """Le mode est un confort d'interface, pas une condition d'enregistrement.

    Un POST direct, un client HTTP, un lien de partage en écriture ou un
    formulaire resté en cache n'envoient que les dates. Exiger le mode ferait
    échouer un envoi parfaitement clair.
    """

    def setUp(self):
        self.dossier = make_dossier(make_user("u"))

    def _sans_mode(self, **extra):
        return EventForm(
            data={"title": "Fait", "date": "2026-06-02", "description": "", **extra},
            instance=Event(dossier=self.dossier),
        )

    def test_only_a_date_gives_a_date(self):
        formulaire = self._sans_mode()
        self.assertTrue(formulaire.is_valid(), formulaire.errors)
        self.assertEqual(formulaire.save().precision, "date")

    def test_an_hour_gives_an_instant(self):
        formulaire = self._sans_mode(heure="14:32")
        self.assertTrue(formulaire.is_valid(), formulaire.errors)
        self.assertEqual(formulaire.save().precision, "instant")

    def test_an_end_date_gives_an_interval(self):
        formulaire = self._sans_mode(date_fin="2026-06-20")
        self.assertTrue(formulaire.is_valid(), formulaire.errors)
        self.assertEqual(formulaire.save().precision, "intervalle")

    def test_a_deduced_interval_still_refuses_a_backwards_end(self):
        """La déduction n'est pas une dispense de validation."""
        formulaire = self._sans_mode(date_fin="2026-05-01")
        self.assertFalse(formulaire.is_valid())
        self.assertIn("date_fin", formulaire.errors)


class UnIntervalleSEtaleSurSesJoursTests(TestCase):
    """Le point du calendrier : six semaines de chantier doivent se voir
    comme six semaines."""

    def setUp(self):
        self.dossier = make_dossier(make_user("u"))
        self.chantier = Event.objects.create(
            dossier=self.dossier,
            title="Chantier",
            date=date(2026, 10, 5),
            date_fin=date(2026, 10, 9),
        )

    def test_the_month_grid_shows_it_on_every_day(self):
        semaines = calendrier.grille_du_mois(date(2026, 10, 1), [self.chantier])
        jours = sorted(
            j["date"] for s in semaines for j in s if self.chantier in j["evenements"]
        )
        self.assertEqual(jours, [date(2026, 10, d) for d in (5, 6, 7, 8, 9)])

    def test_the_week_grid_shows_it_on_every_day(self):
        jours = calendrier.jours_de_la_semaine(date(2026, 10, 5), [self.chantier])
        portent = [j["date"] for j in jours if self.chantier in j["evenements"]]
        self.assertEqual(portent, [date(2026, 10, d) for d in (5, 6, 7, 8, 9)])

    def test_the_list_shows_it_once(self):
        """La liste est une chronologie, pas un calendrier : répéter un
        intervalle sur cinq lignes la rendrait illisible."""
        groupes = calendrier.liste_par_mois([self.chantier])
        self.assertEqual(sum(len(g["evenements"]) for g in groupes), 1)

    def test_an_interval_spanning_two_months_appears_in_both(self):
        pont = Event.objects.create(
            dossier=self.dossier,
            title="Pont",
            date=date(2026, 10, 30),
            date_fin=date(2026, 11, 2),
        )
        octobre = calendrier.grille_du_mois(date(2026, 10, 1), [pont])
        novembre = calendrier.grille_du_mois(date(2026, 11, 1), [pont])
        for grille, jour in (
            (octobre, date(2026, 10, 31)),
            (novembre, date(2026, 11, 1)),
        ):
            porteurs = [j["date"] for s in grille for j in s if pont in j["evenements"]]
            self.assertIn(jour, porteurs)


class LaChronologieMontreLesBarresTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.client.force_login(self.owner)

    def _items(self):
        reponse = self.client.get(
            reverse("timeline", args=[self.dossier.pk]), {"vue": "chronologie"}
        )
        return {item["content"]: item for item in reponse.context["timeline_items"]}

    def test_a_date_is_a_point_without_an_end(self):
        Event.objects.create(dossier=self.dossier, title="Devis", date=date(2026, 6, 2))
        item = self._items()["Devis"]
        self.assertEqual(item["start"], "2026-06-02")
        self.assertNotIn("end", item)

    def test_an_instant_carries_its_hour(self):
        Event.objects.create(
            dossier=self.dossier,
            title="Mail",
            date=date(2026, 6, 2),
            heure=time(14, 32),
        )
        item = self._items()["Mail"]
        self.assertEqual(item["start"], "2026-06-02T14:32:00")
        self.assertNotIn("end", item)

    def test_an_interval_becomes_a_range(self):
        Event.objects.create(
            dossier=self.dossier,
            title="Chantier",
            date=date(2026, 8, 18),
            date_fin=date(2026, 9, 28),
        )
        item = self._items()["Chantier"]
        self.assertEqual(item["type"], "range")
        # vis-timeline exclut la borne de fin : sans ce jour de plus, la
        # barre s'arrêterait la veille du 28 et le dernier jour du chantier
        # ne se verrait pas.
        self.assertEqual(item["end"], "2026-09-29")

    def test_an_hourly_interval_keeps_its_exact_bounds(self):
        Event.objects.create(
            dossier=self.dossier,
            title="Expertise",
            date=date(2026, 6, 2),
            heure=time(9, 0),
            date_fin=date(2026, 6, 2),
            heure_fin=time(12, 30),
        )
        item = self._items()["Expertise"]
        self.assertEqual(item["start"], "2026-06-02T09:00:00")
        self.assertEqual(item["end"], "2026-06-02T12:30:00")


class LesVuesAffichentLaPeriodeTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        Event.objects.create(
            dossier=self.dossier,
            title="Chantier",
            date=date(2026, 10, 5),
            date_fin=date(2026, 10, 9),
        )
        Event.objects.create(
            dossier=self.dossier,
            title="Mail",
            date=date(2026, 10, 6),
            heure=time(14, 32),
        )
        self.client.force_login(self.owner)
        self.base = reverse("timeline", args=[self.dossier.pk])

    def test_the_list_prints_the_full_period(self):
        page = self.client.get(self.base, {"vue": "liste"}).content.decode()
        self.assertIn("du 05/10/2026 au 09/10/2026", page)
        self.assertIn("06/10/2026 à 14:32", page)

    def test_the_week_recalls_the_bounds_of_a_repeated_interval(self):
        """Un intervalle revient sur chacun de ses jours : sans ses bornes,
        il se lirait comme cinq faits distincts."""
        page = self.client.get(
            self.base, {"vue": "semaine", "periode": "2026-10-05"}
        ).content.decode()
        self.assertEqual(page.count("du 05/10/2026 au 09/10/2026"), 5)

    def test_the_month_cell_shows_the_hour_of_an_instant(self):
        page = self.client.get(
            self.base, {"vue": "mois", "periode": "2026-10"}
        ).content.decode()
        self.assertIn("14:32", page)


class ChampsDateEtHeureRemplisTests(TestCase):
    """Un `<input type="date">` n'accepte que `AAAA-MM-JJ`.

    En `fr-be`, Django rendait `value="05.08.2026"` : le navigateur rejette
    la valeur et affiche un champ vide. Modifier un évènement obligeait alors
    à ressaisir sa date de mémoire — sur un dossier de preuves.
    """

    def test_le_formulaire_rend_les_dates_et_heures_au_format_html(self):
        evenement = Event.objects.create(
            dossier=make_dossier(make_user("owner")),
            title="Chantier",
            date=date(2026, 8, 5),
            heure=time(8, 30),
            date_fin=date(2026, 9, 28),
            heure_fin=time(17, 0),
        )
        with translation.override("fr-be"):
            formulaire = EventForm(instance=evenement)
            rendus = {nom: str(formulaire[nom]) for nom in ("date", "heure", "date_fin", "heure_fin")}

        self.assertIn('value="2026-08-05"', rendus["date"])
        self.assertIn('value="08:30"', rendus["heure"])
        self.assertIn('value="2026-09-28"', rendus["date_fin"])
        self.assertIn('value="17:00"', rendus["heure_fin"])
