"""Invitation à un dossier, par courriel.

Deuxième des trois mécanismes de partage. Le jeton vaut autorisation : il
désigne un dossier, un rôle et une adresse. Trois garde-fous l'encadrent,
parce qu'un courriel se retransfère : expiration, usage unique, révocation.

L'adresse n'est volontairement pas vérifiée à l'acceptation — qui détient le
lien peut s'en servir, avec le compte sous lequel il est connecté. C'est la
nature d'un lien transmissible, et la raison pour laquelle l'octroi direct
reste préférable pour les personnes dont on connaît déjà le compte.
"""

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import DossierAccess, Event, Invitation
from events.tests.factories import grant, make_dossier, make_user


class IssuingAnInvitationTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.owner.email = "owner@example.test"
        self.owner.save()
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        self.client.force_login(self.owner)

    def _inviter(self, email="avocat@example.test", role=DossierAccess.Role.READ):
        return self.client.post(
            reverse("dossier_invite", args=[self.dossier.pk]),
            {"email": email, "role": role},
        )

    def test_an_invitation_is_created_and_mailed(self):
        reponse = self._inviter()
        self.assertEqual(reponse.status_code, 302)

        invitation = Invitation.objects.get()
        self.assertEqual(invitation.email, "avocat@example.test")
        self.assertEqual(invitation.created_by, self.owner)
        self.assertTrue(invitation.is_usable)

        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(self.dossier.name, mail.outbox[0].subject)
        self.assertIn(invitation.token, mail.outbox[0].body)

    def test_the_token_is_long_enough_not_to_be_guessed(self):
        """Le jeton vaut un accès à des pièces de dossier."""
        self._inviter()
        self.assertGreaterEqual(len(Invitation.objects.get().token), 32)

    def test_two_invitations_never_share_a_token(self):
        self._inviter("un@example.test")
        self._inviter("deux@example.test")
        jetons = set(Invitation.objects.values_list("token", flat=True))
        self.assertEqual(len(jetons), 2)

    def test_an_invitation_expires(self):
        self._inviter()
        invitation = Invitation.objects.get()
        self.assertGreater(invitation.expires_at, timezone.now())

    def test_inviting_an_address_that_already_has_access_is_refused(self):
        """Une invitation sans effet vaut mieux dite que partie."""
        deja = make_user("deja")
        deja.email = "deja@example.test"
        deja.save()
        grant(self.dossier, deja, DossierAccess.Role.READ)

        reponse = self._inviter("deja@example.test")
        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(Invitation.objects.exists())
        self.assertEqual(len(mail.outbox), 0)

    def test_inviting_the_same_address_twice_is_refused(self):
        self._inviter()
        reponse = self._inviter()
        self.assertEqual(reponse.status_code, 400)
        self.assertEqual(Invitation.objects.count(), 1)

    def test_a_writer_cannot_invite(self):
        """Le droit d'écriture ne se propage pas en droit de partage."""
        writer = make_user("writer")
        grant(self.dossier, writer, DossierAccess.Role.WRITE)
        self.client.force_login(writer)

        reponse = self._inviter("complice@example.test")
        self.assertEqual(reponse.status_code, 404)
        self.assertFalse(Invitation.objects.exists())


class AcceptingAnInvitationTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        self.event = Event.objects.create(
            dossier=self.dossier, title="Devis signe", date="2026-06-02"
        )
        self.invitation = Invitation.objects.create(
            dossier=self.dossier,
            email="avocat@example.test",
            role=DossierAccess.Role.READ,
            created_by=self.owner,
        )
        self.invite = make_user("avocat")

    def _url(self):
        return reverse("invitation_accept", args=[self.invitation.token])

    def test_a_get_does_not_consume_the_invitation(self):
        """Un aperçu de lien, un antivirus de messagerie ou un préchargement
        ouvrent les liens sans que personne ne l'ait voulu."""
        self.client.force_login(self.invite)
        reponse = self.client.get(self._url())
        self.assertEqual(reponse.status_code, 200)
        self.invitation.refresh_from_db()
        self.assertTrue(self.invitation.is_usable)
        self.assertFalse(DossierAccess.objects.exists())

    def test_accepting_creates_the_access_with_its_origin(self):
        self.client.force_login(self.invite)
        reponse = self.client.post(self._url())
        self.assertEqual(reponse.status_code, 302)

        acces = DossierAccess.objects.get(dossier=self.dossier, user=self.invite)
        self.assertEqual(acces.role, DossierAccess.Role.READ)
        self.assertEqual(acces.origin, DossierAccess.Origin.INVITATION)
        self.assertEqual(acces.granted_by, self.owner)

    def test_the_invited_account_really_gains_access(self):
        self.client.force_login(self.invite)
        self.client.post(self._url())
        self.assertEqual(
            self.client.get(
                reverse("timeline", args=[self.dossier.pk])
            ).status_code,
            200,
        )

    def test_an_invited_reader_still_cannot_write_or_share(self):
        self.client.force_login(self.invite)
        self.client.post(self._url())
        self.assertEqual(
            self.client.get(
                reverse("event_edit", args=[self.event.pk])
            ).status_code,
            404,
        )
        self.assertEqual(
            self.client.get(
                reverse("dossier_share", args=[self.dossier.pk])
            ).status_code,
            404,
        )

    def test_an_invitation_serves_only_once(self):
        self.client.force_login(self.invite)
        self.client.post(self._url())

        autre = make_user("curieux")
        self.client.force_login(autre)
        self.client.post(self._url())

        self.assertFalse(
            DossierAccess.objects.filter(dossier=self.dossier, user=autre).exists()
        )

    def test_an_expired_invitation_grants_nothing(self):
        self.invitation.expires_at = timezone.now() - timezone.timedelta(days=1)
        self.invitation.save(update_fields=["expires_at"])

        self.client.force_login(self.invite)
        reponse = self.client.post(self._url())
        self.assertEqual(reponse.status_code, 200)
        self.assertFalse(DossierAccess.objects.exists())

    def test_a_revoked_invitation_grants_nothing(self):
        self.invitation.revoked_at = timezone.now()
        self.invitation.save(update_fields=["revoked_at"])

        self.client.force_login(self.invite)
        self.client.post(self._url())
        self.assertFalse(DossierAccess.objects.exists())

    def test_an_unknown_token_is_a_404(self):
        self.client.force_login(self.invite)
        reponse = self.client.get(
            reverse("invitation_accept", args=["jeton-qui-n-existe-pas"])
        )
        self.assertEqual(reponse.status_code, 404)

    def test_an_anonymous_visitor_is_sent_to_login_first(self):
        """Le lien n'est pas une porte : il faut un compte pour l'utiliser."""
        reponse = self.client.get(self._url())
        self.assertEqual(reponse.status_code, 302)
        self.assertIn("/accounts/login/", reponse["Location"])
        self.assertFalse(DossierAccess.objects.exists())

    def test_accepting_twice_with_the_same_account_changes_nothing(self):
        self.client.force_login(self.invite)
        self.client.post(self._url())
        self.client.post(self._url())
        self.assertEqual(
            DossierAccess.objects.filter(
                dossier=self.dossier, user=self.invite
            ).count(),
            1,
        )


class RevokingAnInvitationTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        self.invitation = Invitation.objects.create(
            dossier=self.dossier,
            email="avocat@example.test",
            role=DossierAccess.Role.READ,
            created_by=self.owner,
        )

    def _url(self):
        return reverse(
            "invitation_revoke", args=[self.dossier.pk, self.invitation.pk]
        )

    def test_the_owner_can_revoke_before_acceptance(self):
        self.client.force_login(self.owner)
        reponse = self.client.post(self._url())
        self.assertEqual(reponse.status_code, 302)
        self.invitation.refresh_from_db()
        self.assertFalse(self.invitation.is_usable)

    def test_a_revoked_link_no_longer_works(self):
        self.client.force_login(self.owner)
        self.client.post(self._url())

        invite = make_user("avocat")
        self.client.force_login(invite)
        self.client.post(
            reverse("invitation_accept", args=[self.invitation.token])
        )
        self.assertFalse(DossierAccess.objects.exists())

    def test_a_get_is_refused(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self._url()).status_code, 405)

    def test_a_writer_cannot_revoke(self):
        writer = make_user("writer")
        grant(self.dossier, writer, DossierAccess.Role.WRITE)
        self.client.force_login(writer)
        self.assertEqual(self.client.post(self._url()).status_code, 404)

    def test_an_invitation_of_another_dossier_cannot_be_revoked_from_here(self):
        autre = make_dossier(self.owner, name="Autre affaire")
        ailleurs = Invitation.objects.create(
            dossier=autre,
            email="x@example.test",
            role=DossierAccess.Role.READ,
            created_by=self.owner,
        )
        self.client.force_login(self.owner)
        reponse = self.client.post(
            reverse("invitation_revoke", args=[self.dossier.pk, ailleurs.pk])
        )
        self.assertEqual(reponse.status_code, 404)
        ailleurs.refresh_from_db()
        self.assertTrue(ailleurs.is_usable)


class LangueDeLInvitationTests(TestCase):
    """Le courriel part dans la langue de la personne invitée, choisie par le propriétaire.

    Avant : dans la langue de l'interface du propriétaire au moment de l'envoi —
    une avocate néerlandophone recevait un courriel en français.
    """

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        self.client.force_login(self.owner)

    def _inviter(self, **champs):
        return self.client.post(
            reverse("dossier_invite", args=[self.dossier.pk]),
            {"email": "advocaat@example.test", "role": DossierAccess.Role.READ, **champs},
        )

    def test_le_courriel_part_dans_la_langue_choisie(self):
        reponse = self._inviter(langue="nl")
        self.assertEqual(reponse.status_code, 302)
        self.assertEqual(Invitation.objects.get().langue, "nl")
        self.assertEqual(mail.outbox[0].subject, "Toegang tot het dossier ‘Dossier adverse’")
        self.assertNotIn("Droit accordé", mail.outbox[0].body)

    def test_le_proprietaire_garde_sa_langue(self):
        """Le message de confirmation reste dans la langue de l'interface."""
        reponse = self._inviter(langue="en-gb")
        self.assertEqual(mail.outbox[0].subject, "Access to the case file “Dossier adverse”")
        confirmation = [str(m) for m in reponse.wsgi_request._messages]
        self.assertTrue(confirmation[0].startswith("Invitation envoyée"))

    def test_sans_langue_postee_celle_de_l_interface(self):
        """Un envoi direct sans le champ garde l'ancien comportement."""
        self.client.cookies["django_language"] = "nl"
        self._inviter()
        self.assertEqual(Invitation.objects.get().langue, "nl")
        self.assertIn("Toegang tot het dossier", mail.outbox[0].subject)

    def test_le_formulaire_propose_la_langue_de_l_interface(self):
        self.client.cookies["django_language"] = "en-gb"
        page = self.client.get(reverse("dossier_share", args=[self.dossier.pk]))
        self.assertContains(page, '<option value="en-gb" selected>', html=False)

    def test_une_langue_inconnue_est_refusee(self):
        reponse = self._inviter(langue="de")
        self.assertEqual(reponse.status_code, 400)
        self.assertFalse(Invitation.objects.exists())
