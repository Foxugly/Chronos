"""Un changement de propriétaire, dans l'admin, annule les invitations encore
en suspens : elles avaient été émises au nom de l'ancien propriétaire."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from events.models import Invitation, Journal
from events.tests.factories import make_dossier, make_user


class InvitationsAuChangementDeProprietaireTests(TestCase):
    def setUp(self):
        self.admin = make_user("admin", is_staff=True, is_superuser=True)
        self.ancien = make_user("ancien")
        self.nouveau = make_user("nouveau")
        self.dossier = make_dossier(self.ancien, name="Litige")
        maintenant = timezone.now()

        def invitation(email, **champs):
            return Invitation.objects.create(dossier=self.dossier, email=email, role="read",
                                             created_by=self.ancien, **champs)

        self.en_attente = invitation("attente@example.test")
        self.expiree = invitation("expiree@example.test", expires_at=maintenant - timedelta(days=1))
        self.acceptee = invitation("acceptee@example.test", accepted_at=maintenant,
                                   accepted_by=self.nouveau)
        self.annulee = invitation("annulee@example.test", revoked_at=maintenant - timedelta(days=2))
        self.client.force_login(self.admin)

    def enregistrer(self, owner):
        return self.client.post(f"/admin/events/dossier/{self.dossier.pk}/change/", {
            "name": "Litige", "owner": owner.pk,
            "accesses-TOTAL_FORMS": "0", "accesses-INITIAL_FORMS": "0",
            "accesses-MIN_NUM_FORMS": "0", "accesses-MAX_NUM_FORMS": "1000",
        })

    def recharger(self):
        for invitation in (self.en_attente, self.expiree, self.acceptee, self.annulee):
            invitation.refresh_from_db()

    def test_les_invitations_en_suspens_sont_annulees_et_journalisees(self):
        annulee_avant = self.annulee.revoked_at
        self.assertEqual(self.enregistrer(self.nouveau).status_code, 302)
        self.recharger()
        self.assertIsNotNone(self.en_attente.revoked_at)
        self.assertIsNotNone(self.expiree.revoked_at)
        self.assertIsNone(self.acceptee.revoked_at)
        self.assertEqual(self.annulee.revoked_at, annulee_avant)

        annulations = Journal.objects.filter(action=Journal.Action.INVITATION_ANNULEE)
        self.assertEqual(sorted(annulations.values_list("objet", flat=True)),
                         ["attente@example.test", "expiree@example.test"])
        for entree in annulations:
            self.assertTrue(entree.via_admin)
            self.assertEqual(entree.details["motif"], "changement_de_proprietaire")
        self.assertTrue(Journal.objects.filter(action=Journal.Action.DOSSIER_MODIFIE).exists())

    def test_sans_changement_de_proprietaire_rien_n_est_annule(self):
        self.assertEqual(self.enregistrer(self.ancien).status_code, 302)
        self.recharger()
        self.assertIsNone(self.en_attente.revoked_at)
        self.assertIsNone(self.expiree.revoked_at)
        self.assertFalse(Journal.objects.exists())
