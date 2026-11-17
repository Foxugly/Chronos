"""Voir à nouveau un lien de partage, et le modifier (destinataire, droit,
validité), depuis la page de partage — propriétaire seul (invariant 5)."""

import datetime
import re

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import DossierAccess, Journal, ShareLink
from events.tests.factories import grant, make_dossier, make_user


class LienDePartageMixin:
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Litige")
        self.lien = ShareLink.objects.create(
            dossier=self.dossier, label="Avocat", role=DossierAccess.Role.READ,
            created_by=self.owner,
            expires_at=timezone.now() + datetime.timedelta(days=30))
        self.partage = reverse("dossier_share", args=[self.dossier.pk])
        self.voir = reverse("share_link_detail", args=[self.dossier.pk, self.lien.pk])
        self.modifier = reverse("share_link_edit", args=[self.dossier.pk, self.lien.pk])
        self.adresse = "http://testserver%s" % reverse("share_timeline", args=[self.lien.token])
        self.client.force_login(self.owner)

    def poster(self, **valeurs):
        donnees = {"label": self.lien.label, "role": self.lien.role,
                   "expire_le": timezone.localtime(self.lien.expires_at).date().isoformat()}
        donnees.update(valeurs)
        return self.client.post(self.modifier, donnees)


class VoirLeLienTests(LienDePartageMixin, TestCase):
    def test_la_page_montre_l_adresse_et_le_bouton_copier(self):
        html = self.client.get(self.voir).content.decode()
        self.assertIn(self.adresse, html)
        self.assertIn('data-copier="%s"' % self.adresse, html)

    def test_la_liste_ne_montre_plus_l_adresse_en_clair_mais_une_popup(self):
        """L'adresse n'est plus sous chaque lien : elle est dans la popup
        « Voir », ouverte par un bouton qui mène sinon à la page."""
        html = self.client.get(self.partage).content.decode()
        bouton = re.search(r'<a[^>]*href="%s"[^>]*>' % re.escape(self.voir), html).group(0)
        self.assertIn('data-bs-toggle="modal"', bouton)
        cible = re.search(r'data-bs-target="#([^"]+)"', bouton).group(1)
        popup = html.index('id="%s"' % cible)
        # Avant la popup, nulle part ; dans la popup, avec son bouton copier.
        self.assertNotIn(self.adresse, html[:popup])
        self.assertIn('data-copier="%s"' % self.adresse, html[popup:])

    def test_un_lien_revoque_n_a_ni_page_ni_bouton(self):
        self.lien.revoked_at = timezone.now()
        self.lien.save()
        self.assertEqual(self.client.get(self.voir).status_code, 404)
        self.assertNotIn(self.voir, self.client.get(self.partage).content.decode())

    def test_404_pour_qui_n_est_pas_proprietaire(self):
        ecrivain = make_user("ecrivain")
        grant(self.dossier, ecrivain, DossierAccess.Role.WRITE)
        for utilisateur in (ecrivain, make_user("inconnu")):
            self.client.force_login(utilisateur)
            with self.subTest(utilisateur=utilisateur.username):
                self.assertEqual(self.client.get(self.voir).status_code, 404)
                self.assertEqual(self.client.get(self.modifier).status_code, 404)

    def test_un_lien_d_un_autre_dossier_est_404(self):
        autre_dossier = make_dossier(self.owner, name="Autre")
        url = reverse("share_link_detail", args=[autre_dossier.pk, self.lien.pk])
        self.assertEqual(self.client.get(url).status_code, 404)
        url = reverse("share_link_edit", args=[autre_dossier.pk, self.lien.pk])
        self.assertEqual(self.client.get(url).status_code, 404)


