"""Les liens de partage et les invitations se consultent dans l'admin.

Ils n'y figuraient pas : un lien expiré ou révoqué n'était visible nulle part
ailleurs que sur la page de partage de son dossier, propriétaire seul.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from events.models import DossierAccess, Invitation, Journal, ShareLink
from admin_2fa.testing import force_admin_login
from events.tests.factories import make_dossier, make_user

LISTE_LIENS = "/admin/events/sharelink/"
LISTE_INVITATIONS = "/admin/events/invitation/"


class AdminDesLiensTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", is_staff=True, is_superuser=True)
        self.dossier = make_dossier(self.admin, name="Litige")
        force_admin_login(self.client, self.admin)  # l'admin exige la 2FA (admin_2fa/)
        maintenant = timezone.now()
        self.actif = ShareLink.objects.create(dossier=self.dossier, label="Avocat", created_by=self.admin)
        self.expire = ShareLink.objects.create(
            dossier=self.dossier, label="Expert", created_by=self.admin,
            expires_at=maintenant - timedelta(days=1))
        self.revoque = ShareLink.objects.create(
            dossier=self.dossier, label="Association", created_by=self.admin,
            revoked_at=maintenant - timedelta(hours=1))

    def test_la_liste_montre_tous_les_liens_meme_expires_et_revoques(self):
        response = self.client.get(LISTE_LIENS)
        self.assertEqual(response.status_code, 200)
        for label in ("Avocat", "Expert", "Association"):
            self.assertContains(response, label)
        for statut in ("Actif", "Expiré", "Révoqué"):
            self.assertContains(response, statut)

    def test_le_jeton_n_apparait_jamais(self):
        """Le jeton ouvre le dossier à qui le détient : l'admin n'a pas à
        l'afficher, ni dans la liste ni sur la fiche."""
        for url in (LISTE_LIENS, f"{LISTE_LIENS}{self.actif.pk}/change/"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, self.actif.token)

    def test_ni_creation_ni_suppression(self):
        """Un lien naît sur la page de partage ; il se coupe, il ne s'efface pas."""
        self.assertEqual(self.client.get(f"{LISTE_LIENS}add/").status_code, 403)
        self.assertEqual(self.client.get(f"{LISTE_LIENS}{self.actif.pk}/delete/").status_code, 403)

    def _modifier(self, lien, **valeurs):
        donnees = {
            "label": lien.label,
            "role": lien.role,
            "expires_at_0": "",
            "expires_at_1": "",
        }
        donnees.update(valeurs)
        return self.client.post(f"{LISTE_LIENS}{lien.pk}/change/", donnees)

    def test_modifier_destinataire_droit_et_expiration_et_le_journaliser(self):
        response = self._modifier(
            self.expire, label="Expert judiciaire", role=DossierAccess.Role.WRITE,
            expires_at_0="2099-01-31", expires_at_1="12:00:00")
        self.assertEqual(response.status_code, 302)
        self.expire.refresh_from_db()
        self.assertEqual(self.expire.label, "Expert judiciaire")
        self.assertEqual(self.expire.role, DossierAccess.Role.WRITE)
        self.assertTrue(self.expire.is_active)  # prolongé : il fonctionne de nouveau

        entree = Journal.objects.get(action=Journal.Action.LIEN_MODIFIE)
        self.assertTrue(entree.via_admin)
        self.assertEqual(entree.lien, self.expire)
        self.assertEqual(entree.details["modele"], "lien")
        self.assertEqual(set(entree.details["changements"]), {"label", "role", "expires_at"})
        self.assertEqual(entree.details["changements"]["label"], ["Expert", "Expert judiciaire"])

    def test_vider_l_expiration_rend_le_lien_illimite(self):
        self._modifier(self.expire)
        self.expire.refresh_from_db()
        self.assertIsNone(self.expire.expires_at)
        self.assertTrue(self.expire.is_active)

    def test_enregistrer_sans_rien_changer_ne_journalise_rien(self):
        self._modifier(self.actif)
        self.assertFalse(Journal.objects.exists())

    def test_une_expiration_a_la_microseconde_ne_cree_pas_de_faux_changement(self):
        """La page de partage calcule l'expiration (`now() + durée`) ; le
        formulaire de l'admin l'affiche à la seconde. Réenregistrer sans rien
        toucher ne doit pas inscrire « expiration modifiée » au journal."""
        moment = timezone.localtime(timezone.now() + timedelta(days=30)).replace(microsecond=123456)
        ShareLink.objects.filter(pk=self.actif.pk).update(expires_at=moment)
        self._modifier(self.actif, expires_at_0=moment.strftime("%Y-%m-%d"),
                       expires_at_1=moment.strftime("%H:%M:%S"))
        self.assertFalse(Journal.objects.exists())

    def test_jeton_compteur_et_dates_ne_se_modifient_pas(self):
        jeton = self.actif.token
        self._modifier(self.actif, token="x" * 40, use_count="999", revoked_at_0="2026-01-01",
                       revoked_at_1="10:00:00", created_by="")
        self.actif.refresh_from_db()
        self.assertEqual(self.actif.token, jeton)
        self.assertEqual(self.actif.use_count, 0)
        self.assertIsNone(self.actif.revoked_at)
        self.assertEqual(self.actif.created_by, self.admin)

    def test_la_modification_se_lit_dans_le_journal_du_dossier(self):
        self._modifier(self.actif, label="Avocate")
        response = self.client.get(f"/dossiers/{self.dossier.pk}/journal/")
        self.assertContains(response, "Lien de partage modifié")
        self.assertContains(response, "Avocate")

    def test_remettre_en_service_un_lien_revoque(self):
        response = self.client.post(LISTE_LIENS, {
            "action": "remettre_en_service",
            "_selected_action": [self.revoque.pk, self.actif.pk],
        })
        self.assertEqual(response.status_code, 302)
        self.revoque.refresh_from_db()
        self.assertIsNone(self.revoque.revoked_at)
        self.assertTrue(self.revoque.is_active)
        entree = Journal.objects.get(action=Journal.Action.LIEN_MODIFIE)  # une seule : l'actif n'est pas touché
        self.assertTrue(entree.via_admin)
        self.assertEqual(entree.lien, self.revoque)
        self.assertEqual(entree.details["changements"]["revoked_at"][1], None)

    def test_revoquer_depuis_l_admin_coupe_le_lien_et_le_journalise(self):
        response = self.client.post(LISTE_LIENS, {
            "action": "revoquer",
            "_selected_action": [self.actif.pk, self.revoque.pk],
        })
        self.assertEqual(response.status_code, 302)
        self.actif.refresh_from_db()
        self.assertFalse(self.actif.is_active)
        entrees = Journal.objects.filter(action=Journal.Action.LIEN_REVOQUE)
        # Le lien déjà révoqué ne l'est pas une seconde fois.
        self.assertEqual(entrees.count(), 1)
        entree = entrees.get()
        self.assertEqual(entree.lien, self.actif)
        self.assertTrue(entree.via_admin)

    def test_une_revocation_ne_touche_pas_la_date_d_un_lien_deja_revoque(self):
        avant = self.revoque.revoked_at
        self.client.post(LISTE_LIENS, {"action": "revoquer", "_selected_action": [self.revoque.pk]})
        self.revoque.refresh_from_db()
        self.assertEqual(self.revoque.revoked_at, avant)


class AdminDesInvitationsTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", is_staff=True, is_superuser=True)
        self.dossier = make_dossier(self.admin, name="Litige")
        force_admin_login(self.client, self.admin)  # l'admin exige la 2FA (admin_2fa/)
        self.invitation = Invitation.objects.create(
            dossier=self.dossier, email="avocat@example.com", role=DossierAccess.Role.READ,
            created_by=self.admin, expires_at=timezone.now() - timedelta(days=1))

    def test_la_liste_montre_les_invitations_expirees(self):
        response = self.client.get(LISTE_INVITATIONS)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "avocat@example.com")
        self.assertContains(response, "Expirée")

    def test_le_jeton_n_apparait_jamais(self):
        for url in (LISTE_INVITATIONS, f"{LISTE_INVITATIONS}{self.invitation.pk}/change/"):
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertNotContains(response, self.invitation.token)

    def test_ni_creation_ni_suppression(self):
        self.assertEqual(self.client.get(f"{LISTE_INVITATIONS}add/").status_code, 403)
        self.assertEqual(
            self.client.get(f"{LISTE_INVITATIONS}{self.invitation.pk}/delete/").status_code, 403)


class AdminDuPartageReserveAuxSuperutilisateursTests(TestCase):
    """Un compte d'équipe (staff) muni de toutes les permissions Django n'est
    pas pour autant habilité à étendre l'accès à un dossier : seul un
    superutilisateur voit et modifie les liens et invitations ici."""

    def setUp(self):
        from django.contrib.auth.models import Permission

        proprietaire = make_user("proprio")
        self.dossier = make_dossier(proprietaire, name="Litige")
        self.lien = ShareLink.objects.create(dossier=self.dossier, label="Avocat",
                                             created_by=proprietaire)
        self.invitation = Invitation.objects.create(
            dossier=self.dossier, email="a@example.com", role=DossierAccess.Role.READ,
            created_by=proprietaire)
        self.staff = make_user("equipe", is_staff=True)
        self.staff.user_permissions.set(Permission.objects.filter(
            content_type__app_label="events",
            content_type__model__in=["sharelink", "invitation"]))
        force_admin_login(self.client, self.staff)  # l'admin exige la 2FA (admin_2fa/)

    def test_ni_liste_ni_fiche(self):
        for url in (LISTE_LIENS, f"{LISTE_LIENS}{self.lien.pk}/change/",
                    LISTE_INVITATIONS, f"{LISTE_INVITATIONS}{self.invitation.pk}/change/"):
            self.assertEqual(self.client.get(url).status_code, 403, url)

    def test_ne_peut_pas_modifier_un_lien(self):
        self.client.post(f"{LISTE_LIENS}{self.lien.pk}/change/", {
            "label": "Pirate", "role": DossierAccess.Role.WRITE,
            "expires_at_0": "", "expires_at_1": ""})
        self.lien.refresh_from_db()
        self.assertEqual((self.lien.label, self.lien.role), ("Avocat", DossierAccess.Role.READ))
        self.assertFalse(Journal.objects.exists())

    def test_absent_de_l_index_de_l_admin(self):
        response = self.client.get("/admin/")
        self.assertNotContains(response, "/admin/events/sharelink/")
        self.assertNotContains(response, "/admin/events/invitation/")
