"""Audit du partage, 2026-11-17 : ce qui a été corrigé, point par point."""

from unittest import mock

from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from events.models import DossierAccess, Event, Invitation, Journal, ShareLink
from events.journal import consigner
from admin_2fa.testing import force_admin_login
from events.tests.factories import grant, make_dossier, make_user, verifier_adresse


class InvitationAccepteeUneSeuleFoisTests(TestCase):
    """Point 1 : accepter deux fois ne crée ni deux accès ni d'erreur 500."""

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.invitation = Invitation.objects.create(
            dossier=self.dossier, email="avocat@example.test",
            role=DossierAccess.Role.READ, created_by=self.owner)
        self.url = reverse("invitation_accept", args=[self.invitation.token])
        self.avocat = make_user("avocat")
        verifier_adresse(self.avocat)

    def test_double_clic_un_seul_acces_pas_de_500(self):
        self.client.force_login(self.avocat)
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.url).status_code, 200)
        self.assertEqual(DossierAccess.objects.filter(user=self.avocat).count(), 1)
        self.assertEqual(Journal.objects.filter(action=Journal.Action.INVITATION_ACCEPTEE).count(), 1)

    def test_un_acces_cree_entre_temps_n_est_pas_une_erreur(self):
        """La course : l'accès existe déjà quand on crée le sien (contrainte
        d'unicité). Aucun 500, l'invitation reste en attente."""
        self.client.force_login(self.avocat)
        with mock.patch("events.views.partage._deja_autorise", return_value=False):
            grant(self.dossier, self.avocat, DossierAccess.Role.READ)
            reponse = self.client.post(self.url)
        self.assertEqual(reponse.status_code, 200)
        self.invitation.refresh_from_db()
        self.assertIsNone(self.invitation.accepted_at)
        self.assertFalse(Journal.objects.filter(action=Journal.Action.INVITATION_ACCEPTEE).exists())


class InvitationLieeALAdresseTests(TestCase):
    """Point 6 : l'invitation vaut pour l'adresse invitée, vérifiée."""

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.invitation = Invitation.objects.create(
            dossier=self.dossier, email="Avocat@Example.test",
            role=DossierAccess.Role.WRITE, created_by=self.owner)
        self.url = reverse("invitation_accept", args=[self.invitation.token])

    def test_un_autre_compte_ne_recoit_rien(self):
        intrus = make_user("intrus")
        verifier_adresse(intrus)
        self.client.force_login(intrus)
        page = self.client.get(self.url)
        self.assertContains(page, "Avocat@Example.test")
        self.assertContains(page, "Changer de compte")
        self.assertNotContains(page, "Accepter l&#x27;invitation")
        self.client.post(self.url)
        self.assertFalse(DossierAccess.objects.exists())

    def test_l_adresse_doit_etre_verifiee(self):
        avocat = make_user("avocat")  # avocat@example.test, non vérifiée
        self.client.force_login(avocat)
        self.client.post(self.url)
        self.assertFalse(DossierAccess.objects.exists())

    def test_la_bonne_adresse_verifiee_sans_tenir_compte_de_la_casse(self):
        avocat = make_user("avocat")
        verifier_adresse(avocat)
        self.client.force_login(avocat)
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.assertTrue(DossierAccess.objects.filter(user=avocat, role=DossierAccess.Role.WRITE).exists())


class NomDuLienDansLeJournalTests(TestCase):
    """Points 3 et 4 : le nom du lien est figé, et caché aux collaborateurs."""

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.lien = ShareLink.objects.create(dossier=self.dossier, label="Maître Dupont",
                                             role=DossierAccess.Role.WRITE, created_by=self.owner)
        evenement = Event.objects.create(dossier=self.dossier, title="Devis", date="2026-06-02")
        consigner(self.dossier, Journal.Action.EVENEMENT_CREE, lien=self.lien,
                  objet=evenement.title, evenement=evenement)
        self.journal = reverse("dossier_journal", args=[self.dossier.pk])

    def test_renommer_le_lien_ne_change_pas_l_auteur_des_entrees_passees(self):
        self.lien.label = "Autre nom"
        self.lien.save()
        self.client.force_login(self.owner)
        page = self.client.get(self.journal)
        self.assertContains(page, "par le lien de partage « Maître Dupont »")
        self.assertNotContains(page, "Autre nom")

    def test_une_ancienne_entree_sans_nom_fige_prend_le_nom_actuel(self):
        # Écrite avant ce correctif : sans `lien_nom` dans ses détails.
        ancienne = Journal(dossier=self.dossier, action=Journal.Action.EVENEMENT_MODIFIE,
                           lien=self.lien, objet="Devis", details={})
        ancienne.save()
        self.client.force_login(self.owner)
        self.assertContains(self.client.get(self.journal), "par le lien de partage « Maître Dupont »", count=2)

    def test_un_collaborateur_ne_voit_pas_a_qui_le_lien_a_ete_donne(self):
        lecteur = make_user("lecteur")
        grant(self.dossier, lecteur, DossierAccess.Role.READ)
        self.client.force_login(lecteur)
        page = self.client.get(self.journal)
        self.assertContains(page, "par un lien de partage")
        self.assertNotContains(page, "Maître Dupont")