class ModifierLeLienTests(LienDePartageMixin, TestCase):
    def test_la_page_de_modification_est_preremplie(self):
        html = self.client.get(self.modifier).content.decode()
        self.assertIn('value="Avocat"', html)
        self.assertIn('value="%s"' % timezone.localtime(self.lien.expires_at).date().isoformat(), html)

    def test_la_page_de_partage_porte_une_popup_de_modification_par_lien(self):
        html = self.client.get(self.partage).content.decode()
        self.assertIn('action="%s"' % self.modifier, html)
        bouton = re.search(r'<a[^>]*href="%s"[^>]*>' % re.escape(self.modifier), html).group(0)
        self.assertIn('data-bs-toggle="modal"', bouton)
        # Les raccourcis de validité.
        self.assertIn("data-raccourci-validite", html)

    def test_modifier_destinataire_droit_et_validite(self):
        jour = timezone.localdate() + datetime.timedelta(days=90)
        reponse = self.poster(label="Maître Dupont", role=DossierAccess.Role.WRITE,
                              expire_le=jour.isoformat())
        self.assertRedirects(reponse, self.partage)
        self.lien.refresh_from_db()
        self.assertEqual(self.lien.label, "Maître Dupont")
        self.assertEqual(self.lien.role, DossierAccess.Role.WRITE)
        # Valable jusqu'à la fin du jour choisi, à l'heure de Bruxelles.
        fin = timezone.localtime(self.lien.expires_at)
        self.assertEqual(fin.date(), jour)
        self.assertEqual((fin.hour, fin.minute), (23, 59))

        entree = Journal.objects.get(action=Journal.Action.LIEN_MODIFIE)
        self.assertFalse(entree.via_admin)
        self.assertEqual(entree.auteur, self.owner)
        self.assertEqual(entree.lien, self.lien)
        self.assertEqual(set(entree.details["changements"]), {"label", "role", "expires_at"})

    def test_vider_la_validite_rend_le_lien_illimite(self):
        self.poster(expire_le="")
        self.lien.refresh_from_db()
        self.assertIsNone(self.lien.expires_at)

    def test_garder_le_meme_jour_ne_change_rien(self):
        """Le champ montre le jour ; l'heure d'expiration d'origine est gardée,
        et rien n'est journalisé."""
        avant = self.lien.expires_at
        self.poster()
        self.lien.refresh_from_db()
        self.assertEqual(self.lien.expires_at, avant)
        self.assertFalse(Journal.objects.exists())

    def test_prolonger_un_lien_expire_le_reactive(self):
        ShareLink.objects.filter(pk=self.lien.pk).update(
            expires_at=timezone.now() - datetime.timedelta(days=1))
        self.lien.refresh_from_db()
        self.assertFalse(self.lien.is_active)
        jour = timezone.localdate() + datetime.timedelta(days=7)
        self.poster(expire_le=jour.isoformat())
        self.lien.refresh_from_db()
        self.assertTrue(self.lien.is_active)

    def test_une_date_passee_est_refusee(self):
        hier = timezone.localdate() - datetime.timedelta(days=1)
        reponse = self.poster(label="Autre", expire_le=hier.isoformat())
        self.assertEqual(reponse.status_code, 400)
        self.assertContains(reponse, "is-invalid", status_code=400)
        self.lien.refresh_from_db()
        self.assertEqual(self.lien.label, "Avocat")
        self.assertFalse(Journal.objects.exists())

    def test_renommer_un_lien_expire_sans_toucher_a_sa_date(self):
        ShareLink.objects.filter(pk=self.lien.pk).update(
            expires_at=timezone.now() - datetime.timedelta(days=3))
        self.lien.refresh_from_db()
        avant = self.lien.expires_at
        reponse = self.poster(label="Expert")
        self.assertRedirects(reponse, self.partage)
        self.lien.refresh_from_db()
        self.assertEqual((self.lien.label, self.lien.expires_at), ("Expert", avant))
        self.assertFalse(self.lien.is_active)

    def test_aujourd_hui_est_accepte(self):
        reponse = self.poster(expire_le=timezone.localdate().isoformat())
        self.assertRedirects(reponse, self.partage)

    def test_un_lien_revoque_ne_se_modifie_pas(self):
        self.lien.revoked_at = timezone.now()
        self.lien.save()
        self.assertEqual(self.poster(label="Autre").status_code, 404)
        self.lien.refresh_from_db()
        self.assertEqual(self.lien.label, "Avocat")
        self.assertNotIn(self.modifier, self.client.get(self.partage).content.decode())

    def test_un_lecteur_ou_un_ecrivain_ne_modifie_rien(self):
        ecrivain = make_user("ecrivain")
        grant(self.dossier, ecrivain, DossierAccess.Role.WRITE)
        self.client.force_login(ecrivain)
        self.assertEqual(self.poster(role=DossierAccess.Role.WRITE).status_code, 404)
        self.lien.refresh_from_db()
        self.assertEqual(self.lien.role, DossierAccess.Role.READ)

    def test_le_jeton_ne_change_pas(self):
        jeton = self.lien.token
        self.poster(label="Autre", token="x" * 40, dossier=make_dossier(self.owner, "B").pk)
        self.lien.refresh_from_db()
        self.assertEqual((self.lien.token, self.lien.dossier_id), (jeton, self.dossier.pk))
