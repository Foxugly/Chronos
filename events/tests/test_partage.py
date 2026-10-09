"""Partage d'un dossier avec un compte existant.

C'est le premier des trois mécanismes prévus. Il porte le modèle de droits
que les deux autres — invitation par e-mail et lien secret — réutiliseront.

La règle structurante : **seul le propriétaire partage**. Un contributeur en
écriture peut tout modifier dans le dossier, mais ne décide pas qui y entre.
Sans cela, un unique compte partagé suffirait à élargir l'accès de proche en
proche sans que le propriétaire en sache rien.
"""

from django.test import TestCase
from django.urls import reverse

from events.models import DossierAccess, Event
from events.tests.factories import grant, make_dossier, make_user


class WhoMaySeeTheSharingScreenTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.writer = make_user("writer")
        self.reader = make_user("reader")
        self.stranger = make_user("stranger")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        grant(self.dossier, self.writer, DossierAccess.Role.WRITE)
        grant(self.dossier, self.reader, DossierAccess.Role.READ)

    def _url(self):
        return reverse("dossier_share", args=[self.dossier.pk])

    def test_the_owner_reaches_it(self):
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self._url()).status_code, 200)

    def test_a_writer_does_not(self):
        """Le point le plus important de cet écran."""
        self.client.force_login(self.writer)
        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_a_reader_does_not(self):
        self.client.force_login(self.reader)
        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_a_stranger_does_not(self):
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self._url()).status_code, 404)

    def test_an_anonymous_visitor_is_sent_to_the_login_page(self):
        reponse = self.client.get(self._url())
        self.assertEqual(reponse.status_code, 302)
        self.assertIn("/accounts/login/", reponse["Location"])

    def test_the_timeline_offers_the_link_only_to_the_owner(self):
        chronologie = reverse("timeline", args=[self.dossier.pk])
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(chronologie), self._url())
        self.client.force_login(self.writer)
        self.assertNotContains(self.client.get(chronologie), self._url())


class GrantingAccessTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.invite = make_user("invite")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        self.event = Event.objects.create(
            dossier=self.dossier, title="Devis signe", date="2026-06-02"
        )
        self.client.force_login(self.owner)

    def _accorder(self, role):
        return self.client.post(
            reverse("dossier_share", args=[self.dossier.pk]),
            {"user": self.invite.pk, "role": role},
        )

    def test_granting_read_creates_the_access_and_records_its_origin(self):
        reponse = self._accorder(DossierAccess.Role.READ)
        self.assertEqual(reponse.status_code, 302)

        acces = DossierAccess.objects.get(dossier=self.dossier, user=self.invite)
        self.assertEqual(acces.role, DossierAccess.Role.READ)
        self.assertEqual(acces.origin, DossierAccess.Origin.DIRECT)
        self.assertEqual(acces.granted_by, self.owner)

    def test_a_granted_reader_really_gains_access(self):
        """L'écran ne sert à rien s'il n'ouvre pas réellement la porte."""
        chronologie = reverse("timeline", args=[self.dossier.pk])
        self.client.force_login(self.invite)
        self.assertEqual(self.client.get(chronologie).status_code, 404)

        self.client.force_login(self.owner)
        self._accorder(DossierAccess.Role.READ)

        self.client.force_login(self.invite)
        self.assertEqual(self.client.get(chronologie).status_code, 200)

    def test_a_reader_still_cannot_write(self):
        self._accorder(DossierAccess.Role.READ)
        self.client.force_login(self.invite)
        self.assertEqual(
            self.client.get(
                reverse("event_edit", args=[self.event.pk])
            ).status_code,
            404,
        )

    def test_a_granted_writer_can_write(self):
        self._accorder(DossierAccess.Role.WRITE)
        self.client.force_login(self.invite)
        self.assertEqual(
            self.client.get(
                reverse("event_edit", args=[self.event.pk])
            ).status_code,
            200,
        )

    def test_a_granted_writer_still_cannot_share(self):
        """Le droit d'écriture ne se propage pas en droit de partage."""
        self._accorder(DossierAccess.Role.WRITE)
        self.client.force_login(self.invite)
        self.assertEqual(
            self.client.get(
                reverse("dossier_share", args=[self.dossier.pk])
            ).status_code,
            404,
        )

    def test_the_picker_hides_accounts_that_already_have_access(self):
        """Proposer un compte déjà autorisé mènerait à une violation de la
        contrainte d'unicité, c'est-à-dire à une erreur serveur pour un geste
        légitime."""
        self._accorder(DossierAccess.Role.READ)
        page = self.client.get(
            reverse("dossier_share", args=[self.dossier.pk])
        )
        choix = page.context["form"].fields["user"].queryset
        self.assertNotIn(self.invite, choix)

    def test_the_picker_hides_the_owner(self):
        page = self.client.get(reverse("dossier_share", args=[self.dossier.pk]))
        self.assertNotIn(self.owner, page.context["form"].fields["user"].queryset)

    def test_a_writer_cannot_grant_through_a_direct_post(self):
        """L'écran est caché à un contributeur ; la vue doit l'être aussi."""
        writer = make_user("writer")
        grant(self.dossier, writer, DossierAccess.Role.WRITE)
        complice = make_user("complice")

        self.client.force_login(writer)
        reponse = self.client.post(
            reverse("dossier_share", args=[self.dossier.pk]),
            {"user": complice.pk, "role": DossierAccess.Role.WRITE},
        )
        self.assertEqual(reponse.status_code, 404)
        self.assertFalse(
            DossierAccess.objects.filter(dossier=self.dossier, user=complice).exists()
        )


