"""Améliorations de la page de partage (2026-11-17)."""

import datetime
import re
from unittest import mock

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import DossierAccess, Invitation, Journal, ShareLink
from events.tests.factories import grant, make_dossier, make_user


class PagePartageMixin:
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.partage = reverse("dossier_share", args=[self.dossier.pk])
        self.client.force_login(self.owner)

    def page(self):
        return self.client.get(self.partage).content.decode()


class ChangerLeDroitTests(PagePartageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.lecteur = make_user("lecteur")
        self.acces = grant(self.dossier, self.lecteur, DossierAccess.Role.READ)
        self.url = reverse("dossier_access_role", args=[self.dossier.pk, self.acces.pk])

    def test_passer_en_ecriture_sans_retirer_l_acces(self):
        reponse = self.client.post(self.url, {"role": "write"})
        self.assertRedirects(reponse, self.partage)
        self.acces.refresh_from_db()
        self.assertEqual(self.acces.role, DossierAccess.Role.WRITE)
        entree = Journal.objects.get(action=Journal.Action.ACCES_MODIFIE)
        self.assertEqual(entree.details["changements"], {"role": ["read", "write"]})
        self.assertEqual(entree.objet, "lecteur")

    def test_meme_droit_rien_n_est_journalise(self):
        self.client.post(self.url, {"role": "read"})
        self.assertFalse(Journal.objects.exists())

    def test_droit_inconnu_refuse(self):
        self.client.post(self.url, {"role": "admin"})
        self.acces.refresh_from_db()
        self.assertEqual(self.acces.role, DossierAccess.Role.READ)
        self.assertFalse(Journal.objects.exists())

    def test_seul_le_proprietaire(self):
        ecrivain = make_user("ecrivain")
        grant(self.dossier, ecrivain, DossierAccess.Role.WRITE)
        self.client.force_login(ecrivain)
        self.assertEqual(self.client.post(self.url, {"role": "write"}).status_code, 404)
        self.acces.refresh_from_db()
        self.assertEqual(self.acces.role, DossierAccess.Role.READ)

    def test_un_get_est_refuse(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_un_acces_d_un_autre_dossier_est_404(self):
        autre = make_dossier(self.owner, name="Autre")
        url = reverse("dossier_access_role", args=[autre.pk, self.acces.pk])
        self.assertEqual(self.client.post(url, {"role": "write"}).status_code, 404)

    def test_la_page_propose_le_choix_du_droit(self):
        html = self.page()
        self.assertIn('action="%s"' % self.url, html)
        self.assertIn("data-changer-droit", html)


class RenvoyerUneInvitationTests(PagePartageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.invitation = Invitation.objects.create(
            dossier=self.dossier, email="a@example.test", role="read", created_by=self.owner,
            expires_at=timezone.now() - datetime.timedelta(days=1))
        self.url = reverse("invitation_resend", args=[self.dossier.pk, self.invitation.pk])

    def test_renvoyer_une_invitation_expiree(self):
        ancien = self.invitation.token
        reponse = self.client.post(self.url)
        self.assertRedirects(reponse, self.partage)
        self.invitation.refresh_from_db()
        self.assertNotEqual(self.invitation.token, ancien)  # l'ancien courriel ne sert plus
        self.assertTrue(self.invitation.is_usable)
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.invitation.token, mail.outbox[0].body)
        entree = Journal.objects.get()
        self.assertEqual(entree.action, Journal.Action.INVITATION_EMISE)
        self.assertTrue(entree.details["renvoi"])

    def test_une_invitation_acceptee_ou_annulee_ne_se_renvoie_pas(self):
        for champ in ("accepted_at", "revoked_at"):
            with self.subTest(champ=champ):
                valeurs = {"accepted_at": None, "revoked_at": None, champ: timezone.now()}
                Invitation.objects.filter(pk=self.invitation.pk).update(**valeurs)
                self.assertEqual(self.client.post(self.url).status_code, 404)
        self.assertEqual(len(mail.outbox), 0)

    def test_echec_d_envoi_rien_ne_change(self):
        ancien = self.invitation.token
        with mock.patch("events.views.partage.send_mail", side_effect=OSError), \
                self.assertLogs("events.views.partage", level="ERROR"):
            self.client.post(self.url)
        self.invitation.refresh_from_db()
        self.assertEqual(self.invitation.token, ancien)
        self.assertFalse(Journal.objects.exists())

    def test_seul_le_proprietaire(self):
        ecrivain = make_user("ecrivain")
        grant(self.dossier, ecrivain, DossierAccess.Role.WRITE)
        self.client.force_login(ecrivain)
        self.assertEqual(self.client.post(self.url).status_code, 404)


class OrganisationDeLaPageTests(PagePartageMixin, TestCase):
    def test_trois_blocs_dans_l_ordre(self):
        html = self.page()
        self.assertLess(html.index("data-bloc-personnes"), html.index("data-bloc-ajouter"))
        self.assertLess(html.index("data-bloc-ajouter"), html.index("data-bloc-liens"))

    def test_les_gestes_irreversibles_demandent_confirmation(self):
        lecteur = make_user("lecteur")
        acces = grant(self.dossier, lecteur, DossierAccess.Role.READ)
        lien = ShareLink.objects.create(dossier=self.dossier, created_by=self.owner, role="read")
        invitation = Invitation.objects.create(dossier=self.dossier, email="a@example.test",
                                               role="read", created_by=self.owner)
        html = self.page()
        for url in (reverse("dossier_access_revoke", args=[self.dossier.pk, acces.pk]),
                    reverse("share_link_revoke", args=[self.dossier.pk, lien.pk]),
                    reverse("invitation_revoke", args=[self.dossier.pk, invitation.pk])):
            with self.subTest(url=url):
                formulaire = re.search(r'<form[^>]*action="%s"[^>]*>' % re.escape(url), html).group(0)
                self.assertIn("data-confirmer=", formulaire)

    def test_les_anciens_liens_et_invitations_sont_replies(self):
        maintenant = timezone.now()
        ShareLink.objects.create(dossier=self.dossier, created_by=self.owner, role="read", label="Actif")
        ShareLink.objects.create(dossier=self.dossier, created_by=self.owner, role="read", label="Coupé",
                                 revoked_at=maintenant)
        Invitation.objects.create(dossier=self.dossier, email="vieille@example.test", role="read",
                                  created_by=self.owner, expires_at=maintenant - datetime.timedelta(days=1))
        html = self.page()
        replie_liens = re.search(r"<details[^>]*data-anciens-liens.*?</details>", html, re.S).group(0)
        self.assertIn("Coupé", replie_liens)
        self.assertNotIn("Actif<", replie_liens)
        replie_invitations = re.search(r"<details[^>]*data-anciennes-invitations.*?</details>", html, re.S).group(0)
        self.assertIn("vieille@example.test", replie_invitations)

    def test_expire_bientot(self):
        maintenant = timezone.now()
        ShareLink.objects.create(dossier=self.dossier, created_by=self.owner, role="read", label="Court",
                                 expires_at=maintenant + datetime.timedelta(days=2))
        ShareLink.objects.create(dossier=self.dossier, created_by=self.owner, role="read", label="Long",
                                 expires_at=maintenant + datetime.timedelta(days=60))
        html = self.page()
        self.assertEqual(html.count("data-expire-bientot"), 1)

    def test_avertissement_ecriture(self):
        html = self.page()
        creation = re.search(r"<form[^>]*data-formulaire-creation-lien.*?</form>", html, re.S).group(0)
        # Présent, caché tant que « lecture » est choisi (le choix par défaut).
        self.assertRegex(creation, r"data-alerte-ecriture\s+hidden")

    def test_avertissement_ecriture_visible_pour_un_lien_en_ecriture(self):
        lien = ShareLink.objects.create(dossier=self.dossier, created_by=self.owner, role="write")
        html = self.client.get(reverse("share_link_edit", args=[self.dossier.pk, lien.pk])).content.decode()
        alerte = re.search(r"<div[^>]*data-alerte-ecriture[^>]*>", html).group(0)
        self.assertNotIn("hidden", alerte)
