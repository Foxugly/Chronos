"""Ergonomie des vues d'un dossier : impression, liste en accordéon, expéditeurs en modal."""

import re

from django.test import TestCase
from django.urls import reverse

from events.models import DossierAccess, Event, Expediteur
from events.tests.factories import grant, make_dossier, make_user


class Base(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.evenement = Event.objects.create(
            dossier=self.dossier, title="Devis signé", date="2026-06-02", description="<p>Le devis.</p>")
        self.client.force_login(self.owner)
        self.base = reverse("timeline", args=[self.dossier.pk])

    def page(self, **parametres):
        return self.client.get(self.base, parametres).content.decode()


class ImprimerTests(Base):
    BOUTON = re.compile(r"<button[^>]*data-imprimer")

    def onglets(self, html):
        return re.search(r'<ul class="nav nav-tabs.*?</ul>', html, re.S).group(0)

    def test_le_bouton_est_sur_la_ligne_des_onglets(self):
        for vue in ("liste", "documents"):
            with self.subTest(vue=vue):
                html = self.page(vue=vue)
                self.assertRegex(self.onglets(html), self.BOUTON)
                self.assertEqual(len(self.BOUTON.findall(html)), 1)

    def test_pas_de_bouton_la_ou_l_on_n_imprime_pas(self):
        self.assertNotRegex(self.page(vue="chronologie"), self.BOUTON)


class AccordeonTests(Base):
    def faits(self, html):
        """Chaque ligne : le volet (`<details>`) et ses boutons, posés à côté."""
        return re.findall(r"<div [^>]*data-fait-ligne[^>]*>\s*<details.*?</details>.*?</div>\s*</div>", html, re.S)

    def volet(self, fait):
        return re.search(r"<details.*?</details>", fait, re.S).group(0)

    def test_chaque_fait_est_un_volet_replie(self):
        html = self.page(vue="liste")
        faits = self.faits(html)
        self.assertEqual(len(faits), 1)
        self.assertNotRegex(faits[0], r"<details[^>]*\bopen\b")
        resume = re.search(r"<summary.*?</summary>", faits[0], re.S).group(0)
        self.assertIn("Devis signé", resume)
        self.assertNotIn("Le devis.", resume)
        self.assertIn("Le devis.", faits[0])

    def test_une_recherche_ouvre_les_faits_trouves(self):
        faits = self.faits(self.page(vue="liste", q="devis"))
        self.assertRegex(faits[0], r"<details[^>]*\bopen\b")

    def test_le_bouton_oeil_ouvre_le_detail_hors_du_volet(self):
        """Hors du `<details>` : fermé, il masquerait tout ce qui n'est pas son résumé."""
        fait = self.faits(self.page(vue="liste"))[0]
        self.assertNotIn("bi-eye", self.volet(fait))
        oeil = re.search(r'<a[^>]*data-voir-evenement[^>]*>\s*<i class="bi bi-eye', fait)
        self.assertIsNotNone(oeil)
        self.assertIn(reverse("event_detail", args=[self.evenement.pk]), oeil.group(0))
        resume = re.search(r"<summary.*?</summary>", fait, re.S).group(0)
        self.assertNotIn("<a ", resume)
        self.assertNotIn("<button", resume)

    def test_un_lecteur_a_l_oeil_mais_ni_modifier_ni_supprimer(self):
        lecteur = make_user("lecteur")
        grant(self.dossier, lecteur, DossierAccess.Role.READ)
        self.client.force_login(lecteur)
        fait = self.faits(self.page(vue="liste"))[0]
        self.assertIn("bi-eye", fait)
        self.assertNotIn(reverse("event_edit", args=[self.evenement.pk]), fait)

    def test_tout_deployer_n_apparait_qu_avec_javascript(self):
        html = self.page(vue="liste")
        self.assertRegex(html, r"<button[^>]*data-tout-deployer[^>]*hidden")


class ExpediteursEnModalTests(Base):
    AJAX = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}

    def setUp(self):
        super().setUp()
        self.expediteur = Expediteur.objects.create(
            dossier=self.dossier, adresse="acme@bat.example", nom="ACME Bat", couleur="#2563eb")
        self.url = reverse("dossier_expediteurs", args=[self.dossier.pk])

    def test_le_bouton_de_l_en_tete_ouvre_le_modal(self):
        self.assertRegex(self.page(vue="liste"),
                         r'<a [^>]*href="%s"[^>]*data-ouvrir-expediteurs' % re.escape(self.url))

    def test_en_ajax_le_fragment_seul(self):
        reponse = self.client.get(self.url, **self.AJAX)
        html = reponse.content.decode()
        self.assertNotIn("<html", html)
        self.assertNotIn("<script", html)
        self.assertIn('name="couleur-%d"' % self.expediteur.pk, html)
        self.assertIn("data-formulaire-expediteurs", html)

    def test_en_ajax_l_enregistrement_repond_ce_qui_a_change(self):
        reponse = self.client.post(self.url, {"couleur-%d" % self.expediteur.pk: "#dc2626"}, **self.AJAX)
        self.assertEqual(reponse.json(), {"modifies": 1, "erreurs": []})
        self.expediteur.refresh_from_db()
        self.assertEqual(self.expediteur.couleur, "#dc2626")
        reponse = self.client.post(self.url, {"couleur-%d" % self.expediteur.pk: "#dc2626"}, **self.AJAX)
        self.assertEqual(reponse.json(), {"modifies": 0, "erreurs": []})

    def test_une_couleur_invalide_est_signalee(self):
        reponse = self.client.post(self.url, {"couleur-%d" % self.expediteur.pk: "red"}, **self.AJAX)
        self.assertEqual(reponse.json()["modifies"], 0)
        self.assertEqual(len(reponse.json()["erreurs"]), 1)

    def test_sans_javascript_la_page_et_la_redirection_restent(self):
        self.assertContains(self.client.get(self.url), "<html", html=False)
        reponse = self.client.post(self.url, {"couleur-%d" % self.expediteur.pk: "#16a34a"})
        self.assertRedirects(reponse, self.url)

    def test_un_lecteur_voit_la_legende_sans_formulaire_et_ne_peut_pas_ecrire(self):
        lecteur = make_user("lecteur")
        grant(self.dossier, lecteur, DossierAccess.Role.READ)
        self.client.force_login(lecteur)
        html = self.client.get(self.url, **self.AJAX).content.decode()
        self.assertIn("ACME Bat", html)
        self.assertNotIn('type="color"', html)
        reponse = self.client.post(self.url, {"couleur-%d" % self.expediteur.pk: "#dc2626"}, **self.AJAX)
        self.assertEqual(reponse.status_code, 404)