class RevokingAccessTests(TestCase):
    def setUp(self):
        self.owner = make_user("owner")
        self.invite = make_user("invite")
        self.dossier = make_dossier(self.owner, name="Dossier adverse")
        self.acces = grant(self.dossier, self.invite, DossierAccess.Role.WRITE)

    def _url(self):
        return reverse(
            "dossier_access_revoke", args=[self.dossier.pk, self.acces.pk]
        )

    def test_the_owner_can_take_an_access_back(self):
        self.client.force_login(self.owner)
        reponse = self.client.post(self._url())
        self.assertEqual(reponse.status_code, 302)
        self.assertFalse(DossierAccess.objects.filter(pk=self.acces.pk).exists())

    def test_the_revoked_account_really_loses_access(self):
        self.client.force_login(self.owner)
        self.client.post(self._url())

        self.client.force_login(self.invite)
        self.assertEqual(
            self.client.get(
                reverse("timeline", args=[self.dossier.pk])
            ).status_code,
            404,
        )

    def test_a_get_is_refused(self):
        """Sans cela, un lien, une image ou un préchargement suffirait à
        révoquer un accès."""
        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(self._url()).status_code, 405)
        self.assertTrue(DossierAccess.objects.filter(pk=self.acces.pk).exists())

    def test_a_writer_cannot_revoke(self):
        self.client.force_login(self.invite)
        reponse = self.client.post(self._url())
        self.assertEqual(reponse.status_code, 404)
        self.assertTrue(DossierAccess.objects.filter(pk=self.acces.pk).exists())

    def test_an_access_of_another_dossier_cannot_be_revoked_from_here(self):
        """L'identifiant d'accès vient de l'URL : rien ne garantit qu'il
        appartienne au dossier désigné."""
        autre = make_dossier(self.owner, name="Autre affaire")
        acces_ailleurs = grant(autre, self.invite, DossierAccess.Role.READ)

        self.client.force_login(self.owner)
        reponse = self.client.post(
            reverse(
                "dossier_access_revoke", args=[self.dossier.pk, acces_ailleurs.pk]
            )
        )
        self.assertEqual(reponse.status_code, 404)
        self.assertTrue(DossierAccess.objects.filter(pk=acces_ailleurs.pk).exists())