class InvitationToutOuRienTests(TestCase):
    """Point 5 : si le courriel ne part pas, l'invitation n'existe pas."""

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.client.force_login(self.owner)
        self.url = reverse("dossier_invite", args=[self.dossier.pk])

    def test_echec_d_envoi_rien_n_est_cree_ni_journalise(self):
        with mock.patch("events.views.partage.send_mail", side_effect=OSError("Graph indisponible")), \
                self.assertLogs("events.views.partage", level="ERROR"):
            reponse = self.client.post(self.url, {"email": "a@example.test", "role": "read"}, follow=True)
        self.assertEqual(reponse.status_code, 200)
        self.assertContains(reponse, "n&#x27;a pas pu partir")
        self.assertFalse(Invitation.objects.exists())
        self.assertFalse(Journal.objects.exists())

    def test_on_peut_reessayer_apres_un_echec(self):
        with mock.patch("events.views.partage.send_mail", side_effect=OSError), \
                self.assertLogs("events.views.partage", level="ERROR"):
            self.client.post(self.url, {"email": "a@example.test", "role": "read"})
        self.client.post(self.url, {"email": "a@example.test", "role": "read"})
        self.assertEqual(Invitation.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)


class AdminDesDossiersReserveAuxSuperutilisateursTests(TestCase):
    """Point 7 : accès et propriétaire d'un dossier, superutilisateur seul."""

    def setUp(self):
        from django.contrib.auth.models import Permission

        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.staff = make_user("equipe", is_staff=True)
        self.staff.user_permissions.set(Permission.objects.filter(
            content_type__app_label="events", content_type__model__in=["dossier", "dossieraccess"]))
        force_admin_login(self.client, self.staff)  # l'admin exige la 2FA (admin_2fa/)

    def test_un_compte_d_equipe_n_y_entre_pas(self):
        for url in ("/admin/events/dossier/", f"/admin/events/dossier/{self.dossier.pk}/change/",
                    "/admin/events/dossieraccess/", "/admin/events/dossieraccess/add/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)

    def test_le_proprietaire_ne_recoit_pas_de_ligne_d_acces(self):
        """Invariant 1, vérifié par le modèle (et donc par l'admin)."""
        from django.core.exceptions import ValidationError

        acces = DossierAccess(dossier=self.dossier, user=self.owner, role=DossierAccess.Role.READ)
        with self.assertRaises(ValidationError):
            acces.full_clean()


class ActionsDePartageAtomiquesTests(TestCase):
    """Point 7 : un geste de partage et sa trace forment une seule transaction."""

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.client.force_login(self.owner)

    def test_si_le_journal_echoue_le_lien_n_est_pas_cree(self):
        with mock.patch("events.views.partage.consigner", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse("share_link_create", args=[self.dossier.pk]),
                                 {"label": "X", "role": "read", "duree": "7"})
        self.assertFalse(ShareLink.objects.exists())

    def test_si_le_journal_echoue_l_acces_n_est_pas_retire(self):
        lecteur = make_user("lecteur")
        acces = grant(self.dossier, lecteur, DossierAccess.Role.READ)
        with mock.patch("events.views.partage.consigner", side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):
                self.client.post(reverse("dossier_access_revoke", args=[self.dossier.pk, acces.pk]))
        self.assertTrue(DossierAccess.objects.filter(pk=acces.pk).exists())


class PasDeCacheTests(TestCase):
    """Point 8 : les pages qui portent un jeton ne restent pas en cache."""

    def setUp(self):
        self.owner = make_user("owner")
        self.dossier = make_dossier(self.owner)
        self.lien = ShareLink.objects.create(dossier=self.dossier, created_by=self.owner,
                                             role=DossierAccess.Role.READ)
        self.invitation = Invitation.objects.create(
            dossier=self.dossier, email="a@example.test", role="read", created_by=self.owner,
            expires_at=timezone.now() + timezone.timedelta(days=3))

    def assertPasEnCache(self, reponse):
        self.assertEqual(reponse.status_code, 200)
        self.assertIn("no-store", reponse.get("Cache-Control", ""))

    def test_les_vues_d_un_lien(self):
        self.assertPasEnCache(self.client.get(reverse("share_timeline", args=[self.lien.token])))

    def test_les_pages_du_proprietaire_et_l_invitation(self):
        self.client.force_login(self.owner)
        for url in (reverse("dossier_share", args=[self.dossier.pk]),
                    reverse("share_link_detail", args=[self.dossier.pk, self.lien.pk]),
                    reverse("share_link_edit", args=[self.dossier.pk, self.lien.pk]),
                    reverse("invitation_accept", args=[self.invitation.token])):
            with self.subTest(url=url):
                self.assertPasEnCache(self.client.get(url))
