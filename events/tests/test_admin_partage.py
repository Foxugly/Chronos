"""Les liens de partage et les invitations se consultent dans l'admin.

Ils n'y figuraient pas : un lien expiré ou révoqué n'était visible nulle part
ailleurs que sur la page de partage de son dossier, propriétaire seul.
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from events.models import DossierAccess, Invitation, Journal, ShareLink
from events.tests.factories import make_dossier, make_user

LISTE_LIENS = "/admin/events/sharelink/"
LISTE_INVITATIONS = "/admin/events/invitation/"


class AdminDesLiensTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", is_staff=True, is_superuser=True)
        self.dossier = make_dossier(self.admin, name="Litige")
        self.client.force_login(self.admin)
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

    def test_ni_creation_ni_modification_ni_suppression(self):
        self.assertEqual(self.client.get(f"{LISTE_LIENS}add/").status_code, 403)
        self.assertEqual(self.client.get(f"{LISTE_LIENS}{self.actif.pk}/delete/").status_code, 403)
        self.client.post(f"{LISTE_LIENS}{self.actif.pk}/change/", {"label": "Pirate"})
        self.actif.refresh_from_db()
        self.assertEqual(self.actif.label, "Avocat")

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
        self.client.force_login(self.admin)
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
