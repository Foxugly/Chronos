"""Le lien de consultation se montre dès sa création ; les étiquettes se saisissent
avec l'autocomplétion de celles du dossier."""

import re

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import DossierAccess, Etiquette, Event, ShareLink
from events.tests.factories import make_dossier, make_user


class LienCreeTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.client.force_login(self.owner)
        self.partage = reverse("dossier_share", args=[self.dossier.pk])

    def creer(self, **champs):
        return self.client.post(reverse("share_link_create", args=[self.dossier.pk]),
                                {"label": "Avocate", "role": DossierAccess.Role.READ, "duree": "30", **champs})

    def test_le_formulaire_de_la_page_suffit_a_creer_un_lien(self):
        """Le bug du 2026-10-11 : la page n'affichait pas « Droit accordé », champ
        obligatoire, et « Créer un lien » était refusé sans un mot. On soumet
        exactement ce que la page propose, valeurs par défaut comprises."""
        html = self.client.get(self.partage).content.decode()
        formulaire = re.search(
            r'<form method="post" action="%s">(.*?)</form>' % re.escape(
                reverse("share_link_create", args=[self.dossier.pk])), html, re.S).group(1)
        donnees = {}
        for nom, valeur in re.findall(r'<input[^>]*name="(\w+)"[^>]*value="([^"]*)"', formulaire):
            donnees[nom] = valeur
        for nom, options in re.findall(r'<select name="(\w+)"[^>]*>(.*?)</select>', formulaire, re.S):
            choisie = re.search(r'<option value="([^"]*)"[^>]*selected', options) or re.search(r'<option value="([^"]*)"', options)
            donnees[nom] = choisie.group(1)
        self.assertIn("role", donnees)
        self.client.post(reverse("share_link_create", args=[self.dossier.pk]), donnees)
        self.assertEqual(ShareLink.objects.count(), 1)

    def test_un_refus_est_signale(self):
        reponse = self.client.post(reverse("share_link_create", args=[self.dossier.pk]),
                                   {"label": "X", "role": "inconnu", "duree": "30"})
        self.assertEqual(reponse.status_code, 400)
        self.assertContains(reponse, "is-invalid", status_code=400)
        self.assertFalse(ShareLink.objects.exists())

    def test_le_texte_ne_promet_plus_la_lecture_seule(self):
        self.assertNotContains(self.client.get(self.partage), "accès en <strong>lecture seule</strong>")

    def test_apres_creation_le_lien_est_montre_avec_un_bouton_copier(self):
        reponse = self.creer()
        lien = ShareLink.objects.get()
        self.assertRedirects(reponse, "%s?nouveau=%d" % (self.partage, lien.pk))
        html = self.client.get(reponse.url).content.decode()
        adresse = "http://testserver%s" % reverse("share_timeline", args=[lien.token])
        bloc = re.search(r"<div[^>]*data-lien-cree.*?</div>\s*</div>", html, re.S).group(0)
        self.assertIn(adresse, bloc)
        self.assertIn('data-copier="%s"' % adresse, bloc)

    def test_sans_nouveau_pas_d_encart(self):
        self.creer()
        self.assertNotIn("data-lien-cree", self.client.get(self.partage).content.decode())

    def test_un_lien_revoque_ou_d_un_autre_dossier_n_est_pas_montre(self):
        self.creer()
        lien = ShareLink.objects.get()
        lien.revoked_at = timezone.now()
        lien.save()
        autre = ShareLink.objects.create(dossier=make_dossier(make_user("x"), "Autre"),
                                         created_by=self.owner, role=DossierAccess.Role.READ)
        for pk in (lien.pk, autre.pk, "abc"):
            with self.subTest(pk=pk):
                html = self.client.get(self.partage, {"nouveau": pk}).content.decode()
                self.assertNotIn("data-lien-cree", html)
                self.assertNotIn(autre.token, html)

    def test_chaque_lien_actif_a_son_bouton_copier(self):
        self.creer()
        lien = ShareLink.objects.get()
        html = self.client.get(self.partage).content.decode()
        self.assertIn('data-copier="http://testserver%s"' % reverse("share_timeline", args=[lien.token]), html)

    def test_plus_d_aide_sous_le_destinataire(self):
        self.assertNotContains(self.client.get(self.partage), "Pour vous souvenir à qui ce lien")


class SaisieDesEtiquettesTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        Etiquette.objects.create(dossier=self.dossier, nom="Carrelage")
        Etiquette.objects.create(dossier=make_dossier(make_user("y"), "Autre"), nom="Secret")
        self.evenement = Event.objects.create(dossier=self.dossier, title="Fait", date="2026-06-02")
        self.client.force_login(self.owner)

    def formulaire(self):
        return self.client.get(reverse("event_edit", args=[self.evenement.pk])).content.decode()

    def test_l_autocompletion_propose_les_etiquettes_du_dossier(self):
        html = self.formulaire()
        liste = re.search(r'<datalist id="suggestions-etiquettes[^"]*">(.*?)</datalist>', html, re.S).group(1)
        self.assertIn('<option value="Carrelage">', liste)
        self.assertNotIn("Secret", liste)

    def test_les_etiquettes_viennent_avant_les_documents(self):
        html = self.formulaire()
        self.assertLess(html.index("data-etiquettes"), html.index("Documents existants"))

    def test_le_champ_est_equipe_pour_le_script(self):
        html = self.formulaire()
        self.assertRegex(html, r'<input[^>]*data-saisie-etiquette[^>]*name="nouvelles_etiquettes"')
        self.assertRegex(html, r'<input[^>]*name="etiquettes"[^>]*data-nom="Carrelage"')

    def test_sans_javascript_la_saisie_texte_marche_toujours(self):
        self.client.post(reverse("event_edit", args=[self.evenement.pk]), {
            "title": "Fait", "date": "2026-06-02", "etiquettes_presentes": "1",
            "nouvelles_etiquettes": "carrelage, Joints",
        })
        self.assertEqual(sorted(self.evenement.etiquettes.values_list("nom", flat=True)), ["Carrelage", "Joints"])
