"""Les quatre onglets FullCalendar, à côté de la chronologie vis-timeline."""

import datetime
import json
import re

from django.test import TestCase
from django.urls import reverse

from events.models import DossierAccess, Event, ShareLink
from events.tests.factories import grant, make_dossier, make_user


class PourCalendrierTests(TestCase):
    """Le format que FullCalendar attend, pour chacune des trois précisions."""

    def setUp(self):
        self.dossier = make_dossier(make_user("owner"))

    def evenement(self, **champs):
        return Event.objects.create(dossier=self.dossier, title="Fait", **champs)

    def test_une_date_est_toute_la_journee(self):
        e = self.evenement(date=datetime.date(2026, 6, 2))
        self.assertEqual(
            e.pour_calendrier(),
            {"id": e.pk, "title": "Fait", "allDay": True, "start": "2026-06-02"},
        )

    def test_un_instant_garde_son_heure(self):
        e = self.evenement(date=datetime.date(2026, 8, 21), heure=datetime.time(14, 32))
        element = e.pour_calendrier()
        self.assertFalse(element["allDay"])
        self.assertEqual(element["start"], "2026-08-21T14:32:00")
        self.assertNotIn("end", element)

    def test_un_intervalle_en_jours_finit_le_lendemain(self):
        """FullCalendar exclut la borne de fin, comme vis-timeline."""
        e = self.evenement(
            date=datetime.date(2026, 8, 18), date_fin=datetime.date(2026, 9, 28)
        )
        element = e.pour_calendrier()
        self.assertTrue(element["allDay"])
        self.assertEqual(element["end"], "2026-09-29")

    def test_un_intervalle_horaire_garde_ses_deux_heures(self):
        e = self.evenement(
            date=datetime.date(2026, 10, 20),
            heure=datetime.time(9, 0),
            date_fin=datetime.date(2026, 10, 20),
            heure_fin=datetime.time(12, 30),
        )
        element = e.pour_calendrier()
        self.assertFalse(element["allDay"])
        self.assertEqual(element["start"], "2026-10-20T09:00:00")
        self.assertEqual(element["end"], "2026-10-20T12:30:00")

    def test_une_heure_de_debut_seule_reste_horaire(self):
        """L'heure saisie fait partie de la preuve : elle ne doit pas se perdre
        dans une case « toute la journée »."""
        e = self.evenement(
            date=datetime.date(2026, 8, 18),
            heure=datetime.time(8, 0),
            date_fin=datetime.date(2026, 8, 20),
        )
        element = e.pour_calendrier()
        self.assertFalse(element["allDay"])
        self.assertEqual(element["start"], "2026-08-18T08:00:00")
        self.assertEqual(element["end"], "2026-08-21T00:00:00")


class VueCalendrierTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.reader = make_user("reader")
        self.dossier = make_dossier(self.owner)
        grant(self.dossier, self.reader, DossierAccess.Role.READ)
        Event.objects.create(dossier=self.dossier, title="Devis signé", date="2026-06-02")
        Event.objects.create(
            dossier=self.dossier,
            title="Chantier",
            date="2026-08-18",
            date_fin="2026-09-28",
        )
        self.base = reverse("timeline", args=[self.dossier.pk])

    def page(self, user=None, vue="calendrier-mois"):
        self.client.force_login(user or self.owner)
        return self.client.get(self.base, {"vue": vue})

    def json_script(self, reponse, ident):
        html = reponse.content.decode()
        contenu = re.search(
            r'<script id="%s" type="application/json">(.*?)</script>' % ident, html, re.S
        ).group(1)
        return json.loads(contenu)

    def test_le_calendrier_a_son_gabarit_et_ses_evenements(self):
        reponse = self.page()
        self.assertTemplateUsed(reponse, "events/calendrier.html")
        items = self.json_script(reponse, "calendrier-items")
        self.assertEqual([i["title"] for i in items], ["Devis signé", "Chantier"])

    def test_chaque_onglet_choisit_sa_vue_fullcalendar(self):
        attendu = {
            "calendrier-mois": "dayGridMonth",
            "calendrier-semaine": "timeGridWeek",
            "calendrier-liste": "liste",
            "calendrier-timeline": "frise",
        }
        for onglet, vue in attendu.items():
            with self.subTest(onglet=onglet):
                reponse = self.page(vue=onglet)
                self.assertTemplateUsed(reponse, "events/calendrier.html")
                self.assertEqual(self.json_script(reponse, "calendrier-vue"), vue)

    def test_le_theme_est_celui_de_bootstrap(self):
        reponse = self.page()
        self.assertContains(reponse, 'themeSystem: "bootstrap5"')
        self.assertContains(reponse, "fullcalendar-bootstrap5-6.1.21")
        self.assertContains(reponse, "bootstrap-icons@1.13.1")

    def test_les_bornes_couvrent_tout_le_dossier_avec_une_marge(self):
        bornes = self.json_script(self.page(), "calendrier-bornes")
        self.assertEqual(
            bornes,
            {"debut": "2026-05-26", "fin": "2026-10-05", "initiale": "2026-08-18"},
        )

    def test_la_navigation_reste_dans_les_bornes_du_dossier(self):
        """Les flèches et « aujourd'hui » s'arrêtent aux dates des évènements :
        hors de cette période, le calendrier n'a rien à montrer."""
        self.assertContains(
            self.page(), "validRange: bornes ? {start: bornes.debut, end: bornes.fin} : undefined"
        )

    def test_un_dossier_vide_n_a_pas_de_bornes(self):
        vide = make_dossier(self.owner, name="Vide")
        self.client.force_login(self.owner)
        reponse = self.client.get(
            reverse("timeline", args=[vide.pk]), {"vue": "calendrier-timeline"}
        )
        self.assertIsNone(self.json_script(reponse, "calendrier-bornes"))

    def test_un_titre_hostile_ne_ferme_pas_la_balise(self):
        Event.objects.create(
            dossier=self.dossier, title="</script><script>pwned=1</script>", date="2026-07-01"
        )
        html = self.page().content.decode()
        self.assertNotIn("</script><script>pwned", html)

    def test_chaque_ressource_externe_porte_son_empreinte(self):
        html = self.page().content.decode()
        for balise in re.findall(r"<(?:script|link) [^>]*(?:src|href)=\"(?:https://|/static/vendor/)[^>]*>", html):
            with self.subTest(balise=balise[:80]):
                self.assertIn('integrity="sha', balise)
                self.assertIn('crossorigin="anonymous"', balise)

    def test_la_cle_de_licence_est_celle_choisie(self):
        """Le module timeline de FullCalendar n'est pas sous MIT : la clé
        non commerciale est une décision du propriétaire, pas un détail."""
        self.assertContains(self.page(), "CC-Attribution-NonCommercial-NoDerivatives")

    def test_un_lecteur_n_a_ni_formulaire_ni_editeur(self):
        html = self.page(user=self.reader).content.decode()
        self.assertNotIn("quill", html)
        self.assertIsNone(re.search(r"<a [^>]*data-ouvrir-formulaire", html))
        self.assertIn('id="modal-evenement"', html)

    def test_les_quatre_onglets_dans_l_ordre_demande(self):
        html = self.page().content.decode()
        onglets = re.findall(r'class="nav-link[^"]*"\s+(?:aria-current="page"\s+)?href="[^"]*\?vue=([\w-]+)"', html)
        self.assertEqual(
            onglets,
            ["chronologie", "calendrier", "liste", "documents"],
        )
        # Les vues du calendrier se choisissent dans sa propre barre.
        self.assertIn('right: "dayGridMonth,timeGridWeek,liste,frise"', html)
        self.assertNotIn("Sans JavaScript :", html)

    def test_toutes_les_vues_du_calendrier_allument_l_onglet_calendrier(self):
        for vue in ("calendrier", "calendrier-mois", "calendrier-semaine", "calendrier-liste",
                    "calendrier-timeline", "mois", "semaine"):
            with self.subTest(vue=vue):
                html = self.page(vue=vue).content.decode()
                actifs = re.findall(r'class="nav-link active"\s+aria-current="page"\s+href="[^"]*\?vue=([\w-]+)"', html)
                self.assertEqual(actifs, ["calendrier"])

    def test_sans_javascript_le_calendrier_renvoie_aux_vues_serveur(self):
        html = self.page(vue="calendrier").content.decode()
        self.assertIn("<noscript>", html)
        self.assertIn("?vue=mois", html)
        self.assertIn("?vue=semaine", html)

    def test_un_lien_de_partage_ouvre_le_detail_par_son_propre_chemin(self):
        lien = ShareLink.objects.create(
            dossier=self.dossier, created_by=self.owner, role=DossierAccess.Role.READ
        )
        html = self.client.get(
            reverse("share_timeline", args=[lien.token]), {"vue": "calendrier-mois"}
        ).content.decode()
        # Entre guillemets : l'URL de partage contient `/events/<id>/` elle aussi.
        self.assertIn('"%s"' % reverse("share_event_detail", args=[lien.token, 999999999]), html)
        self.assertNotIn('"%s"' % reverse("event_detail", args=[999999999]), html)
