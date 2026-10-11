"""Le fil de discussion d'un courriel, et les courriels cités qui manquent au dossier."""

import datetime
from zoneinfo import ZoneInfo

from django.test import TestCase
from django.urls import reverse

from events.fils import courriels_manquants, fil_de_discussion
from events.models import CourrielImporte, DossierAccess, Event, ShareLink
from events.tests.factories import grant, make_dossier, make_user

BRUXELLES = ZoneInfo("Europe/Brussels")


class Base(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.n = 0

    def courriel(self, titre, date, heure="10:00", de="acme@bat.example", dossier=None, **cite):
        """Un évènement importé, avec sa trace ; `cite` : cite_de, cite_le, cite_objet."""
        dossier = dossier or self.dossier
        self.n += 1
        evenement = Event.objects.create(dossier=dossier, title=titre, date=date, heure=heure)
        CourrielImporte.objects.create(
            dossier=dossier, message_id=f"<{self.n}@test>", evenement=evenement, expediteur=de, **cite
        )
        return evenement


class FilDeDiscussionTests(Base):
    def test_meme_objet_hors_prefixes_et_accents(self):
        a = self.courriel("sdb : jonctions sol/murs", "2026-09-01")
        b = self.courriel("RE: SDB : Jonctions sol/murs", "2026-09-03", de="archi@x.be")
        c = self.courriel("TR: sdb : jonctions sol/murs", "2026-09-05")
        self.courriel("Facture", "2026-09-02")
        self.assertEqual(fil_de_discussion(b), [a, c])

    def test_un_evenement_saisi_a_la_main_n_est_pas_d_un_fil(self):
        """Deux « Devis » saisis à la main ne sont pas une conversation."""
        a = self.courriel("Devis", "2026-09-01")
        manuel = Event.objects.create(dossier=self.dossier, title="Devis", date="2026-09-02")
        self.assertEqual(fil_de_discussion(a), [])
        self.assertEqual(fil_de_discussion(manuel), [])

    def test_sans_objet_ne_fait_pas_un_fil(self):
        a = self.courriel("(sans objet)", "2026-09-01")
        self.courriel("(sans objet)", "2026-09-02")
        self.assertEqual(fil_de_discussion(a), [])

    def test_jamais_d_un_autre_dossier(self):
        autre = make_dossier(self.owner, name="Autre")
        a = self.courriel("sdb", "2026-09-01")
        self.courriel("RE: sdb", "2026-09-02", dossier=autre)
        self.assertEqual(fil_de_discussion(a), [])


class CourrielsManquantsTests(Base):
    def cite(self, de, jour, heure, objet):
        h, m = map(int, heure.split(":"))
        return {"cite_de": de, "cite_le": datetime.datetime(2026, 9, jour, h, m, tzinfo=BRUXELLES),
                "cite_objet": objet}

    def test_une_citation_sans_courriel_dans_le_dossier_manque(self):
        reponse = self.courriel("RE: Facture", "2026-09-10", de="acme@bat.example",
                                **self.cite("BNQ <info@banque.example>", 8, "09:15", "Facture"))
        manquants = courriels_manquants(self.dossier)
        self.assertEqual(len(manquants), 1)
        self.assertEqual(manquants[0]["de"], "BNQ <info@banque.example>")
        self.assertEqual(manquants[0]["objet"], "Facture")
        self.assertEqual(manquants[0]["cite_par"], [reponse])
        self.assertEqual(manquants[0]["a"], "")

    def test_le_destinataire_du_manquant(self):
        self.courriel("RE: Facture", "2026-09-10",
                      **self.cite("info@banque.example", 8, "09:15", "Facture"), cite_a="Jean <r@x.be>")
        self.assertEqual(courriels_manquants(self.dossier)[0]["a"], "Jean <r@x.be>")

    def test_le_destinataire_vient_de_la_premiere_reponse_qui_le_connait(self):
        self.courriel("RE: Facture", "2026-09-10", **self.cite("info@banque.example", 8, "09:15", "Facture"))
        self.courriel("RE: Facture", "2026-09-11", **self.cite("info@banque.example", 8, "09:16", "Facture"),
                      cite_a="Jean <r@x.be>")
        self.assertEqual(courriels_manquants(self.dossier)[0]["a"], "Jean <r@x.be>")

    def test_le_courriel_arrive_et_ne_manque_plus(self):
        self.courriel("RE: Facture", "2026-09-10", **self.cite("info@banque.example", 8, "09:15", "Facture"))
        self.assertEqual(len(courriels_manquants(self.dossier)), 1)
        # Arrivé ensuite, l'heure de réception à quelques minutes de l'heure d'envoi citée.
        self.courriel("Facture", "2026-09-08", heure="09:22", de="info@banque.example")
        self.assertEqual(courriels_manquants(self.dossier), [])

    def test_deux_reponses_au_meme_absent_ne_font_qu_un_manquant(self):
        r1 = self.courriel("RE: Facture", "2026-09-10", **self.cite("info@banque.example", 8, "09:15", "Facture"))
        r2 = self.courriel("RE: Facture", "2026-09-11", de="archi@x.be",
                           **self.cite("info@banque.example", 8, "09:20", "TR: Facture"))
        manquants = courriels_manquants(self.dossier)
        self.assertEqual(len(manquants), 1)
        self.assertEqual(manquants[0]["cite_par"], [r1, r2])

    def test_rien_ne_manque_sans_citation(self):
        self.courriel("Facture", "2026-09-08")
        self.assertEqual(courriels_manquants(self.dossier), [])

    def test_du_plus_ancien_au_plus_recent(self):
        self.courriel("RE: B", "2026-09-10", **self.cite("b@x.be", 9, "09:00", "B"))
        self.courriel("RE: A", "2026-09-10", **self.cite("a@x.be", 2, "09:00", "A"))
        self.assertEqual([m["objet"] for m in courriels_manquants(self.dossier)], ["A", "B"])


class PagesTests(Base):
    def setUp(self):
        super().setUp()
        self.reponse = self.courriel(
            "RE: Facture", "2026-09-10",
            cite_de="BNQ <info@banque.example>", cite_objet="Facture", cite_a="Jean <jdupont@perso.example>",
            cite_le=datetime.datetime(2026, 9, 8, 9, 15, tzinfo=BRUXELLES),
        )
        self.page = reverse("dossier_manquants", args=[self.dossier.pk])
        self.dossier_url = reverse("timeline", args=[self.dossier.pk])

    def test_un_lecteur_voit_la_page_et_le_bouton(self):
        lecteur = make_user("lecteur")
        grant(self.dossier, lecteur, DossierAccess.Role.READ)
        self.client.force_login(lecteur)
        reponse = self.client.get(self.page)
        self.assertContains(reponse, "info@banque.example")
        self.assertContains(reponse, "jdupont@perso.example")
        self.assertContains(reponse, "Facture")
        self.assertContains(reponse, reverse("event_detail", args=[self.reponse.pk]))
        self.assertContains(self.client.get(self.dossier_url, {"vue": "liste"}), self.page)

    def test_un_inconnu_a_404(self):
        self.client.force_login(make_user("intrus"))
        self.assertEqual(self.client.get(self.page).status_code, 404)

    def test_pas_de_bouton_quand_rien_ne_manque(self):
        self.courriel("Facture", "2026-09-08", heure="09:17", de="info@banque.example")
        self.client.force_login(self.owner)
        self.assertNotContains(self.client.get(self.dossier_url, {"vue": "liste"}), self.page)

    def test_le_detail_montre_le_fil(self):
        self.courriel("Facture", "2026-09-01", de="info@banque.example")
        self.client.force_login(self.owner)
        reponse = self.client.get(reverse("event_detail", args=[self.reponse.pk]))
        self.assertContains(reponse, "Même fil de discussion")

    def test_le_fil_ne_repete_pas_un_lien_deja_montre(self):
        """« En réponse à » le dit déjà : le fil ne montre que ce qu'aucun lien ne prouve."""
        origine = self.courriel("Facture", "2026-09-01", de="info@banque.example")
        Event.objects.filter(pk=self.reponse.pk).update(reponse_a=origine)
        self.client.force_login(self.owner)
        reponse = self.client.get(reverse("event_detail", args=[self.reponse.pk]))
        self.assertContains(reponse, "En réponse à")
        self.assertNotContains(reponse, "Même fil de discussion")

    def test_un_lien_de_partage_montre_le_fil_mais_pas_les_manquants(self):
        """Le fil est dans le détail, servi aussi par les liens ; la page des manquants,
        comme le journal, reste aux comptes."""
        autre = self.courriel("Facture", "2026-09-01", de="info@banque.example")
        lien = ShareLink.objects.create(
            dossier=self.dossier, created_by=self.owner, role=DossierAccess.Role.READ
        )
        reponse = self.client.get(reverse("share_event_detail", args=[lien.token, self.reponse.pk]))
        self.assertContains(reponse, reverse("share_event_detail", args=[lien.token, autre.pk]))
        self.assertNotContains(self.client.get(reverse("share_timeline", args=[lien.token])), "manquants")