class ResumeSurUneLigneTests(Base):
    def resume(self, html, titre):
        for bloc in re.findall(r"<summary.*?</summary>", html, re.S):
            if titre in bloc:
                return bloc
        self.fail("résumé introuvable : %s" % titre)

    def test_l_expediteur_d_un_courriel(self):
        expediteur = Expediteur.objects.create(
            dossier=self.dossier, adresse="acme@bat.example", nom="ACME Bat", couleur="#2563eb")
        Event.objects.filter(pk=self.evenement.pk).update(expediteur=expediteur)
        resume = self.resume(self.page(vue="liste"), "Devis signé")
        self.assertIn("ACME Bat", resume)
        self.assertIn('title="acme@bat.example"', resume)
        self.assertIn("bi-envelope", resume)

    def test_la_source_d_un_fait_saisi_a_la_main(self):
        resume = self.resume(self.page(vue="liste"), "Devis signé")
        self.assertIn("Saisi à la main", resume)
        self.assertIn("bi-pencil", resume)

    def test_le_titre_tient_sur_une_ligne_et_se_lit_en_entier_au_survol(self):
        long = "Mise en demeure – paiement des travaux réalisés et indemnisation du préjudice subi " * 2
        Event.objects.filter(pk=self.evenement.pk).update(title=long.strip())
        resume = self.resume(self.page(vue="liste"), "Mise en demeure")
        self.assertRegex(resume, r'<span class="[^"]*text-truncate[^"]*"[^>]*title="Mise en demeure')
        self.assertRegex(resume, r'<summary class="[^"]*flex-nowrap')
