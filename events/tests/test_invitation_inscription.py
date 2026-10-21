"""Une invitation survit à l'inscription et à la vérification d'adresse.

Depuis le 2026-10-21 (Chronos#33), un compte neuf doit confirmer son adresse
avant de se connecter. Sans précaution, la personne venue d'une invitation
aboutissait, après le clic sur le lien de confirmation, à la page de connexion
puis à l'accueil, vide : l'invitation qui l'avait amenée était perdue.
"""

import re

from django.core import mail
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import DossierAccess, Invitation
from events.tests.factories import make_dossier, make_user, verifier_adresse

MOT_DE_PASSE = "un-tres-bon-mot-de-passe1"


class InvitationEtInscriptionTests(TestCase):
    def setUp(self):
        # Les limites de débit d'allauth vivent dans le cache, qui survit d'un
        # test à l'autre : un deuxième courriel de confirmation vers la même
        # adresse serait tu en silence.
        cache.clear()
        self.owner = make_user("owner")
        verifier_adresse(self.owner)
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        self.invitation = Invitation.objects.create(
            dossier=self.dossier, email="avocat@example.test",
            role=DossierAccess.Role.READ, created_by=self.owner,
        )
        self.url = reverse("invitation_accept", args=[self.invitation.token])

    def _ouvrir_l_invitation(self):
        """Anonyme : l'invitation renvoie à la connexion, `next` compris."""
        reponse = self.client.get(self.url)
        self.assertEqual(reponse.status_code, 302)
        self.assertIn("next=", reponse["Location"])
        return reponse["Location"]

    def _s_inscrire(self, email="avocat@example.test"):
        connexion = self.client.get(self._ouvrir_l_invitation())
        lien = re.search(r'href="(/accounts/signup/[^"]*)"', connexion.content.decode())
        inscription = lien.group(1).replace("&amp;", "&")
        self.assertIn("next=", inscription)  # le lien « Créer un compte » garde la suite
        self.assertContains(self.client.get(inscription), self.url)
        reponse = self.client.post(inscription, {
            "username": "avocat", "email": email,
            "password1": MOT_DE_PASSE, "password2": MOT_DE_PASSE, "next": self.url,
        })
        self.assertRedirects(reponse, reverse("account_email_verification_sent"),
                             fetch_redirect_response=False)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [email])
        return re.search(r"/accounts/confirm-email/[^/\s]+/", mail.outbox[0].body).group(0)

    def _accepter(self):
        page = self.client.get(self.url)
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, self.dossier.name)
        reponse = self.client.post(self.url)
        self.assertRedirects(reponse, reverse("timeline", args=[self.dossier.pk]),
                             fetch_redirect_response=False)
        self.invitation.refresh_from_db()
        self.assertIsNotNone(self.invitation.accepted_at)
        self.assertTrue(
            DossierAccess.objects.filter(dossier=self.dossier, user__username="avocat").exists()
        )

    def test_inscription_puis_confirmation_dans_le_meme_navigateur(self):
        lien = self._s_inscrire()
        reponse = self.client.post(lien)
        self.assertRedirects(reponse, self.url, fetch_redirect_response=False)
        self.assertIn("_auth_user_id", self.client.session)
        self._accepter()

    def test_confirmation_ouverte_ailleurs_puis_connexion(self):
        lien = self._s_inscrire()
        self.client.logout()  # une autre session : le téléphone qui lit le courrier
        reponse = self.client.post(lien)
        self.assertNotIn("_auth_user_id", self.client.session)
        connexion = reponse["Location"]
        self.assertTrue(connexion.startswith(reverse("account_login") + "?next="))
        reponse = self.client.post(connexion, {"login": "avocat", "password": MOT_DE_PASSE})
        self.assertRedirects(reponse, self.url, fetch_redirect_response=False)
        self._accepter()

    def test_confirmation_ailleurs_sans_invitation_utilisable(self):
        """Une invitation révoquée n'est pas proposée : la connexion ordinaire."""
        lien = self._s_inscrire()
        self.invitation.revoked_at = timezone.now()
        self.invitation.save()
        self.client.logout()
        reponse = self.client.post(lien)
        self.assertEqual(reponse["Location"], reverse("account_login"))

    def test_adresse_d_inscription_differente_de_l_adresse_invitee(self):
        """Comportement inchangé : le jeton vaut autorisation, quelle que soit
        l'adresse du compte qui l'utilise."""
        lien = self._s_inscrire(email="autre@example.test")
        reponse = self.client.post(lien)
        self.assertRedirects(reponse, self.url, fetch_redirect_response=False)
        self._accepter()

    def test_adresse_differente_confirmee_ailleurs_ne_devine_pas_l_invitation(self):
        """L'invitation n'est retrouvée que par l'adresse prouvée."""
        lien = self._s_inscrire(email="autre@example.test")
        self.client.logout()
        reponse = self.client.post(lien)
        self.assertEqual(reponse["Location"], reverse("account_login"))

    def test_compte_existant_non_connecte(self):
        user = make_user("avocat", password=MOT_DE_PASSE)
        verifier_adresse(user)
        connexion = self._ouvrir_l_invitation()
        reponse = self.client.post(connexion, {"login": "avocat", "password": MOT_DE_PASSE})
        self.assertRedirects(reponse, self.url, fetch_redirect_response=False)
        self._accepter()

    def test_compte_existant_non_verifie(self):
        """Connexion refusée, lien renvoyé ; le clic ramène à l'invitation."""
        make_user("avocat", password=MOT_DE_PASSE)
        connexion = self._ouvrir_l_invitation()
        reponse = self.client.post(connexion, {"login": "avocat", "password": MOT_DE_PASSE})
        self.assertNotIn("_auth_user_id", self.client.session)
        lien = re.search(r"/accounts/confirm-email/[^/\s]+/", mail.outbox[0].body).group(0)
        reponse = self.client.post(lien)
        self.assertRedirects(reponse, self.url, fetch_redirect_response=False)
        self._accepter()

    def test_un_next_externe_n_est_ni_transmis_ni_suivi(self):
        connexion = reverse("account_login") + "?next=https://evil.example/"
        page = self.client.get(connexion)
        self.assertContains(page, 'href="/accounts/signup/"')  # le lien n'emporte pas l'URL
        verifier_adresse(make_user("avocat", password=MOT_DE_PASSE))
        reponse = self.client.post(connexion, {
            "login": "avocat", "password": MOT_DE_PASSE, "next": "https://evil.example/",
        })
        self.assertEqual(reponse["Location"], "/")
